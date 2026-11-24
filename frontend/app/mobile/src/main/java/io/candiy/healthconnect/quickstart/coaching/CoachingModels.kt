package io.candiy.healthconnect.quickstart.coaching

import org.json.JSONArray
import org.json.JSONObject

/*
 * AI 코치(웹 /coaching 과 같은 백엔드, backend/docs/api-spec.md 6·7절)의 앱 쪽 모델과 JSON 해석.
 * 웹 frontend/web/src/lib/features/coaching 의 types·parse 와 같은 계약을 앱에 맞게 줄였다.
 * 근거(evidence)의 모델과 해석은 CoachingEvidence.kt 에 있다(S15P31A403-360 부터 앱에서도 근거 카드·상세를 보여 준다).
 */

/** 명세 6.2 대화 목록 항목 */
data class ConversationItem(
    val id: String,
    val title: String,
    val updatedAt: String,
    val lastRequestStatus: String,
)

data class ConversationPage(val items: List<ConversationItem>, val nextCursor: String?)

/** 건강 조회 실패 사유(명세 2.4). 사유를 모르면 null */
data class ErrorContext(val reason: String, val metrics: List<String>)

/** 명세 6.3 메시지 한 개 */
data class CoachingMessage(
    val id: String,
    val requestId: String,
    val role: String,
    val sequenceNo: Int,
    val content: String,
    val requestStatus: String,
    val errorCode: String?,
    val evidence: List<Evidence>,
    val errorContext: ErrorContext?,
) {
    val isUser get() = role == "user"
    val isGenerating get() = requestStatus == "accepted" || requestStatus == "generating"
}

data class MessagePage(
    val conversationId: String,
    val title: String,
    val items: List<CoachingMessage>,
    val nextBeforeSequenceNo: Int?,
)

/** 명세 6.4 요청 상태(그리고 같은 모양의 request_snapshot) */
data class RequestState(
    val requestId: String,
    val conversationId: String,
    val status: String,
    val content: String,
    val evidence: List<Evidence>,
    val errorCode: String?,
    val errorContext: ErrorContext?,
) {
    val isDone get() = status == "completed" || status == "failed"
}

/** WebSocket 서버 이벤트(명세 7.2·7.4·7.5). 앱이 쓰는 것만 나눈다 */
sealed interface SocketEvent {
    data object Ready : SocketEvent
    data class ConnectionError(val code: String) : SocketEvent
    data class Accepted(val requestId: String, val conversationId: String, val title: String) : SocketEvent
    data class Stage(val stage: String) : SocketEvent
    data class Delta(val text: String) : SocketEvent
    data class EvidenceItems(val items: List<Evidence>) : SocketEvent
    data class Completed(val content: String, val evidence: List<Evidence>) : SocketEvent
    data class Failed(
        val code: String,
        val message: String,
        val accepted: Boolean,
        val retryable: Boolean,
        val context: ErrorContext?,
    ) : SocketEvent
    data class Snapshot(val state: RequestState) : SocketEvent
    /** 서버가 이벤트 없이 연결을 닫았거나 연결이 끊겼다 */
    data class Closed(val afterReady: Boolean) : SocketEvent
}

object CoachingJson {
    private fun JSONObject.str(name: String): String? = if (isNull(name)) null else optString(name)

    private fun context(o: JSONObject?): ErrorContext? {
        if (o == null) return null
        val reason = o.str("reason") ?: return null
        val metrics = o.optJSONArray("metrics") ?: JSONArray()
        return ErrorContext(reason, List(metrics.length()) { metrics.getString(it) })
    }

    fun conversations(body: String): ConversationPage {
        val data = JSONObject(body).getJSONObject("data")
        val items = data.getJSONArray("items")
        return ConversationPage(
            items = List(items.length()) { i ->
                val o = items.getJSONObject(i)
                ConversationItem(
                    id = o.getString("id"),
                    title = o.getString("title"),
                    updatedAt = o.getString("updatedAt"),
                    lastRequestStatus = o.getString("lastRequestStatus"),
                )
            },
            nextCursor = data.str("nextCursor"),
        )
    }

    /** 메시지 하나가 형식이 달라도 대화 전체를 버리지 않고 그 메시지만 뺀다(웹 282와 같은 방침) */
    fun messages(body: String): MessagePage {
        val data = JSONObject(body).getJSONObject("data")
        val conversation = data.getJSONObject("conversation")
        val items = data.getJSONArray("items")
        return MessagePage(
            conversationId = conversation.getString("id"),
            title = conversation.getString("title"),
            items = (0 until items.length()).mapNotNull { i -> runCatching { message(items.getJSONObject(i)) }.getOrNull() },
            nextBeforeSequenceNo = if (data.isNull("nextBeforeSequenceNo")) null else data.getInt("nextBeforeSequenceNo"),
        )
    }

    private fun message(o: JSONObject) = CoachingMessage(
        id = o.getString("id"),
        requestId = o.getString("requestId"),
        role = o.getString("role"),
        sequenceNo = o.getInt("sequenceNo"),
        content = o.getString("content"),
        requestStatus = o.getString("requestStatus"),
        errorCode = o.str("errorCode"),
        evidence = EvidenceJson.list(o.optJSONArray("evidence")),
        errorContext = context(o.optJSONObject("errorContext")),
    )

    fun requestState(data: JSONObject) = RequestState(
        requestId = data.getString("requestId"),
        conversationId = data.getString("conversationId"),
        status = data.getString("status"),
        content = data.getJSONObject("assistantMessage").getString("content"),
        evidence = EvidenceJson.list(data.optJSONArray("evidence")),
        errorCode = data.str("errorCode"),
        errorContext = context(data.optJSONObject("errorContext")),
    )

    fun requestStateBody(body: String) = requestState(JSONObject(body).getJSONObject("data"))

    /** REST 오류 본문의 code. 읽을 수 없으면 null */
    fun errorCode(body: String?): String? =
        runCatching { JSONObject(body ?: "").getJSONObject("error").getString("code") }.getOrNull()

    /** 서버 이벤트 한 줄. 모르는 type 이나 읽을 수 없는 프레임은 null(무시) */
    fun event(text: String): SocketEvent? = runCatching {
        val o = JSONObject(text)
        val data = o.optJSONObject("data") ?: JSONObject()
        when (o.getString("type")) {
            "ready" -> SocketEvent.Ready
            "connection_error" -> SocketEvent.ConnectionError(data.getString("code"))
            "accepted" -> SocketEvent.Accepted(
                requestId = o.getString("requestId"),
                conversationId = o.getString("conversationId"),
                title = data.getJSONObject("conversation").getString("title"),
            )
            "status" -> SocketEvent.Stage(data.getString("stage"))
            "delta" -> SocketEvent.Delta(data.getString("text"))
            "evidence" -> SocketEvent.EvidenceItems(EvidenceJson.list(data.getJSONArray("items")))
            "completed" -> SocketEvent.Completed(
                content = data.getJSONObject("assistantMessage").getString("content"),
                evidence = EvidenceJson.list(data.optJSONArray("evidence")),
            )
            "error" -> SocketEvent.Failed(
                code = data.getString("code"),
                message = data.optString("message"),
                accepted = data.optBoolean("accepted"),
                retryable = data.optBoolean("retryable"),
                context = context(data),
            )
            "request_snapshot" -> SocketEvent.Snapshot(requestState(data))
            else -> null
        }
    }.getOrNull()

    fun auth(accessToken: String): String = JSONObject().put("type", "auth").put("accessToken", accessToken).toString()

    fun question(clientRequestId: String, conversationId: String?, content: String): String =
        JSONObject()
            .put("type", "question")
            .put("clientRequestId", clientRequestId)
            .put("conversationId", conversationId ?: JSONObject.NULL)
            .put("content", content)
            .toString()
}
