package io.candiy.healthconnect.quickstart.coaching

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.candiy.healthconnect.quickstart.CardSkeleton
import io.candiy.healthconnect.quickstart.Link
import io.candiy.healthconnect.quickstart.LinkIcons
import io.candiy.healthconnect.quickstart.LinkSkeleton
import io.candiy.healthconnect.quickstart.R
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** 첫 화면 질문 예시. 웹 코칭 첫 화면(CoachingWelcome.svelte)과 같은 세 가지 */
private data class Example(val label: String, val question: String, val description: String)

private val EXAMPLES = listOf(
    Example("변화 확인", "최근 수면 시간이 어떻게 달라졌어?", "이전 기간과 수면 기록 비교"),
    Example("지표 함께 보기", "잠을 짧게 잔 날 심박수는 어땠어?", "같은 날짜의 수면과 심박 기록 확인"),
    Example("활동량 비교", "지난주보다 얼마나 더 걸었어?", "일평균과 총 걸음 수 비교"),
)

/**
 * AI 코치 탭(웹 /coaching 을 앱으로 옮긴 것). 위 머리말, 가운데 대화(또는 첫 화면), 아래 입력창.
 * 대화 목록은 웹 휴대폰 화면처럼 머리말 왼쪽 버튼으로 여는 왼쪽 서랍이다(처음에는 아래 시트였는데, 왼쪽 위 버튼을
 * 눌렀는데 아래에서 올라와 어색해 웹과 같은 방향으로 바꿨다).
 * 답변 아래 근거 카드를 누르면 근거 상세(차트·표)가 화면 가운데 창으로 열린다(S15P31A403-360·372, EvidenceScreen.kt).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CoachingScreen(c: CoachingController) {
    LaunchedEffect(Unit) { c.start() }
    // 대화 목록 서랍. 열려 있을 때만 옆으로 밀어 닫을 수 있고, 닫혀 있을 때는 화면 가장자리 밀기로 열리지 않는다(대화 스크롤과 겹치지 않게)
    val drawer = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    fun closeList() { scope.launch { drawer.close() } }
    BackHandler(enabled = drawer.isOpen) { closeList() }
    // 근거 상세를 열어 둔 답변의 근거. null 이면 닫힌 상태다
    var openEvidence by remember { mutableStateOf<List<Evidence>?>(null) }
    var question by remember { mutableStateOf("") }
    // 보내면 키보드를 내려 답변이 화면을 다 쓰게 한다(제미나이와 같은 동작). 다음 질문은 입력칸을 다시 눌러 쓴다
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    fun ask(text: String): Boolean = c.send(text).also { sent -> if (sent) { keyboard?.hide(); focus.clearFocus() } }

    openEvidence?.let { evidence ->
        EvidenceDialog(evidence, hourly = remember(evidence) { c.hourlySteps() }) { openEvidence = null }
    }

    ModalNavigationDrawer(
        drawerState = drawer,
        gesturesEnabled = drawer.isOpen,
        drawerContent = {
            // 바깥(MainTabs)이 이미 창 여백을 뺐으므로 서랍은 여백을 다시 빼지 않는다. 폭은 화면의 82%, 넓은 화면에서도 320dp 까지
            ModalDrawerSheet(
                modifier = Modifier.fillMaxWidth(0.82f).widthIn(max = 320.dp),
                drawerContainerColor = Link.Background,
                // Material 은 서랍 면에 강조색을 옅게 섞는다(tonal elevation). 웹처럼 흰 면으로 두려고 끈다
                drawerTonalElevation = 0.dp,
                drawerShape = RoundedCornerShape(topEnd = 20.dp, bottomEnd = 20.dp),
                windowInsets = WindowInsets(0, 0, 0, 0),
            ) {
                ConversationDrawer(c, close = ::closeList)
            }
        },
    ) {
        Column(Modifier.fillMaxSize()) {
            Header(c, onList = { keyboard?.hide(); focus.clearFocus(); c.refreshList(); scope.launch { drawer.open() } })
            Box(Modifier.weight(1f).fillMaxWidth()) {
                if (c.conversationId == null && c.live == null) Welcome { question = ""; ask(it) }
                else Conversation(c, onAskAgain = { ask(it) }, onEvidence = { openEvidence = it })
            }
            InputBar(
                value = question,
                onValue = { question = it },
                locked = c.isGenerating,
                onSend = { if (ask(question)) question = "" },
            )
        }
    }
}

@Composable
private fun Header(c: CoachingController, onList: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(start = 8.dp, end = 12.dp, top = 6.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        RoundIcon(LinkIcons.Panel, "대화 목록 열기", onList)
        Spacer(Modifier.width(6.dp))
        Column(Modifier.weight(1f)) {
            Text("AI 코치", fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Link.Text)
            c.title?.let { Text(it, fontSize = 12.sp, color = Link.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis) }
        }
        // "새 대화"는 대화 목록 서랍 맨 위에만 둔다. 여기에도 있으면 같은 버튼이 두 곳에 보이고 제목 줄이 복잡하다(웹과 같은 자리, S15P31A403-372)
    }
    HorizontalDivider(color = Link.Surface)
}

@Composable
private fun RoundIcon(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    Box(
        Modifier.size(40.dp).background(Link.Background, CircleShape).border(1.dp, Link.Border, CircleShape)
            .clickable(onClick = onClick).semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) { Icon(icon, contentDescription = null, tint = Link.Text, modifier = Modifier.size(20.dp)) }
}

@Composable
private fun Welcome(onAsk: (String) -> Unit) {
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Image(painterResource(R.drawable.candy), contentDescription = null, modifier = Modifier.size(56.dp, 30.dp))
            Spacer(Modifier.height(10.dp))
            Text("무엇이 궁금하세요?", fontSize = 24.sp, fontWeight = FontWeight.Bold, color = Link.Text)
            Spacer(Modifier.height(6.dp))
            Text("내 건강 기록을 바탕으로 답해요. 예시를 누르면 바로 질문해요.", fontSize = 14.sp, color = Link.Sub)
            Spacer(Modifier.height(4.dp))
        }
        items(EXAMPLES) { e ->
            Column(
                Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(18.dp)).clickable { onAsk(e.question) }
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            ) {
                Text(e.label, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = Link.Primary)
                Spacer(Modifier.height(4.dp))
                Text(e.question, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                Spacer(Modifier.height(2.dp))
                Text(e.description, fontSize = 13.sp, color = Link.Muted)
            }
        }
    }
}

@Composable
private fun Conversation(c: CoachingController, onAskAgain: (String) -> Unit, onEvidence: (List<Evidence>) -> Unit) {
    val state = rememberLazyListState()
    val live = c.live
    // 새 메시지·답변 조각이 오면 맨 아래로 따라간다
    val tail = c.messages.size + (live?.text?.length ?: 0) + (if (live?.error != null) 1 else 0)
    LaunchedEffect(tail, c.historyLoad) {
        val last = state.layoutInfo.totalItemsCount - 1
        if (last >= 0) state.animateScrollToItem(last)
    }
    LazyColumn(Modifier.fillMaxSize(), state = state, contentPadding = PaddingValues(horizontal = 18.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        if (c.historyLoad == Load.LOADING && live == null) {
            item { HistorySkeleton() }
        }
        if (c.historyLoad == Load.ERROR) {
            item { Notice(CoachingText.loadFailed("이전 대화를", c.historyErrorCode), "다시 불러오기") { c.loadHistory() } }
        }
        if (c.nextBefore != null && c.historyLoad == Load.LOADED) {
            item {
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    Text(
                        if (c.olderLoading) "불러오는 중…" else "이전 대화 보기",
                        fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = Link.Sub,
                        modifier = Modifier.background(Link.Surface, RoundedCornerShape(50)).clickable(enabled = !c.olderLoading) { c.loadOlder() }
                            .padding(horizontal = 14.dp, vertical = 7.dp),
                    )
                }
            }
        }
        items(c.messages, key = { it.id }) { m ->
            if (m.isUser) UserBubble(m.content)
            else Answer(
                content = m.content,
                streaming = false,
                stage = if (m.isGenerating) "generating_answer" else null,
                evidence = m.evidence,
                onEvidence = onEvidence,
                error = if (m.requestStatus == "failed") CoachingText.error(m.errorCode ?: "", m.errorContext) else null,
                onRefresh = if (m.isGenerating) ({ c.loadHistory() }) else null,
            )
        }
        if (live != null) {
            item(key = "live-user-${live.clientRequestId}") { UserBubble(live.content) }
            item(key = "live-answer-${live.clientRequestId}") {
                Answer(
                    content = live.text,
                    streaming = live.waiting,
                    stage = if (live.waiting) live.stage ?: "sending" else null,
                    evidence = live.evidence,
                    onEvidence = onEvidence,
                    error = live.error,
                    onRetry = if (!live.waiting && !c.isGenerating) ({ onAskAgain(live.content) }) else null,
                )
            }
        }
    }
}

/**
 * 질문은 오른쪽의 둥근 말풍선 하나로 묶고, 답변은 말풍선 없이 화면 폭을 쓰는 글로 둔다.
 * 질문 글자는 답변 본문과 같은 16sp 다(멘토링 피드백, S15P31A403-315). 둘은 크기가 아니라 위치·말풍선으로 구분하고 답변 첫 줄만 17sp 로 크다.
 * 질문·답변을 색 상자 유무와 글자 위계로 나누는 방식은 제미나이 모바일 앱(11-06 실기기 확인)과 같고, 색은 웹과 같은 연분홍이다
 */
@Composable
private fun UserBubble(text: String) {
    BoxWithConstraints(Modifier.fillMaxWidth().padding(top = 8.dp), contentAlignment = Alignment.CenterEnd) {
        Text(
            text, fontSize = 16.sp, lineHeight = 24.sp, color = Link.Text,
            modifier = Modifier.widthIn(max = maxWidth * 0.78f).background(Link.Pink, RoundedCornerShape(22.dp))
                .padding(horizontal = 16.dp, vertical = 11.dp),
        )
    }
}

@Composable
private fun Answer(
    content: String,
    streaming: Boolean,
    stage: String?,
    evidence: List<Evidence>,
    onEvidence: (List<Evidence>) -> Unit,
    error: String?,
    onRetry: (() -> Unit)? = null,
    onRefresh: (() -> Unit)? = null,
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Image(painterResource(R.drawable.candy), contentDescription = "AI 코치", modifier = Modifier.size(30.dp, 16.dp))
            if (stage != null) {
                Spacer(Modifier.width(8.dp))
                Progress(if (stage == "sending") "질문을 보내고 있어요" else CoachingText.STAGES[stage] ?: "답변을 작성하고 있어요")
            }
        }
        if (content.isNotBlank()) Markdown(content, streaming)
        // 답변을 쓰는 동안은 근거가 먼저 와도 숨기고, 끝나면 카드로 보여 준다(웹과 같은 순서)
        if (evidence.isNotEmpty() && !streaming) EvidenceCard(evidence) { onEvidence(evidence) }
        if (onRefresh != null && content.isBlank()) {
            Text("새로고침", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = Link.Primary, modifier = Modifier.clickable(onClick = onRefresh).padding(vertical = 4.dp))
        }
        if (error != null) Notice(error, if (onRetry != null) "다시 질문하기" else null, onRetry)
    }
}

/** 진행 안내(웹 CoachingProgress 와 같은 연분홍 알약). 움직이는 점 대신 고정 점 세 개 */
@Composable
private fun Progress(text: String) {
    Row(
        Modifier.background(Link.Pink, RoundedCornerShape(50)).padding(horizontal = 12.dp, vertical = 5.dp)
            .semantics { contentDescription = text },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        listOf(Link.Red, Link.Red.copy(alpha = 0.6f), Link.Coral).forEach {
            Box(Modifier.size(6.dp).background(it, CircleShape))
            Spacer(Modifier.width(3.dp))
        }
        Spacer(Modifier.width(3.dp))
        Text(text, fontSize = 12.sp, color = Link.Sub)
    }
}

/** 오류 상자(웹 CoachingErrorNotice 와 같은 연분홍 바탕) */
@Composable
private fun Notice(message: String, action: String?, onAction: (() -> Unit)?) {
    Column(
        Modifier.fillMaxWidth().background(Link.BadBg, RoundedCornerShape(14.dp)).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(message, fontSize = 14.sp, lineHeight = 20.sp, color = Link.Text)
        if (action != null && onAction != null) {
            Text(
                action, fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Primary,
                modifier = Modifier.background(Link.Background, RoundedCornerShape(10.dp)).clickable(onClick = onAction)
                    .padding(horizontal = 12.dp, vertical = 6.dp),
            )
        }
    }
}

/**
 * 답변 본문. 위계는 글자 크기·굵기·간격으로만 준다(제미나이와 같은 문서형).
 * 첫 문단은 질문에 대한 답 한 문장이라(AI 지침 v1.12) 조금 크고 굵게(웹 AssistantMessage 와 같은 위계), 나머지는 16sp 보통 글자,
 * 소제목은 17sp 굵게, 목록은 들여 쓴 점·번호와 넉넉한 항목 간격, 표는 테두리 없이 머리 행·가로 구분선만 둔다.
 */
@Composable
private fun Markdown(content: String, streaming: Boolean) {
    val blocks = remember(content, streaming) { CoachingMarkdown.parse(content, streaming) }
    Column {
        blocks.forEachIndexed { index, block ->
            val gap = when {
                index == 0 -> 0.dp
                block is CoachingMarkdown.Block.Heading -> 18.dp
                block is CoachingMarkdown.Block.Item && blocks[index - 1] is CoachingMarkdown.Block.Item -> 6.dp
                else -> 12.dp
            }
            Spacer(Modifier.height(gap))
            when (block) {
                is CoachingMarkdown.Block.Paragraph -> {
                    val lead = index == 0
                    Text(
                        rich(block.spans), color = Link.Text, fontSize = if (lead) 17.sp else 16.sp, lineHeight = 26.sp,
                        fontWeight = if (lead) FontWeight.SemiBold else FontWeight.Normal,
                    )
                }
                is CoachingMarkdown.Block.Heading -> Text(rich(block.spans), fontSize = 17.sp, lineHeight = 24.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                is CoachingMarkdown.Block.Item -> Row(Modifier.padding(start = (block.depth * 18).dp)) {
                    val numbered = block.marker != "•"
                    Box(Modifier.width(if (numbered) 24.dp else 18.dp).height(26.dp), contentAlignment = if (numbered) Alignment.CenterStart else Alignment.Center) {
                        if (numbered) Text(block.marker, fontSize = 16.sp, color = Link.Sub, fontWeight = FontWeight.SemiBold)
                        else Box(Modifier.size(5.dp).background(if (block.depth == 0) Link.Text else Link.Muted, CircleShape))
                    }
                    Spacer(Modifier.width(6.dp))
                    Text(rich(block.spans), fontSize = 16.sp, lineHeight = 26.sp, color = Link.Text)
                }
                is CoachingMarkdown.Block.Table -> AnswerTable(block)
                CoachingMarkdown.Block.Rule -> HorizontalDivider(color = Link.Border, modifier = Modifier.padding(vertical = 4.dp))
            }
        }
    }
}

/** 표: 머리 행은 굵은 작은 글자, 행 사이 옅은 가로 선, 첫 열(날짜·지표 이름)은 굵게. 넓으면 옆으로 밀어 본다 */
@Composable
private fun AnswerTable(table: CoachingMarkdown.Block.Table) {
    val columns = table.header.size
    fun width(column: Int) = if (column == 0) 104.dp else 120.dp
    Column(
        Modifier.horizontalScroll(rememberScrollState())
            .semantics { contentDescription = "표 ${table.rows.size}행 ${columns}열" },
    ) {
        Row(Modifier.padding(bottom = 2.dp)) {
            table.header.forEachIndexed { column, cell ->
                Text(rich(cell), fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.Bold, color = Link.Sub,
                    modifier = Modifier.width(width(column)).padding(end = 12.dp, top = 4.dp, bottom = 8.dp))
            }
        }
        table.rows.forEach { row ->
            HorizontalDivider(color = Link.Border, modifier = Modifier.width((104 + 120 * (columns - 1)).dp))
            Row {
                row.forEachIndexed { column, cell ->
                    Text(rich(cell), fontSize = 14.sp, lineHeight = 21.sp, color = Link.Text,
                        fontWeight = if (column == 0) FontWeight.SemiBold else FontWeight.Normal,
                        modifier = Modifier.width(width(column)).padding(end = 12.dp, top = 9.dp, bottom = 9.dp))
                }
            }
        }
    }
}

private fun rich(spans: List<CoachingMarkdown.Span>): AnnotatedString = buildAnnotatedString {
    spans.forEach { s ->
        withStyle(SpanStyle(fontWeight = if (s.bold) FontWeight.Bold else null, fontStyle = if (s.italic) FontStyle.Italic else null)) { append(s.text) }
    }
}

@Composable
private fun InputBar(value: String, onValue: (String) -> Unit, locked: Boolean, onSend: () -> Unit) {
    val canSend = !locked && CoachingText.isValid(value)
    val tooLong = CoachingText.length(value) > CoachingText.MAX_LENGTH
    Column(Modifier.fillMaxWidth().background(Link.Background).padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = value,
                onValueChange = onValue,
                // 답변을 쓰는 동안은 잠그되 글자는 남긴다(웹과 같은 동작)
                readOnly = locked,
                placeholder = { Text(if (locked) "답변을 작성하고 있어요" else "궁금한 점을 물어보세요.", fontSize = 15.sp, color = Link.Disabled) },
                textStyle = androidx.compose.ui.text.TextStyle(fontSize = 15.sp, color = Link.Text),
                maxLines = 4,
                shape = RoundedCornerShape(16.dp),
                isError = tooLong,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (canSend) onSend() }),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Link.Red, unfocusedBorderColor = Link.Border,
                    focusedContainerColor = Link.Background, unfocusedContainerColor = Link.Background, cursorColor = Link.Red,
                ),
                modifier = Modifier.weight(1f).heightIn(min = 48.dp).semantics { contentDescription = "질문 입력" },
            )
            Spacer(Modifier.width(8.dp))
            Box(
                Modifier.size(44.dp).background(if (canSend) Link.Primary else Link.Coral, CircleShape)
                    .clickable(enabled = canSend, onClick = onSend).semantics { contentDescription = "질문 보내기" },
                contentAlignment = Alignment.Center,
            ) { Icon(LinkIcons.Send, contentDescription = null, tint = androidx.compose.ui.graphics.Color.White, modifier = Modifier.size(20.dp)) }
        }
        Text(
            if (tooLong) "질문은 ${CoachingText.MAX_LENGTH}자까지 보낼 수 있어요" else "AI 코치는 참고용이며, 진단·처방을 제공하지 않아요.",
            fontSize = 11.sp, color = if (tooLong) Link.Bad else Link.Disabled, modifier = Modifier.padding(start = 4.dp, top = 4.dp),
        )
    }
}

@Composable
private fun HistorySkeleton() {
    Column(Modifier.semantics { contentDescription = "이전 대화를 불러오는 중" }, verticalArrangement = Arrangement.spacedBy(16.dp)) {
        repeat(2) {
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) { LinkSkeleton(Modifier.width(200.dp).height(42.dp), RoundedCornerShape(16.dp)) }
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                LinkSkeleton(Modifier.size(34.dp, 18.dp))
                LinkSkeleton(Modifier.fillMaxWidth(0.7f).height(18.dp))
                LinkSkeleton(Modifier.fillMaxWidth().height(14.dp))
                LinkSkeleton(Modifier.fillMaxWidth(0.85f).height(14.dp))
            }
        }
    }
}

/** 대화 목록 서랍. 웹 휴대폰의 서랍과 같은 내용(새 대화 · 대화 목록 · 이어 불러오기) */
@Composable
private fun ConversationDrawer(c: CoachingController, close: () -> Unit) {
    val today = LocalDate.now()
    // 웹 대화 목록(ConversationList)과 같은 모양: 흰 면, 제목 줄 아래 구분선, 그 아래 "새 대화" 줄, 고른 대화는 연분홍 면과 왼쪽 레드 막대
    Column(Modifier.fillMaxSize()) {
    Text(
        "대화 목록", fontSize = 18.sp, lineHeight = 26.sp, fontWeight = FontWeight.Bold, color = Link.Text,
        modifier = Modifier.padding(start = 24.dp, end = 16.dp, top = 18.dp, bottom = 16.dp).semantics { heading() },
    )
    HorizontalDivider(color = Link.Border)
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        // 어느 화면에서 서랍을 열어도 항상 둔다. 첫 화면(새 대화)에 있을 때는 지금 여기라는 뜻으로 고른 대화와 같은 표시를 한다
        // (ChatGPT·Claude·Gemini 앱의 서랍과 같다). 그때 누르면 서랍만 닫힌다
        item {
            val atHome = c.conversationId == null && c.live == null
            val tint = if (c.isGenerating) Link.Disabled else Link.Primary
            Row(
                Modifier.fillMaxWidth()
                    .drawBehind {
                        if (atHome) drawRoundRect(Link.Red, topLeft = Offset(-4.dp.toPx(), 0f), size = Size(4.dp.toPx(), size.height), cornerRadius = CornerRadius(2.dp.toPx()))
                    }
                    .clip(RoundedCornerShape(12.dp))
                    .background(if (atHome) Link.Pink else Link.Background)
                    .clickable(enabled = !c.isGenerating, role = Role.Button) { if (!atHome) c.newConversation(); close() }
                    .semantics { selected = atHome }
                    .padding(horizontal = 12.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(LinkIcons.Plus, contentDescription = null, tint = tint, modifier = Modifier.size(20.dp))
                Spacer(Modifier.width(8.dp))
                Text("새 대화", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = tint)
            }
        }
        when {
            c.listLoad == Load.LOADING -> item {
                Column(Modifier.semantics { contentDescription = "대화 목록을 불러오는 중" }, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    repeat(4) {
                        Column(Modifier.fillMaxWidth().background(Link.Surface, RoundedCornerShape(12.dp)).padding(12.dp)) {
                            CardSkeleton(Modifier.fillMaxWidth(0.7f).height(14.dp))
                            Spacer(Modifier.height(6.dp))
                            CardSkeleton(Modifier.width(60.dp).height(11.dp))
                        }
                    }
                }
            }
            c.listLoad == Load.ERROR -> item { Notice(CoachingText.loadFailed("대화 목록을", c.listErrorCode), "다시 불러오기") { c.refreshList() } }
            c.conversations.isEmpty() -> item { Text("아직 대화가 없어요. 첫 질문을 보내 보세요.", fontSize = 14.sp, lineHeight = 22.sp, color = Link.Sub, modifier = Modifier.padding(8.dp)) }
        }
        items(c.conversations, key = { it.id }) { item ->
            val active = item.id == c.conversationId
            Column(
                Modifier.fillMaxWidth()
                    // 고른 대화: 연분홍 면(웹 primary-soft) 왼쪽 바깥에 브랜드 레드 막대(폭 4)를 둔다
                    .drawBehind {
                        if (active) drawRoundRect(Link.Red, topLeft = Offset(-4.dp.toPx(), 0f), size = Size(4.dp.toPx(), size.height), cornerRadius = CornerRadius(2.dp.toPx()))
                    }
                    .clip(RoundedCornerShape(12.dp))
                    .background(if (active) Link.Pink else Link.Background)
                    .clickable(enabled = !c.isGenerating) { c.open(item); close() }
                    .semantics { selected = active }
                    .padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(item.title, fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(
                    time(item.updatedAt, today) + if (active) " · ${statusLabel(item.lastRequestStatus)}" else "",
                    fontSize = 12.sp, color = Link.Sub,
                )
            }
        }
        if (c.nextCursor != null) {
            item {
                Box(Modifier.fillMaxWidth().padding(top = 8.dp), contentAlignment = Alignment.Center) {
                    Text(
                        if (c.moreLoading) "불러오는 중…" else "이전 대화 더 보기", fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = Link.Sub,
                        modifier = Modifier.background(Link.Surface, RoundedCornerShape(50)).clickable(enabled = !c.moreLoading) { c.loadMoreConversations() }
                            .padding(horizontal = 14.dp, vertical = 7.dp),
                    )
                }
            }
        }
    }
    }
}

private fun statusLabel(status: String) = when (status) {
    "completed" -> "AI 답변 완료"
    "failed" -> "답변 실패"
    else -> "답변 작성 중"
}

private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")
private val DAY = DateTimeFormatter.ofPattern("MM.dd")

/** 웹 대화 목록과 같은 표시: 오늘 09:45 · 어제 14:04 · 11.03 */
internal fun time(iso: String, today: LocalDate): String = runCatching {
    val at = Instant.parse(iso).atZone(ZoneId.systemDefault())
    when (at.toLocalDate()) {
        today -> "오늘 ${at.format(CLOCK)}"
        today.minusDays(1) -> "어제 ${at.format(CLOCK)}"
        else -> at.format(DAY)
    }
}.getOrDefault("")
