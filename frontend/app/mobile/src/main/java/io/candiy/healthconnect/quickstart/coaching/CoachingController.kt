package io.candiy.healthconnect.quickstart.coaching

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import io.candiy.healthconnect.quickstart.FileLog
import io.candiy.healthconnect.quickstart.auth.AuthSession
import io.candiy.healthconnect.quickstart.auth.RefreshOutcome
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.UUID

/** 불러오기 상태 */
enum class Load { LOADING, LOADED, ERROR }

/**
 * 지금 보내고 있는 질문 한 건(웹 session.svelte.ts 의 CoachingRequest 를 줄인 것).
 * 끝나면 저장된 메시지(명세 6.3)를 다시 읽어 바꾸므로, 화면에는 진행 중·접수 실패일 때만 남는다.
 */
data class LiveRequest(
    val clientRequestId: String,
    /** 처음 보낼 때의 대화 ID. 같은 전송을 다시 보낼 때는 이 값을 그대로 쓴다(명세 6.1) */
    val sentConversationId: String?,
    val content: String,
    val requestId: String? = null,
    val stage: String? = null,
    val text: String = "",
    val evidence: List<Evidence> = emptyList(),
    /** 실패 문구. null 이면 진행 중 */
    val error: String? = null,
    /** 접수되지 않은 실패. 질문을 고쳐 다시 보낼 수 있다 */
    val rejected: Boolean = false,
) {
    val waiting get() = error == null
}

/**
 * AI 코치 탭의 상태와 동작. Compose 상태로 들고 있어 탭을 오가도 대화가 유지된다(액티비티 수명 동안).
 * 로그에는 질문·답변 내용과 토큰을 남기지 않는다. 단계·코드·건수만 남긴다.
 */
class CoachingController(
    private val scope: CoroutineScope,
    private val auth: AuthSession,
    private val api: CoachingApi,
    private val socket: CoachingSocket,
    /** 로그인 상태가 끝났을 때(갱신까지 거절) */
    private val onSignedOut: () -> Unit,
) {
    /**
     * 근거 창 하나가 쓸 시간대별 걸음 조회기(S15P31A403-440). 창을 열 때마다 새로 만들어 창이 닫히면 쌓아 둔 값도 버린다.
     * 실패(연결·서버 오류·로그인 만료)는 구분하지 않고 화면에서 다시 불러오게 한다
     */
    fun hourlySteps(): HourlyStepsLoader = HourlyStepsLoader { start, end -> (api.hourlySteps(start, end) as? CoachingResult.Ok)?.value }

    var conversations by mutableStateOf<List<ConversationItem>>(emptyList()); private set
    var listLoad by mutableStateOf(Load.LOADING); private set
    var nextCursor by mutableStateOf<String?>(null); private set
    var moreLoading by mutableStateOf(false); private set

    /** 연 대화. null 이면 새 대화(첫 화면) */
    var conversationId by mutableStateOf<String?>(null); private set
    var title by mutableStateOf<String?>(null); private set
    var messages by mutableStateOf<List<CoachingMessage>>(emptyList()); private set
    var historyLoad by mutableStateOf(Load.LOADED); private set
    var nextBefore by mutableStateOf<Int?>(null); private set
    var olderLoading by mutableStateOf(false); private set

    var live by mutableStateOf<LiveRequest?>(null); private set

    /** 목록·대화를 불러오지 못한 까닭(서버 오류 코드 또는 NETWORK). 화면이 연결 문제인지 알려 줄 때 쓴다 */
    var listErrorCode by mutableStateOf<String?>(null); private set
    var historyErrorCode by mutableStateOf<String?>(null); private set

    /** 답변을 기다리는 동안 입력을 잠근다(명세 7.3 대화당 진행 요청 하나) */
    val isGenerating get() = live?.waiting == true

    private var started = false
    private var openJob: Job? = null

    /** 탭을 처음 열 때 한 번 목록을 읽는다 */
    fun start() {
        if (started) return
        started = true
        refreshList()
    }

    /**
     * 인터넷 연결이 돌아왔을 때 못 불러온 것만 다시 읽는다. 탭을 연 적이 없거나 잘 불러와 있으면 아무 일도 하지 않는다.
     * 보내던 질문은 건드리지 않는다(다시 보내기는 사용자가 누른다)
     */
    fun retryFailedLoads() {
        if (!started) return
        if (listErrorCode != null) refreshList()
        if (historyLoad == Load.ERROR) loadHistory()
    }

    /** 로그아웃 때 비운다 */
    fun reset() {
        openJob?.cancel()
        started = false
        conversations = emptyList(); listLoad = Load.LOADING; nextCursor = null; listErrorCode = null; historyErrorCode = null
        conversationId = null; title = null; messages = emptyList(); historyLoad = Load.LOADED; nextBefore = null
        live = null
    }

    fun refreshList() {
        scope.launch {
            if (conversations.isEmpty()) listLoad = Load.LOADING
            when (val r = api.conversations()) {
                is CoachingResult.Ok -> { conversations = r.value.items; nextCursor = r.value.nextCursor; listLoad = Load.LOADED; listErrorCode = null }
                CoachingResult.SignedOut -> onSignedOut()
                is CoachingResult.Failed -> { if (conversations.isEmpty()) listLoad = Load.ERROR; listErrorCode = r.code; log("list_failed", r.code) }
            }
        }
    }

    fun loadMoreConversations() {
        val cursor = nextCursor ?: return
        if (moreLoading) return
        moreLoading = true
        scope.launch {
            when (val r = api.conversations(cursor)) {
                is CoachingResult.Ok -> {
                    val known = conversations.map { it.id }.toSet()
                    conversations = conversations + r.value.items.filterNot { it.id in known }
                    nextCursor = r.value.nextCursor
                }
                CoachingResult.SignedOut -> onSignedOut()
                is CoachingResult.Failed -> log("list_more_failed", r.code)
            }
            moreLoading = false
        }
    }

    fun newConversation() {
        if (isGenerating) return
        openJob?.cancel()
        conversationId = null; title = null; messages = emptyList(); historyLoad = Load.LOADED; nextBefore = null
        live = null
    }

    fun open(item: ConversationItem) {
        if (isGenerating || item.id == conversationId) return
        conversationId = item.id
        title = item.title
        messages = emptyList()
        live = null
        loadHistory()
    }

    fun loadHistory() {
        val id = conversationId ?: return
        openJob?.cancel()
        historyLoad = Load.LOADING
        openJob = scope.launch {
            when (val r = api.messages(id)) {
                is CoachingResult.Ok -> if (conversationId == id) {
                    messages = r.value.items; title = r.value.title; nextBefore = r.value.nextBeforeSequenceNo; historyLoad = Load.LOADED
                    historyErrorCode = null
                }
                CoachingResult.SignedOut -> onSignedOut()
                is CoachingResult.Failed -> if (conversationId == id) { historyLoad = Load.ERROR; historyErrorCode = r.code; log("history_failed", r.code) }
            }
        }
    }

    fun loadOlder() {
        val id = conversationId ?: return
        val before = nextBefore ?: return
        if (olderLoading) return
        olderLoading = true
        scope.launch {
            when (val r = api.messages(id, before)) {
                is CoachingResult.Ok -> if (conversationId == id) {
                    val known = messages.map { it.id }.toSet()
                    messages = r.value.items.filterNot { it.id in known } + messages
                    nextBefore = r.value.nextBeforeSequenceNo
                }
                CoachingResult.SignedOut -> onSignedOut()
                is CoachingResult.Failed -> log("older_failed", r.code)
            }
            olderLoading = false
        }
    }

    /** 질문 보내기. 실패한 질문을 다시 물을 때도 새 전송 ID 를 쓴다(명세 6.1) */
    fun send(content: String): Boolean {
        if (isGenerating || !CoachingText.isValid(content)) return false
        val request = LiveRequest(UUID.randomUUID().toString(), conversationId, CoachingText.normalize(content))
        live = request
        scope.launch { run(request, retriedAuth = false, resent = false) }
        return true
    }

    private suspend fun run(request: LiveRequest, retriedAuth: Boolean, resent: Boolean) {
        val token = auth.accessToken()
        if (token == null) return fail(request, "AUTH_REQUIRED", accepted = false).also { signOutIfNeeded() }
        log("ask_started", if (resent) "resend" else "new")
        socket.ask(token, request.clientRequestId, request.sentConversationId, request.content).collect { event ->
            when (event) {
                SocketEvent.Ready -> Unit
                is SocketEvent.ConnectionError -> {
                    log("connection_error", event.code)
                    // 질문 시점의 액세스 만료는 갱신 뒤 같은 전송으로 다시 보낸다(명세 7.2)
                    if (event.code == "AUTH_EXPIRED" && !retriedAuth && auth.refresh() is RefreshOutcome.Refreshed) {
                        run(request, retriedAuth = true, resent = resent)
                    } else {
                        fail(request, event.code, accepted = false)
                        signOutIfNeeded()
                    }
                }
                is SocketEvent.Accepted -> {
                    update { it.copy(requestId = event.requestId) }
                    if (conversationId == null) { conversationId = event.conversationId; title = event.title }
                    refreshList()
                }
                is SocketEvent.Stage -> update { it.copy(stage = event.stage) }
                is SocketEvent.Delta -> update { it.copy(text = it.text + event.text, stage = "generating_answer") }
                is SocketEvent.EvidenceItems -> update { it.copy(evidence = event.items) }
                is SocketEvent.Completed -> finish(request, "completed")
                is SocketEvent.Failed -> {
                    log("ask_failed", event.code)
                    if (event.accepted) finish(request, "failed")
                    else fail(request, event.code, accepted = false, context = event.context, serverMessage = event.message)
                }
                is SocketEvent.Snapshot -> {
                    update { it.copy(requestId = event.state.requestId) }
                    if (conversationId == null) conversationId = event.state.conversationId
                    if (event.state.isDone) finish(request, event.state.status) else poll(request, event.state.requestId)
                }
                is SocketEvent.Closed -> {
                    val requestId = live?.takeIf { it.clientRequestId == request.clientRequestId }?.requestId
                    log("socket_closed", if (event.afterReady) "after_ready" else "before_ready")
                    when {
                        requestId != null -> poll(request, requestId)
                        // 보낸 뒤 접수 여부를 모르면 같은 전송으로 한 번 다시 보낸다. 이미 접수됐으면 snapshot 이 온다(명세 7.6)
                        event.afterReady && !resent -> run(request, retriedAuth, resent = true)
                        else -> fail(request, "CONNECTION_FAILED", accepted = false)
                    }
                }
            }
        }
    }

    /** 연결이 끊긴 진행 요청을 2초 간격으로 확인한다(명세 7.6). 생성 기한 120초를 넘기면 멈춘다 */
    private suspend fun poll(request: LiveRequest, requestId: String) {
        repeat(65) {
            delay(2_000)
            when (val r = api.request(requestId)) {
                is CoachingResult.Ok -> if (r.value.isDone) return finish(request, r.value.status)
                CoachingResult.SignedOut -> return onSignedOut()
                is CoachingResult.Failed -> if (r.status in 400..499 && r.status != 408 && r.status != 429) {
                    return fail(request, r.code, accepted = true)
                }
            }
        }
        fail(request, "RESULT_TIMEOUT", accepted = true)
    }

    /** 끝난 요청은 저장된 메시지로 바꾼다. 다시 읽지 못하면 받은 내용을 그대로 둔다 */
    private suspend fun finish(request: LiveRequest, status: String) {
        log("ask_finished", status)
        val id = conversationId
        if (id != null) {
            val r = api.messages(id)
            if (r is CoachingResult.Ok && conversationId == id) {
                messages = r.value.items; title = r.value.title; nextBefore = r.value.nextBeforeSequenceNo; historyLoad = Load.LOADED
                if (live?.clientRequestId == request.clientRequestId) live = null
                refreshList()
                return
            }
            if (r is CoachingResult.SignedOut) return onSignedOut()
        }
        if (status == "completed") update { it.copy(error = null, stage = null) }.also { keepAsMessage(request) }
        else fail(request, "RESULT_TIMEOUT", accepted = true)
    }

    /** 다시 읽기에 실패한 완료 답변은 화면의 실시간 답변을 메시지로 굳힌다 */
    private fun keepAsMessage(request: LiveRequest) {
        val current = live ?: return
        if (current.clientRequestId != request.clientRequestId) return
        messages = messages + listOf(
            CoachingMessage("u-${current.clientRequestId}", current.requestId.orEmpty(), "user", 0, current.content, "completed", null, emptyList(), null),
            CoachingMessage("a-${current.clientRequestId}", current.requestId.orEmpty(), "assistant", 0, current.text, "completed", null, current.evidence, null),
        )
        live = null
    }

    private fun fail(
        request: LiveRequest,
        code: String,
        accepted: Boolean,
        context: ErrorContext? = null,
        serverMessage: String? = null,
    ) {
        val text = CoachingText.error(code, context, serverMessage)
        update { it.copy(error = if (accepted) text else "질문을 보내지 못했어요. $text", rejected = !accepted, stage = null) }
        lastErrorCode = code
    }

    private var lastErrorCode: String? = null

    private suspend fun signOutIfNeeded() {
        if (lastErrorCode == "AUTH_REQUIRED" || auth.current() == null) onSignedOut()
    }

    private fun update(change: (LiveRequest) -> LiveRequest) {
        live = live?.let(change)
    }

    private fun log(event: String, detail: String) = FileLog.i("coaching", "$event $detail")
}
