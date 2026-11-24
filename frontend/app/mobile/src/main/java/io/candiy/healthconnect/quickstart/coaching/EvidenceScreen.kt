package io.candiy.healthconnect.quickstart.coaching

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ScrollState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.material3.LocalTextStyle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.DialogWindowProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import kotlin.math.roundToInt
import io.candiy.healthconnect.quickstart.Link
import io.candiy.healthconnect.quickstart.LinkIcons
import io.candiy.healthconnect.quickstart.coaching.EvidenceView as V

/*
 * AI 답변의 근거 화면(S15P31A403-360). 웹의 "이 답변에서 사용한 기록" 카드(EvidenceSummary)와
 * "참조 데이터 상세" 창(EvidenceDialog · KindEvidencePanel · DailyBarChart)을 앱 화면에 맞게 옮겼다.
 * 차트는 외부 라이브러리 없이 Compose 로 직접 그린다(막대·눈금선·평균선뿐이라 직접 그리는 편이 가볍다).
 * 값과 문구 규칙은 EvidenceView 에 있고 여기서는 배치만 한다.
 *
 * 색·글자 크기·여백·칸 배치는 웹을 휴대폰 폭으로 봤을 때와 같게 맞췄다(S15P31A403-372).
 * 강조 글자는 진한 레드(Link.Primary), 차트 막대·진행 막대는 브랜드 레드(Link.Red), 상자는 연회색 면에 테두리,
 * 간격은 묶음 사이 24 · 제목과 내용 사이 12 다. 상세는 웹처럼 화면 가운데 뜨는 흰 창이다. 웹과 다른 점은 막대를 눌러 값을 본다는 것뿐이다.
 */

// 차트 막대 색. 웹 DailyBarChart 와 같다(S15P31A403-440): 평균 이상(브랜드 레드) · 평균 미만(옅은 코랄). 평균선이 없으면 코랄 한 가지다
private val BarHigh = Link.Red
private val BarLow = Link.Coral
private val BarNormal = Link.CoralMain
private val BarLowest = Link.CoralSoft
internal val ChartHeight = 176.dp
// 세로축 글자 폭. "120분"이 32dp 에서는 끝 글자가 잘려 넓혔다(S15P31A403-440)
internal val AxisWidth = 38.dp
internal val AxisGap = 8.dp
/** 막대 위 꼬리표("최고")의 대략적인 폭. 칸이 이보다 좁으면 양옆 막대 위로 올린다 */
private val ExtremeWidth = 30.dp
internal val BoxShape = RoundedCornerShape(16.dp)

/** 답변 아래 카드. 어떤 기간·지표를 썼는지 알리고 누르면 상세를 연다 */
@Composable
fun EvidenceCard(evidence: List<Evidence>, onOpen: () -> Unit) {
    val groups = remember(evidence) { V.groups(evidence) }
    val kinds = remember(evidence) { V.kinds(evidence) }
    val first = groups.firstOrNull()
    val periodOf: Evidence = first?.current ?: kinds.firstOrNull() ?: return
    val days = first?.current?.snapshot?.days?.size ?: 0
    // 상세에 차트가 하나도 없으면(기록이 하루 이하·하루 기간) "차트 보기"라고 부르지 않는다
    val hasChart = groups.any { V.showsChart(it.current.snapshot) }

    // 웹 카드와 같은 모양: 흰 면에 테두리, 지표 이름은 진한 레드, "차트 보기"는 상자 없이 글자와 화살표만
    Column(
        Modifier.fillMaxWidth().background(Link.Background, RoundedCornerShape(12.dp)).border(1.dp, Link.Border, RoundedCornerShape(12.dp))
            .padding(start = 20.dp, end = 20.dp, top = 18.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text("이 답변에서 사용한 기록", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text)
        Text(
            buildAnnotatedString {
                if (days > 1) append("${days}일 · ")
                append(V.period(periodOf.periodStart, periodOf.periodEnd))
                // 한 주씩 여러 번, 또는 31일 제한으로 둘로 나눠 조회한 기록이면 한 범위로 보여 주고 나눠 부른 횟수를 알린다
                if (first?.merged == true) withStyle(SpanStyle(color = Link.Sub)) { append(" · ${first.parts.size}번 나눠 조회") }
                first?.comparison?.let { append("\n비교 ${V.period(it.periodStart, it.periodEnd)}") }
            },
            fontSize = 15.sp, lineHeight = 22.sp, color = Link.Text,
        )
        Text(
            buildAnnotatedString {
                append("사용 데이터: ")
                groups.forEachIndexed { index, g ->
                    if (index > 0) append("  ")
                    val cover = V.coverage(g)
                    // 기록이 있는 지표만 강조색으로 쓴다(웹과 같다)
                    withStyle(SpanStyle(color = if (cover.hasAnyValue) Link.Primary else Link.Sub)) { append(V.METRIC_LABELS[g.metric] ?: g.metric) }
                    V.coverageNote(cover)?.let { append(" · $it") }
                }
                kinds.forEachIndexed { index, kind ->
                    if (index > 0 || groups.isNotEmpty()) append("  ")
                    withStyle(SpanStyle(color = Link.Primary)) { append(V.kindLabel(kind)) }
                }
            },
            fontSize = 14.sp, lineHeight = 21.sp, color = Link.Sub,
        )
        Row(
            Modifier.clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onOpen).padding(vertical = 6.dp).padding(end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(if (hasChart) "차트 보기" else "기록 보기", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Primary)
            Spacer(Modifier.width(2.dp))
            Icon(LinkIcons.ChevronRight, contentDescription = null, tint = Link.Primary, modifier = Modifier.size(18.dp))
        }
    }
}

/**
 * 근거 상세 화면. 화면을 가득 채우는 흰 면이다(S15P31A403-425). 처음에는 웹처럼 둘레 여백 16 · 둥글기 24 의 가운데 창이었는데,
 * 휴대폰에서는 그 여백만큼 차트가 좁아져 일주일 칸과 날짜가 빽빽했다. 상태 표시줄 아래부터 내비게이션 바 위까지를 다 쓴다.
 *
 * 아래에서 올라오는 시트를 쓰지 않는 이유(S15P31A403-372):
 * - 시트는 아래로 쓸어내리면 닫히는데, 긴 내용을 위아래로 넘기는 손짓과 겹쳐 의도치 않게 닫혔다.
 * - Material 시트는 면에 강조색을 옅게 섞어(tonal elevation) 배경이 분홍빛이 됐다. 웹은 흰 면이다.
 * 닫는 방법은 위 X 버튼과 아래 "닫기" 버튼이다. 창 밖을 눌러도 닫히지 않는다(웹과 같다).
 * 기기의 뒤로 가기는 Android 기본 동작이라 그대로 닫는다.
 */
@Composable
fun EvidenceDialog(evidence: List<Evidence>, hourly: HourlyStepsLoader? = null, onClose: () -> Unit) {
    // 창이 놓여도 되는 세로 범위(화면 좌표, px): 상태 표시줄·카메라 구멍 아래부터 내비게이션 바 위까지.
    // 앱 화면의 창에서 읽는다. 대화 상자 창은 기기·버전에 따라 이 값을 0 으로 받는다
    val hostView = LocalView.current
    val safe = remember(hostView) {
        val bars = ViewCompat.getRootWindowInsets(hostView)
            ?.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
        val screenHeight = hostView.rootView.height
        (bars?.top ?: 0) to (screenHeight - (bars?.bottom ?: 0))
    }
    Dialog(
        onDismissRequest = onClose,
        properties = DialogProperties(usePlatformDefaultWidth = false, dismissOnClickOutside = false, decorFitsSystemWindows = false),
    ) {
        val dialogView = LocalView.current
        // 뒤 화면을 가리는 정도를 웹(글자색 30%)에 맞춘다. 기본값은 더 어둡다
        val window = (dialogView.parent as? DialogWindowProvider)?.window
        SideEffect { window?.setDimAmount(0.3f) }

        /*
         * 내비게이션 바와 겹치지 않게 하는 방법(S15P31A403-372, Android 15 · 3버튼 내비게이션 실기기에서 좌표를 재서 정했다).
         * Compose 대화 상자는 내용을 화면 전체 높이까지 잴 수 있게 하는데, 시스템은 창을 시스템 막대 안쪽에 놓는다.
         * 그래서 내용이 창보다 길어져 아래로 넘쳤고(재 보니 내용 높이 2399px · 창 2190px), 아래 "닫기" 버튼이 내비게이션 바에 걸렸다.
         * 창이 막대 크기를 알려 주는지, 어디에 놓이는지는 기기·버전마다 달라서 가정하지 않는다.
         * 내용이 실제로 놓인 화면 좌표를 재고, 안전한 범위를 벗어난 만큼만 위아래 여백으로 뺀다.
         */
        var overflow by remember { mutableStateOf<Pair<Int, Int>?>(null) }
        val density = LocalDensity.current
        Box(
            Modifier.fillMaxSize()
                .onGloballyPositioned { coordinates ->
                    val location = IntArray(2).also(dialogView::getLocationOnScreen)
                    val top = location[1] + coordinates.positionInRoot().y.roundToInt()
                    val bottom = top + coordinates.size.height
                    val next = (safe.first - top).coerceAtLeast(0) to (bottom - safe.second).coerceAtLeast(0)
                    if (next != overflow) overflow = next
                }
                // 위치를 재기 전 한 순간은 그리지 않는다(여백 없는 큰 창이 깜빡 보이지 않게)
                .alpha(if (overflow == null) 0f else 1f)
                .padding(
                    top = with(density) { (overflow?.first ?: 0).toDp() },
                    bottom = with(density) { (overflow?.second ?: 0).toDp() },
                )
                ,
        ) {
            Box(Modifier.fillMaxSize().background(Link.Background)) {
                EvidenceDetail(evidence, hourly, onClose)
            }
        }
    }
}

/** 근거 상세 창의 내용. 지표가 여럿이면 위 탭으로 고른다 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun EvidenceDetail(evidence: List<Evidence>, hourly: HourlyStepsLoader?, onClose: () -> Unit) {
    val groups = remember(evidence) { V.groups(evidence) }
    val kinds = remember(evidence) { V.kinds(evidence) }
    val tabs = remember(evidence) { groups.map { it.metric to (V.METRIC_LABELS[it.metric] ?: it.metric) } + kinds.map { it.id to V.kindLabel(it) } }
    var selectedKey by remember(evidence) { mutableStateOf(tabs.firstOrNull()?.first) }
    val group = groups.firstOrNull { it.metric == selectedKey }
    val kind = kinds.firstOrNull { it.id == selectedKey }

    // 차트마다 고른 보기(일주일·기간 전체). 같은 창에서 그 탭으로 돌아오면 고른 보기로 다시 시작하고, 다른 탭의 차트에는 번지지 않는다.
    // 창을 닫으면 이 화면이 사라져 함께 비워지므로, 다시 열거나 다른 답변의 차트를 열면 일주일부터다(웹 439)
    val chartViews = remember(evidence) { mutableStateMapOf<String, Boolean>() }

    val scroll = rememberScrollState()
    // 지표 탭을 바꾸면 내용 길이가 달라지므로 맨 위부터 다시 보여 준다
    LaunchedEffect(selectedKey) { scroll.scrollTo(0) }

    Column(Modifier.fillMaxWidth()) {
    ScrollProgress(scroll)
    Column(
        Modifier.fillMaxWidth().verticalScroll(scroll).padding(horizontal = 16.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Text("참조 데이터 상세", fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.semantics { heading() })
                Spacer(Modifier.height(8.dp))
                Text("AI 답변에 사용된 건강 기록을 확인할 수 있어요.", fontSize = 15.sp, lineHeight = 22.sp, color = Link.Sub)
            }
            Spacer(Modifier.width(12.dp))
            // 웹과 같이 면 없는 둥근 닫기 버튼(보조 글자색)
            Box(
                Modifier.size(40.dp).clip(CircleShape).clickable(role = Role.Button, onClick = onClose)
                    .semantics { contentDescription = "닫기" },
                contentAlignment = Alignment.Center,
            // 아이콘 묶음에 X 가 따로 없어 더하기 아이콘을 45도 돌려 닫기로 쓴다
            ) { Icon(LinkIcons.Plus, contentDescription = null, tint = Link.Sub, modifier = Modifier.size(22.dp).rotate(45f)) }
        }

        if (tabs.size > 1) {
            val tab: @Composable (String, String, Modifier, Boolean) -> Unit = { key, label, modifier, narrow ->
                val selected = key == selectedKey
                Text(
                    label, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = if (selected) Link.Primary else Link.Sub, textAlign = TextAlign.Center,
                    maxLines = 1, softWrap = false,
                    modifier = modifier
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (selected) Link.Pink else Link.Background)
                        .border(1.dp, if (selected) Link.Pink else Link.Border, RoundedCornerShape(12.dp))
                        .clickable(role = Role.Tab) { selectedKey = key }
                        .then(if (narrow) Modifier else Modifier.widthIn(min = 96.dp))
                        .padding(horizontal = if (narrow) 4.dp else 24.dp, vertical = 12.dp),
                )
            }
            // 탭이 네 개면(수면·활동·심박·운동) 휴대폰 폭 한 줄에 96 폭 칸이 세 개만 들어가 하나가 다음 줄로 넘어갔다.
            // 네 개는 폭을 똑같이 나눠 한 줄에 둔다. 세 개까지는 그대로 한 줄이고, 다섯 개부터나 이름이 긴 탭이 있으면 다음 줄로 넘긴다
            if (tabs.size == 4 && tabs.all { it.second.length <= 5 }) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    tabs.forEach { (key, label) -> tab(key, label, Modifier.weight(1f), true) }
                }
            } else {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    tabs.forEach { (key, label) -> tab(key, label, Modifier, false) }
                }
            }
        }

        when {
            group != null -> MetricPanel(group, hourly, chartViews)
            kind is SleepSessionsEvidence -> SleepSessionsPanel(kind)
            kind is HourlyStepsEvidence -> HourlyStepsPanel(kind)
        }

        // 웹 창 맨 아래와 같은 테두리 닫기 버튼. 길게 내린 뒤에도 위로 올라가지 않고 닫을 수 있다
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Text(
                "닫기", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, textAlign = TextAlign.Center,
                modifier = Modifier.clip(RoundedCornerShape(12.dp)).background(Link.Background).border(1.dp, Link.Border, RoundedCornerShape(12.dp))
                    .clickable(role = Role.Button, onClick = onClose).height(44.dp).padding(horizontal = 24.dp).wrapContentHeight(Alignment.CenterVertically),
            )
        }
    }
    }
}

/**
 * 내린 만큼 왼쪽부터 차오르는 가로 막대(웹 근거 상세 창과 같은 방식). 창 맨 위에 고정해 전체 길이와 지금 위치를 알린다.
 * 내릴 내용이 없으면 두지 않는다. 스크롤 자체는 그대로라 보조기기에는 읽히지 않는 꾸밈이다.
 */
@Composable
private fun ScrollProgress(scroll: ScrollState) {
    if (scroll.maxValue <= 0) return
    val fraction = (scroll.value.toFloat() / scroll.maxValue).coerceIn(0f, 1f)
    Box(Modifier.fillMaxWidth().height(6.dp).background(Link.Pink).clearAndSetSemantics { }) {
        Box(Modifier.fillMaxWidth(fraction).fillMaxHeight().background(BarHigh, RoundedCornerShape(topEnd = 3.dp, bottomEnd = 3.dp)))
    }
}

@Composable
private fun PeriodHeader(caption: String, period: String, comparison: String? = null, recorded: String? = null, split: String? = null, overlaps: List<String> = emptyList()) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(caption, fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Sub)
        Text(period, fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Link.Text)
        comparison?.let { Text("비교 기간 $it", fontSize = 14.sp, color = Link.Sub) }
        split?.let { Text(it, fontSize = 14.sp, color = Link.Sub) }
        // 같은 지표를 겹치는 기간으로 한 번 더 조회한 근거. 차트는 위 기간으로 그리고 이 기간은 평균만 적는다(웹 418·439)
        overlaps.forEach { Text(it, fontSize = 14.sp, lineHeight = 20.sp, color = Link.Sub) }
        // 기간 묶음과 출처 묶음 사이를 한 단계 띄운다(웹은 두 묶음이 16 떨어져 있다)
        Spacer(Modifier.height(8.dp))
        recorded?.let { Text(it, fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Sub) }
        Text("출처 · 내 건강 기록 (CANDiY API)", fontSize = 13.sp, color = Link.Sub)
    }
}

@Composable
private fun SectionTitle(text: String, trailing: String? = null) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(text, fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.weight(1f).semantics { heading() })
        trailing?.let { Text(it, fontSize = 13.sp, color = Link.Sub) }
    }
}

/** 제목과 그 아래 내용 한 묶음. 사이는 12 */
@Composable
private fun Section(content: @Composable () -> Unit) = Column(verticalArrangement = Arrangement.spacedBy(12.dp)) { content() }

@Composable
internal fun Note(text: String) = Text(text, fontSize = 13.sp, lineHeight = 19.sp, color = Link.Sub)

/** 연회색 면에 테두리를 둔 상자. 요약 값을 담는다(웹 px-6 py-5) */
@Composable
private fun Panel(content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) { content() }
}

/** 줄 사이에 구분선이 있는 목록 상자(웹 divide-y · px-5). 위아래 여백은 각 줄이 갖는다 */
@Composable
private fun <T> RowsPanel(items: List<T>, row: @Composable (T) -> Unit) {
    Column(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(horizontal = 16.dp)) {
        items.forEachIndexed { index, item ->
            if (index > 0) HorizontalDivider(color = Link.Border)
            row(item)
        }
    }
}

/** 한 칸. [span]은 차지하는 칸 수 */
private class Cell(val span: Int = 1, val content: @Composable () -> Unit)

/**
 * 칸을 [columns]개씩 나눠 놓는 상자(웹 grid). 날짜별 기록은 3칸, 최저·최고와 수면 단계는 2칸으로 쓴다.
 * 자리가 모자란 칸은 다음 줄로 내려간다. 줄 수가 적어 지연 목록 없이 그대로 놓는다
 */
@Composable
private fun GridPanel(columns: Int, cells: List<Cell>) {
    val rows = remember(columns, cells) {
        val result = mutableListOf<MutableList<Cell>>()
        var used = columns
        cells.forEach { cell ->
            val span = cell.span.coerceAtMost(columns)
            if (used + span > columns) { result += mutableListOf<Cell>(); used = 0 }
            result.last() += cell
            used += span
        }
        result
    }
    Column(
        Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        rows.forEach { cellsInRow ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                cellsInRow.forEach { cell -> Box(Modifier.weight(cell.span.coerceAtMost(columns).toFloat())) { cell.content() } }
                val left = columns - cellsInRow.sumOf { it.span.coerceAtMost(columns) }
                if (left > 0) Spacer(Modifier.weight(left.toFloat()))
            }
        }
    }
}

/** 이름과 값 한 쌍. [size]는 값 글자 크기: 핵심 수치 28 · 요약 24 · 작은 칸 20(웹 text-metric · text-2xl · text-xl) */
@Composable
private fun Stat(title: String, value: String, caption: String? = null, accent: Boolean = false, size: Int = 24) {
    Column {
        Text(title, fontSize = 13.sp, lineHeight = 19.sp, fontWeight = FontWeight.Bold, color = Link.Text)
        Spacer(Modifier.height(if (size <= 20) 4.dp else 8.dp))
        Text(value, fontSize = size.sp, lineHeight = (size + 8).sp, fontWeight = FontWeight.Bold, color = if (accent) Link.Red else Link.Text)
        caption?.let { Spacer(Modifier.height(4.dp)); Text(it, fontSize = 12.sp, color = Link.Text) }
    }
}

/** 작은 칸: 윗줄 이름(13), 아랫줄 값(14). 날짜별 기록·가장 높은 날에 쓴다 */
@Composable
private fun SmallCell(label: String, value: String, valueBold: Boolean = true, note: String? = null) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, fontSize = 13.sp, lineHeight = 18.sp, color = Link.Text)
        Text(value, fontSize = 14.sp, lineHeight = 20.sp, fontWeight = if (valueBold) FontWeight.Bold else FontWeight.Normal, color = Link.Text)
        note?.let { Text(it, fontSize = 12.sp, color = Link.CoralMain) }
    }
}

/**
 * 목록 한 줄: 왼쪽 두 줄(윗줄·아랫줄), 오른쪽 값. 굵은 쪽이 그 줄의 이름이다.
 * [pill]은 연분홍 알약 표시("가장 길게"), [tag]는 면 없는 작은 글자("운동 미인정")
 */
@Composable
private fun ListRow(title: String, value: String, sub: String? = null, titleBold: Boolean = false, subBold: Boolean = true, pill: String? = null, tag: String? = null) {
    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(title, fontSize = if (titleBold) 14.sp else 13.sp, fontWeight = if (titleBold) FontWeight.Bold else FontWeight.Normal, color = Link.Text)
            sub?.let {
                Text(
                    it, fontSize = if (subBold) 14.sp else 13.sp, fontWeight = if (subBold) FontWeight.Bold else FontWeight.Normal,
                    color = Link.Text, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Spacer(Modifier.width(12.dp))
        pill?.let {
            Text(it, fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Link.Primary, modifier = Modifier.background(Link.Pink, RoundedCornerShape(50)).padding(horizontal = 8.dp, vertical = 2.dp))
            Spacer(Modifier.width(8.dp))
        }
        tag?.let { Text(it, fontSize = 12.sp, color = Link.Text); Spacer(Modifier.width(8.dp)) }
        Text(value, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text)
    }
}

/** 접었다 펴는 줄(웹의 details). 웹처럼 글은 그대로 두고 앞의 세모만 돌린다 */
@Composable
private fun Expander(label: String, content: @Composable () -> Unit) {
    var open by remember { mutableStateOf(false) }
    Column {
        Text(
            (if (open) "▾ " else "▸ ") + label, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Sub,
            modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button) { open = !open }
                .semantics { stateDescription = if (open) "펼침" else "접힘" }.padding(vertical = 4.dp),
        )
        if (open) { Spacer(Modifier.height(12.dp)); content() }
    }
}

/** 기록이 없을 때의 안내 상자 */
@Composable
private fun EmptyBox(text: String) = Text(
    text, fontSize = 14.sp, lineHeight = 21.sp, color = Link.Text, textAlign = TextAlign.Center,
    modifier = Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(horizontal = 24.dp, vertical = 32.dp),
)

/** 비교 기간의 평균. 차트에 실선으로 겹쳐 그리고 아래에 두 평균과 차이를 적는다. [reference]는 기록이 적어 참고용인 비교다 */
private data class PreviousAverage(val average: Double, val delta: String, val reference: Boolean)

@Composable
private fun MetricPanel(group: V.Group, hourly: HourlyStepsLoader?, chartViews: SnapshotStateMap<String, Boolean>) {
    val metric = group.metric
    val s = group.current.snapshot
    val label = V.METRIC_LABELS[metric] ?: metric
    val cover = remember(group) { V.coverage(group) }
    val merged = group.merged
    val compareCover = group.comparison?.let { V.coverage(it.snapshot) }
    val delta = V.averageDelta(group)
    val bars = remember(s) { V.chartBars(metric, s) }
    val showsChart = V.showsChart(s)
    // 산소포화도는 요약을 한 상자에 모은다(S15P31A403-384): 평균 아래에 측정 최저·최고를 함께 두고,
    // 따로 있던 "측정 최저·최고" 묶음과 "가장 높은 날·낮은 날" 칸은 같은 내용이 반복되어 두지 않는다
    val compactSummary = metric == "oxygen_saturations"
    val measured = V.measuredRange(metric, s)
    // 날짜별 기록 달력(웹 430~439). 걸음·운동·수면·심박은 기간이 이틀 이상이면 길이와 관계없이 달력이다.
    // 운동 기록·산소포화도·시간대별 걸음은 달력의 그날 상세에 넣어, 따로 있던 목록 묶음을 두지 않는다
    val calendarBase = remember(s) { V.calendarAverage(metric, s) }
    val showsCalendar = calendarBase != null && V.showsCalendar(metric, s, cover)
    val allBeforeLink = V.allBeforeLink(s)
    // 차트마다 고른 보기를 기억하는 이름. 지표와 근거가 같으면 같은 차트다
    val chartKey = "$metric:${group.current.id}"

    PeriodHeader(
        caption = if (cover.periodDays == 1) "하루 기록" else "${cover.periodDays}일 기간",
        period = V.period(group.current.periodStart, group.current.periodEnd),
        comparison = group.comparison?.let { V.period(it.periodStart, it.periodEnd) },
        split = if (merged) "${group.parts.size}번 나눠 조회한 기록을 이어서 보여 줘요" else null,
        // 다 기록된 기간은 "7일 / 7일"이 기간 표시와 같은 말이라 쓰지 않는다
        recorded = if (cover.periodDays > 1 && cover.recordedDays < cover.countedDays) "기록된 날 · ${cover.recordedDays}일 / ${cover.countedDays}일" else null,
        // 운동 탭에는 "함께 조회한 기간" 줄을 두지 않는다. AI 는 운동과 그날 밤 수면을 견주려고 하루 어긋난 두 기간을 함께 조회하는데,
        // 기간과 평균이 거의 같은 줄이 하나 더 보여 무엇을 말하는지 알기 어려웠다(발표 준비 때 받은 의견)
        overlaps = if (metric == "exercises") emptyList() else group.overlaps.map { "함께 조회한 기간 ${V.period(it.periodStart, it.periodEnd)} · 평균 ${V.value(metric, it.snapshot.avg)}" },
    )

    if (merged) {
        // 나눠 조회한 기간마다 AI 가 받은 평균을 그대로 보여 준다. 전체 평균은 앱에서 다시 내지 않는다.
        // "주별로 알려 줘" 같은 답변의 숫자를 여기서 바로 맞춰 볼 수 있다
        Section {
            SectionTitle("조회한 기간별 평균")
            RowsPanel(group.parts) { part ->
                val partCover = V.coverage(part.snapshot)
                ListRow(
                    title = V.period(part.periodStart, part.periodEnd),
                    sub = if (partCover.recordedDays < partCover.countedDays) "${partCover.countedDays}일 중 ${partCover.recordedDays}일 기록" else null,
                    value = V.value(metric, part.snapshot.avg),
                    titleBold = true, subBold = false,
                )
            }
        }
    } else if (cover.hasAnyValue || group.comparison != null) {
        // 기록이 없고 비교도 없으면 "-" 카드 대신 아래 안내만 둔다.
        // 휴대폰 폭의 웹처럼 값 셋을 같은 크기로 세로로 놓고 사이에 선을 두지 않는다
        // 산소포화도는 상자 위에 지표 이름을 한 번만 쓰고 상자 안은 "평균·최저·최고"로 줄인다(이름이 길면 좁은 칸에서 두 줄이 된다)
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (compactSummary) SectionTitle("산소포화도")
            Panel {
                val today = cover.today
                // 산소포화도는 평균·최저·최고를 한 상자에 나란히 둔다(S15P31A403-384). 최저·최고가 측정된 날짜는 값 아래에 쓴다.
                // 비교 기간이 있어도 상자를 나누지 않고 평균·이전 기간 대비·최저·최고 네 칸을 두 줄에 둔다(웹 404, 휴대폰 폭은 두 칸씩)
                val todayOnly = cover.recordedDays == 0 && today != null
                val oneRow = compactSummary && measured != null && !todayOnly
                val compared = group.comparison != null && compareCover != null
                // 이전 기간 평균 칸은 두지 않고 차이만 보여 준다(웹 393). 비교 기간은 머리말에 있다.
                // AI 와 같은 기준(3일 이상·절반 이상)에 못 미치면 차이가 커 보여도 믿기 어려워 강조색을 빼고 참고용이라고 붙인다
                val deltaStat: @Composable (Int) -> Unit = { size ->
                    val reference = delta != null && compareCover != null && !(cover.isReliable && compareCover.isReliable)
                    Stat(
                        if (reference) "이전 기간 대비 · 참고용" else "이전 기간 대비",
                        if (delta == null) "-" else V.delta(metric, delta),
                        when {
                            reference -> if (cover.periodDays == 1 && compareCover?.periodDays == 1) "하루 기록끼리 비교했어요" else "기록이 적은 기간이 섞여 있어요"
                            compareCover?.recordedDays == 0 -> "이전 기간 기록 없음"
                            else -> null
                        },
                        accent = !reference, size = size,
                    )
                }
                if (oneRow) {
                    val lowest: @Composable () -> Unit = { Stat("최저", V.value(metric, measured?.first?.value), measured?.first?.let { V.dayWithWeekday(it.date) }, size = 20) }
                    val highest: @Composable () -> Unit = { Stat("최고", V.value(metric, measured?.second?.value), measured?.second?.let { V.dayWithWeekday(it.date) }, size = 20) }
                    if (compared) {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(Modifier.weight(1f)) { Stat("평균", V.value(metric, s.avg), size = 20) }
                            Box(Modifier.weight(1f)) { deltaStat(20) }
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(Modifier.weight(1f)) { lowest() }
                            Box(Modifier.weight(1f)) { highest() }
                        }
                    } else {
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Box(Modifier.weight(1f)) { Stat("평균", V.value(metric, s.avg), size = 20) }
                            Box(Modifier.weight(1f)) { lowest() }
                            Box(Modifier.weight(1f)) { highest() }
                        }
                    }
                } else {
                    // 평균과 "이전 기간 대비" 두 칸을 가로로 나란히 둔다. 앞 칸은 글자 폭만큼만 차지해 휴대폰에서도 한 줄에 들어간다(웹과 같다)
                    Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                        Box(if (compared) Modifier else Modifier.weight(1f)) {
                            if (todayOnly && today != null) {
                                Stat("오늘 ${V.DAY_LABELS[metric]}", V.value(metric, today.value), today.asOf?.let { "${V.clock(it)} 기준 · 수집 중" } ?: "수집 중")
                            } else {
                                Stat(
                                    V.statTitle(group.current, cover, V.AVERAGE_LABELS[metric] ?: label), V.value(metric, s.avg),
                                    if (cover.recordedDays == 0 && cover.periodDays > 1) "기록 없음" else null,
                                )
                            }
                        }
                        if (compared) Box(Modifier.weight(1f)) { deltaStat(24) }
                    }
                }
                if (compactSummary && measured != null && !oneRow) {
                    val (minimum, maximum) = measured
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Box(Modifier.weight(1f)) { Stat("최저", V.value(metric, minimum?.value), minimum?.let { V.dayWithWeekday(it.date) }, size = 20) }
                        Box(Modifier.weight(1f)) { Stat("최고", V.value(metric, maximum?.value), maximum?.let { V.dayWithWeekday(it.date) }, size = 20) }
                    }
                }
            }
        }
    }

    val skipReason = V.chartSkipReason(s)
    if (showsChart) {
        val title = (V.CHART_TITLES[metric] ?: label).let {
            if (bars.weekly) it.replace("일별", if (metric in BODY_METRICS) "주별 마지막 측정" else "주별 평균") else it
        }
        Section {
            SectionTitle(title, "단위 · ${V.UNIT_LABELS[metric]}")
            val previousAverage = group.comparison?.snapshot?.avg
            BarChart(
                metric = metric, bars = bars.bars, weekly = bars.weekly, title = title,
                extremes = remember(s, merged) { V.extremes(metric, bars, s, merged) },
                average = if (!merged && cover.recordedDays >= V.MIN_DAYS_FOR_TREND) s.avg else null,
                // 꺾은선 차트(산소포화도)에는 이전 기간 평균선을 두지 않는다. 비교는 위 요약 상자의 "이전 기간 대비"로 본다(웹과 같다)
                previous = if (!V.isLineChart(metric) && previousAverage != null && delta != null && compareCover != null) {
                    PreviousAverage(previousAverage, V.delta(metric, delta), reference = !(cover.isReliable && compareCover.isReliable))
                } else null,
                whole = chartViews[chartKey] == true,
                onWhole = { chartViews[chartKey] = it },
            )
        }
    } else if (skipReason != null) {
        // 값은 있는데 차트가 없을 때 빠진 게 아니라는 걸 알린다(하루 기간·기록 하루·모두 0)
        Note(skipReason)
    } else if (!cover.hasAnyValue) {
        // 물은 기간이 통째로 앱 연동 전이면 그렇게 알린다. 전에는 "기록이 없어요" 아래에 "1일은 앱 연동 전이라 기간에서 뺐어요"가 붙어,
        // 하루를 물었는데 그 하루를 뺐다는 말이 되었다(웹 439)
        if (allBeforeLink) EmptyBox("앱을 연동하기 전이라 $label 기록이 없어요.")
        else EmptyBox("이 기간에는 $label 기록이 없어요." + if (s.unsyncedDays > 0) "\n아직 동기화되지 않은 날이 있어요. 홈 탭에서 동기화해 주세요." else "")
    }

    // 답변이 하루 평균 말고 측정 최고·수면 단계·운동 기록을 말해도 근거에서 찾을 수 있게 한다
    measured?.takeIf { !compactSummary }?.let { (minimum, maximum) ->
        Section {
            SectionTitle("측정 최저·최고")
            GridPanel(2, listOf(
                Cell { Stat("측정 최저" + (minimum?.let { " · ${V.dayWithWeekday(it.date)}" } ?: ""), V.value(metric, minimum?.value), size = 20) },
                Cell { Stat("측정 최고" + (maximum?.let { " · ${V.dayWithWeekday(it.date)}" } ?: ""), V.value(metric, maximum?.value), size = 20) },
            ))
            Note(V.measuredNote(cover.periodDays))
        }
    }

    // 하루 수면 기록에는 달력이 없어 잠든·깬 시각을 볼 곳이 없었다. 답변은 "밤 11시 37분에 잠들어 7시 19분에 깼다"고 말한다(웹 439).
    // 여러 날이면 달력의 그날 상세에 있다
    if (metric == "sleeps" && !showsCalendar && s.nights.size == 1) {
        val night = s.nights.first()
        Section {
            SectionTitle("잠든·깬 시각")
            GridPanel(2, listOf(
                Cell { Stat("잠든 시각", V.clock(night.startAt), size = 20) },
                Cell { Stat("깬 시각", night.endAt?.let { V.clock(it) } ?: "-", size = 20) },
            ))
        }
    }

    val stages = remember(s) { V.sleepStageAverages(s) }
    if (stages.isNotEmpty()) {
        val counted = stages.maxOf { it.nights }
        val skipped = s.nights.size - counted
        Section {
            // 단계 기록이 하루뿐이면 평균이 아니라 그날 값이라 "평균"을 붙이지 않는다(웹 420)
            SectionTitle(if (counted == 1) "수면 단계" else "수면 단계 평균")
            GridPanel(2, stages.map { stage -> Cell { Stat(stage.label, V.value("sleeps", stage.minutes), size = 20) } })
            Note(V.stageNote(cover.periodDays, counted, skipped))
        }
        // 날짜별 수면 단계 차트(웹 SleepStageBars). 밤마다 깊은 잠·렘수면·얕은 잠을 쌓아 그린다. 긴 기간도 주로 묶지 않고 밤마다 그린다
        if (counted >= V.MIN_DAYS_FOR_TREND) {
            val columns = remember(s) { V.stageColumns(if (bars.weekly) V.dayBars(s) else bars.bars, s.nights) }
            Section {
                SectionTitle("날짜별 수면 단계")
                StageChart(columns, whole = chartViews["$chartKey:stages"] == true) { chartViews["$chartKey:stages"] = it }
            }
        }
    }

    // 달력이 있으면 밤별 산소포화도·운동 횟수 표·운동 기록은 그날 상세에 있어 아래 묶음을 두지 않는다
    val spo2Nights = remember(s) { V.nightSpo2(s) }
    if (spo2Nights.isNotEmpty() && !showsCalendar) {
        Section {
            SectionTitle("수면 중 산소포화도")
            val summary = remember(s) { V.spo2Summary(s) }
            if (summary == null) {
                Spo2NightList(spo2Nights)
            } else {
                // 밤이 많으면(한 달 등) 평균·최저·최고를 먼저 보여 주고, 밤별 목록은 날짜별 전체 보기처럼 접어 둔다
                fun percent(value: Double?) = V.value("oxygen_saturations", value)
                GridPanel(3, listOf(
                    Cell { Stat("평균", percent(summary.average), size = 20) },
                    Cell { Stat("최저", percent(summary.lowest?.value), summary.lowest?.let { V.dayWithWeekday(it.date) }, size = 20) },
                    Cell { Stat("최고", percent(summary.highest?.value), summary.highest?.let { V.dayWithWeekday(it.date) }, size = 20) },
                ))
                Note(V.spo2SummaryNote(summary))
                Expander("날짜별 전체 보기 (${summary.nights}일)") { Spo2NightList(spo2Nights) }
            }
        }
    }

    // 운동 횟수·칼로리·거리(S15P31A403-375). 답변이 운동 시간 말고 이 값을 말해도 근거에서 찾을 수 있다
    val activity = remember(s) { V.activity(s) }
    if (activity != null && !showsCalendar) {
        Section {
            SectionTitle("운동 횟수·칼로리·거리")
            if (activity.cards.isNotEmpty()) {
                GridPanel(2, activity.cards.map { card -> Cell { Stat(card.label, card.value, card.captions.joinToString("\n"), size = 20) } })
            }
            // 하루 기간은 위 카드와 같은 값 하나뿐이라 표를 두지 않는다
            if (activity.rows.size > 1) {
                if (activity.rows.size <= V.MAX_LISTED_ACTIVITY_DAYS) ActivityTable(activity.rows)
                else Expander("날짜별 횟수·칼로리·거리 보기 (${activity.rows.size}일)") { ActivityTable(activity.rows) }
            }
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) { activity.notes.forEach { Note(it) } }
        }
    }

    val sessions = remember(s) { V.sessionItems(s) }
    if (sessions.isNotEmpty() && !showsCalendar) {
        Section {
            SectionTitle("운동 기록 · ${sessions.size}개")
            SessionList(sessions.take(V.MAX_LISTED_SESSIONS))
            if (sessions.size > V.MAX_LISTED_SESSIONS) {
                Expander("나머지 ${sessions.size - V.MAX_LISTED_SESSIONS}개 보기") { SessionList(sessions.drop(V.MAX_LISTED_SESSIONS)) }
            }
            if (sessions.any { !it.session.countsAsExercise }) Note("운동으로 인정되지 않은 기록은 운동 시간에서 뺐어요.")
        }
    }

    // 하루 기간은 위 카드와 같은 값 하나뿐이라 목록을 두지 않는다
    if (cover.hasAnyValue && cover.periodDays > 1) {
        Section {
            SectionTitle("날짜별 기록")
            if (showsCalendar && calendarBase != null) {
                DayCalendar(metric, s, calendarBase, hourly)
            } else {
                val items = remember(s) { V.dayItems(s.days) }
                if (V.listsAllDays(s)) {
                    DayList(items, metric)
                } else {
                    // 긴 기간은 가장 높은·낮은 날과 기록 없는 날만 먼저 보여 준다. 산소포화도는 위 요약·차트와 겹쳐 접는 줄만 둔다
                    if (!compactSummary) GridPanel(2, V.dayHighlights(metric, s).map {
                        // 이름이 길어져("가장 많이 걸은 날") 반 칸에 "· 날짜"까지 넣으면 가운뎃점 뒤에서 줄이 끊겼다(S15P31A403-427).
                        // 이름과 날짜를 처음부터 두 줄로 나눠 칸마다 같은 모양이 되게 한다
                        Cell { SmallCell(it.label + "\n" + V.dayWithWeekday(it.date), V.value(metric, it.value)) }
                    } +
                        if (cover.countedDays > cover.recordedDays) listOf(Cell { SmallCell("기록 없는 날", "${cover.countedDays - cover.recordedDays}일") }) else emptyList())
                    Expander("날짜별 전체 보기 (${cover.periodDays}일)") { DayList(items, metric) }
                }
            }
        }
    }

    // "선택한 기간의 ○○ 기록이에요"는 위 기간 머리말과 겹치는 말이라 두지 않는다(웹 438)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (merged) Note("전체 평균은 따로 계산하지 않고, 조회한 기간별 평균을 그대로 보여 줘요.")
        if (cover.recordedDays > 1 && cover.recordedDays < cover.countedDays) Note("기록 없는 날은 0으로 치지 않고 평균에서 뺐어요.")
        if (s.days.any { it.inProgress } && cover.periodDays > 1) Note("오늘 기록은 수집 중이며 기간 평균에서 제외해요.")
        if (!allBeforeLink) V.beforeLinkDays(s).takeIf { it > 0 }?.let { Note("${it}일은 앱 연동 전이라 기간에서 뺐어요.") }
        if (bars.weekly && showsChart) {
            Note(if (metric in BODY_METRICS) "기간이 길어 차트는 7일씩 묶어 그 주 마지막 측정값으로 보여 줘요." else "기간이 길어 차트는 7일씩 묶은 하루 평균으로 보여 줘요.")
        }
    }
}

/**
 * 날짜별 기록. 웹 휴대폰 폭과 같이 한 줄에 세 칸씩 놓는다(윗줄 날짜, 아랫줄 값).
 * 이어지는 기록 없는 날은 한 칸으로 묶고, 이틀 이상이면 두 칸을 쓴다. "수집 중"은 값 아래에 코랄색으로 둔다
 */
@Composable
private fun DayList(items: List<V.DayItem>, metric: String) {
    GridPanel(3, items.map { item ->
        when (item) {
            is V.DayItem.Day -> Cell { SmallCell(V.dayWithWeekday(item.date), V.value(metric, item.value), note = if (item.inProgress) "수집 중" else null) }
            is V.DayItem.Gap -> Cell(span = if (item.count > 1) 2 else 1) {
                SmallCell(
                    if (item.count > 1) "${V.shortDate(item.start)} ~ ${V.shortDate(item.end)}" else V.dayWithWeekday(item.start),
                    (V.MISSING_REASON_LABELS[item.reason] ?: "기록 없음") + if (item.count > 1) " · ${item.count}일" else "",
                    valueBold = false,
                )
            }
        }
    })
}

/**
 * 날짜별 산소포화도 표(웹 404와 같다): 날짜 · 그 밤의 평균 · 90% 미만.
 * 밤마다 두 줄이던 목록을 한 줄로 줄였다. "산소포화도 90% 미만"은 줄마다 되풀이하지 않고 머리글에 한 번만 쓴다
 */
@Composable
private fun Spo2NightList(nights: List<V.NightSpo2>) {
    val dateWidth = 84.dp
    val averageWidth = 60.dp
    Column(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape)) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
            Text("날짜", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.width(dateWidth))
            Text("평균", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.width(averageWidth))
            Text("90% 미만", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.weight(1f))
        }
        nights.forEach { night ->
            HorizontalDivider(color = Link.Border)
            Row(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.Top) {
                Text(V.dayWithWeekday(night.date), fontSize = 14.sp, lineHeight = 19.sp, fontWeight = FontWeight.SemiBold, color = Link.Text, maxLines = 1, softWrap = false, modifier = Modifier.width(dateWidth))
                // 그 밤의 평균. 90% 미만 여부만으로는 평소 수준을 알 수 없다. 평균이 없는 밤은 "-"로 둔다
                Text(night.average?.let { V.value("oxygen_saturations", it) } ?: "-", fontSize = 14.sp, lineHeight = 19.sp, color = Link.Text, maxLines = 1, softWrap = false, modifier = Modifier.width(averageWidth))
                Box(Modifier.weight(1f)) { Spo2Bare(night.state) }
            }
        }
    }
}

/**
 * 산소포화도 90% 미만을 이름 없이 값만 쓰는 칸(웹 Spo2LowTimes 의 bare). 이름은 표 머리글이나 줄 이름에 따로 있다.
 * 시각이 많으면 앞의 몇 개만 두고 "외 N회 보기"를 붙인다. 누르면 앞 몇 개를 다시 쓰지 않고 아래 줄에 전체 시각을 편다.
 * [end]는 오른쪽 정렬(달력 상세), [bold]는 값 전체를 굵게(달력 상세의 다른 값과 같게)
 */
@Composable
internal fun Spo2Bare(state: V.Spo2Low, end: Boolean = false, bold: Boolean = false) {
    val times = (state as? V.Spo2Low.Times)?.times
    val rest = (times?.size ?: 0) - V.SPO2_TIMES_PREVIEW
    var expanded by remember(state) { mutableStateOf(false) }
    val weight = if (bold) FontWeight.Bold else FontWeight.Normal
    val align = if (end) TextAlign.End else TextAlign.Start
    Column(horizontalAlignment = if (end) Alignment.End else Alignment.Start) {
        Text(
            buildAnnotatedString {
                when {
                    times == null -> append(V.spo2Bare(state))
                    rest <= 0 || !expanded -> {
                        val text = V.spo2Bare(state, expanded = false)
                        val count = text.substringBefore(" · ")
                        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(count) }
                        append(text.removePrefix(count))
                        // 줄이 바뀔 때 "외 2회"와 "보기"가 갈라지지 않게 사이를 끊기지 않는 빈칸으로 둔다
                        if (rest > 0) withStyle(SpanStyle(color = Link.Primary, fontWeight = FontWeight.Bold)) { append(" 외 ${rest}회 보기") }
                    }
                    else -> {
                        withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append("${times.size}회") }
                        withStyle(SpanStyle(color = Link.Primary, fontWeight = FontWeight.Bold)) { append(" 접기") }
                    }
                }
            },
            fontSize = 13.sp, lineHeight = 19.sp, fontWeight = weight, color = Link.Text, textAlign = align,
            modifier = if (rest > 0) {
                Modifier.clickable(role = Role.Button) { expanded = !expanded }.semantics { stateDescription = if (expanded) "펼침" else "접힘" }
            } else Modifier,
        )
        if (expanded && times != null) {
            Spacer(Modifier.height(4.dp))
            Text(times.joinToString(", ") { V.clock(it) }, fontSize = 13.sp, lineHeight = 20.sp, fontWeight = weight, color = Link.Text, textAlign = align)
        }
    }
}

/**
 * 날짜별 횟수·칼로리·거리 표. 휴대폰 폭에 네 열이 들어가게 열 폭을 내용 길이에 맞춰 나눈다(날짜·칼로리가 넓고 횟수가 좁다).
 * "수집 중"은 날짜 아래 줄에 코랄색으로 둔다(웹 휴대폰 폭과 같다)
 */
@Composable
private fun ActivityTable(rows: List<V.ActivityRow>) {
    val weights = listOf(0.9f, 1.15f, 0.95f)
    Column(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape)) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 12.dp)) {
            Text("날짜", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.weight(1.2f))
            listOf("횟수", "칼로리", "거리").forEachIndexed { i, head ->
                Text(head, fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text, textAlign = TextAlign.End, modifier = Modifier.weight(weights[i]))
            }
        }
        rows.forEach { row ->
            HorizontalDivider(color = Link.Border)
            Row(Modifier.padding(horizontal = 12.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1.2f)) {
                    Text(V.dayWithWeekday(row.date), fontSize = 13.sp, fontWeight = FontWeight.SemiBold, color = Link.Text, maxLines = 1, softWrap = false)
                    if (row.inProgress) Text("수집 중", fontSize = 12.sp, color = Link.CoralMain)
                }
                row.cells.forEachIndexed { i, cell ->
                    Text(cell, fontSize = 13.sp, color = Link.Text, textAlign = TextAlign.End, maxLines = 1, softWrap = false, modifier = Modifier.weight(weights[i]))
                }
            }
        }
    }
}

@Composable
private fun SessionList(items: List<V.SessionItem>) {
    RowsPanel(items) { item ->
        val session = item.session
        ListRow(
            title = "${V.dayWithWeekday(session.date)} · ${V.clock(session.startAt)}",
            sub = session.title ?: session.typeName,
            value = V.value("exercises", session.durationMinutes),
            pill = if (item.isLongest) "가장 길게" else null,
            tag = if (!item.isLongest && !session.countsAsExercise) "운동 미인정" else null,
        )
    }
}

/** 수면 중 산소포화도 90% 미만 한 줄. 시각이 많으면 앞의 몇 개만 두고 눌러서 전체를 편다 */
@Composable
private fun Spo2Line(state: V.Spo2Low) {
    var expanded by remember(state) { mutableStateOf(false) }
    val rest = (state as? V.Spo2Low.Times)?.let { it.times.size - V.SPO2_TIMES_PREVIEW } ?: 0
    Column {
        Text(V.spo2Text(state, expanded), fontSize = 13.sp, lineHeight = 19.sp, color = Link.Text)
        if (rest > 0) {
            Text(
                if (expanded) "접기" else "외 ${rest}회 보기", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Primary,
                modifier = Modifier.clickable(role = Role.Button) { expanded = !expanded }.padding(vertical = 4.dp),
            )
        }
    }
}

/**
 * 날짜별 차트. 왼쪽 눈금 글자, 가로 눈금선, 평균 점선, 막대, 아래 날짜 글자(처음·가운데·끝)로 이룬다.
 * 산소포화도는 막대 대신 꺾은선과 점으로 그리고 축을 90에서 시작한다(V.isLineChart, S15P31A403-384).
 * 막대나 점이 있는 칸을 누르면 차트 위에 그 날짜와 값을 보여 준다(웹은 마우스를 올리면 나오는 설명에 해당).
 * 보조기기에는 차트 전체를 날짜별 값 목록 한 줄로 읽어 준다.
 */
@Composable
private fun BarChart(
    metric: String, bars: List<V.Bar>, weekly: Boolean, average: Double?, extremes: List<V.Extreme>, title: String,
    previous: PreviousAverage?, whole: Boolean, onWhole: (Boolean) -> Unit,
) {
    // 이전 기간 평균선도 축 안에 들어오게 눈금을 잡는다
    val scale = remember(bars, average, previous) { V.chartScale(metric, bars.map { it.value } + listOfNotNull(previous?.average), average) }
    var selected by remember(bars) { mutableStateOf<String?>(null) }
    val description = remember(bars) {
        "$title. " + bars.joinToString(", ") { b -> "${b.title} ${if (b.value == null) "기록 없음" else V.value(metric, b.value)}${if (b.inProgress) " 수집 중" else ""}" }
    }
    val line = V.isLineChart(metric)
    // 일별 차트는 한 화면에 일주일(7칸)을 보여 주고, 더 길면 가로로 민다(웹과 같은 공통 규칙, S15P31A403-425).
    // 오른쪽 위의 보기 전환으로 기간 전체를 한 화면에 볼 수도 있다. 주 단위로 묶은 막대는 밀지 않는다
    val available = !weekly && bars.size > V.VISIBLE_DAYS
    // 보기(일주일·기간 전체)는 근거 창이 차트마다 기억한다. 탭을 옮겼다 돌아와도 고른 보기가 남는다(웹 439)
    val scrolls = available && !whole
    // 막대가 많으면 휴대폰 폭에 들어가도록 사이 간격을 줄인다. 미는 차트는 칸 폭이 고정이라 간격을 두지 않는다
    val gap: Dp = if (scrolls) 0.dp else if (bars.size > 14) 1.dp else 8.dp
    val scroll = rememberScrollState()
    // 차트가 보이는 폭(px). 이 폭을 7로 나눈 값이 하루 칸의 폭이다
    var viewport by remember { mutableStateOf(0) }
    // 처음에는 값이 있는 가장 최근 일주일이 보이게 한다. 내용 폭은 배치가 끝나야 알 수 있어 그때 한 번만 옮긴다
    var placed by remember(bars, whole) { mutableStateOf(false) }
    LaunchedEffect(scroll.maxValue, viewport, bars, whole) {
        if (scrolls && !placed && scroll.maxValue > 0 && viewport > 0) {
            scroll.scrollTo((V.firstVisible(bars) * viewport / V.VISIBLE_DAYS).coerceAtMost(scroll.maxValue))
            placed = true
        }
    }
    // 차트를 밀면 팝업이 가리키던 막대가 자리를 옮기므로 닫는다
    LaunchedEffect(scroll.isScrollInProgress) { if (scroll.isScrollInProgress) selected = null }
    // 꼬리표는 늘 기간 전체의 가장 높은 날과 낮은 날에 고정한다(S15P31A403-428). 427 에서는 일주일씩 볼 때 보이는 일주일 안에서 골랐는데,
    // 넘길 때마다 꼬리표가 다른 막대로 옮겨 가 기준이 흔들렸다. 이제 그 날이 화면에 있을 때 보이고 넘기면 함께 지나간다

    Column(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).padding(start = 12.dp, end = 12.dp, top = 16.dp, bottom = 12.dp)) {
        // 범례와 보기 전환을 한 줄에 둔다(웹과 같다). 처음에는 전환 버튼이 윗줄에 혼자 있고 범례가 아랫줄에 있어 줄이 어긋나 보였다.
        // 범례가 네 개면(기록 없음·이전 기간 평균이 더해질 때) 휴대폰 폭 한 줄에 다 들어가지 않아 범례만 줄을 바꾼다.
        // 그때도 전환 버튼은 범례 첫 줄과 높이를 맞춘다. 두 줄 사이 가운데에 두면 어느 줄과도 맞지 않았다.
        // 버튼이 범례 한 줄보다 높아 범례를 3 내린다(실기기에서 두 가운데가 1px 안으로 맞는 값)
        Row(verticalAlignment = Alignment.Top, modifier = Modifier.fillMaxWidth()) {
            Box(Modifier.weight(1f).padding(top = if (available) 3.dp else 0.dp)) {
                ChartLegend(
                    tones = !line && average != null,
                    empty = !line && bars.any { it.value == null || it.value == 0.0 },
                    average = average != null,
                    previous = previous != null,
                    line = line,
                )
            }
            if (available) {
                Spacer(Modifier.width(8.dp))
                RangeToggle(whole, V.wholeViewLabel(bars.size)) { onWhole(it); selected = null }
            }
        }
        // 막대 위의 꼬리표("최고"·"최저")와 "오늘" 글자가 범례에 닿지 않게 띄운다.
        // 고른 막대의 날짜와 값은 이 자리의 한 줄 글 대신 막대 옆 팝업으로 보여 준다(날짜별 수면 단계 차트와 같은 방식)
        Spacer(Modifier.height(26.dp))

        Row(Modifier.fillMaxWidth().clearAndSetSemantics { contentDescription = description }) {
            // 왼쪽 눈금 글자. 눈금선과 같은 높이에 글자 가운데를 맞춘다. 차트를 밀어도 눈금은 제자리에 있다
            BoxWithConstraints(Modifier.width(AxisWidth).height(ChartHeight)) {
                scale.ticks.forEach { tick ->
                    val y = maxHeight * (1f - scale.fraction(tick))
                    Text(
                        V.axisValue(metric, tick), fontSize = 12.sp, lineHeight = 16.sp, color = Link.Text, textAlign = TextAlign.End, maxLines = 1, softWrap = false,
                        modifier = Modifier.fillMaxWidth().offset(y = y - 8.dp),
                    )
                }
            }
            Spacer(Modifier.width(AxisGap))
            BoxWithConstraints(Modifier.weight(1f).onSizeChanged { viewport = it.width }) {
                // 미는 차트는 보이는 폭을 7칸으로 나눈 칸 폭으로 전체 폭을 정한다. 아니면 보이는 폭이 곧 전체 폭이다
                val contentWidth = if (scrolls) maxWidth / V.VISIBLE_DAYS * bars.size else maxWidth
                // 꼬리표가 덮는 양옆 칸 수. 칸이 꼬리표보다 좁으면(기간 전체 보기) 양옆의 막대 위로 올린다
                val reach = if (bars.isEmpty()) 0 else ((ExtremeWidth / 2 - contentWidth / bars.size / 2) / (contentWidth / bars.size)).let { kotlin.math.ceil(it).toInt() }.coerceAtLeast(0)
                Column(if (scrolls) Modifier.horizontalScroll(scroll) else Modifier) {
                    Box(Modifier.width(contentWidth).height(ChartHeight)) {
                        Canvas(Modifier.fillMaxSize()) {
                            scale.ticks.forEach { tick ->
                                val y = size.height * (1f - scale.fraction(tick))
                                drawLine(Link.Border, Offset(0f, y), Offset(size.width, y), strokeWidth = 1.dp.toPx())
                            }
                        }
                        if (line) {
                            // 꺾은선과 점. 칸의 가운데를 잇고, 기록이 없는 날에서는 선을 끊는다(없는 값을 이어 그리지 않는다)
                            Canvas(Modifier.fillMaxSize()) {
                                val gapPx = gap.toPx()
                                val column = (size.width - gapPx * (bars.size - 1)) / bars.size
                                fun point(index: Int, value: Double) = Offset(index * (column + gapPx) + column / 2, size.height * (1f - scale.fraction(value)))
                                val radius = (if (!scrolls && bars.size > 14) 3.dp else 4.dp).toPx()
                                bars.forEachIndexed { index, bar ->
                                    val value = bar.value ?: return@forEachIndexed
                                    val next = bars.getOrNull(index + 1)?.value ?: return@forEachIndexed
                                    drawLine(BarHigh, point(index, value), point(index + 1, next), strokeWidth = 2.dp.toPx(), cap = StrokeCap.Round)
                                }
                                bars.forEachIndexed { index, bar ->
                                    val value = bar.value ?: return@forEachIndexed
                                    val center = point(index, value)
                                    // 고른 점은 바깥에 연한 고리를 둔다
                                    if (bar.key == selected) drawCircle(BarLowest, radius * 2.2f, center)
                                    if (bar.inProgress) {
                                        // 수집 중인 오늘은 통계에서 빠지므로 채우지 않고 테두리만 둔다
                                        drawCircle(Link.Background, radius, center)
                                        drawCircle(BarNormal, radius, center, style = Stroke(2.dp.toPx()))
                                    } else {
                                        drawCircle(BarHigh, radius, center)
                                    }
                                }
                            }
                        }
                        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(gap), verticalAlignment = Alignment.Bottom) {
                            bars.forEachIndexed { barIndex, bar ->
                                // 칸 전체가 눌린다(막대가 가늘거나 값이 없는 날도 누르기 쉽게). 눌린 표시는 두지 않고 고른 막대에 테두리를 그린다
                                Box(
                                    Modifier.weight(1f).fillMaxHeight()
                                        .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) { selected = if (selected == bar.key) null else bar.key },
                                    contentAlignment = Alignment.BottomCenter,
                                ) {
                                    val value = bar.value
                                    // 가장 높은 칸과 낮은 칸의 꼬리표. 막대 꼭대기 바로 위에 둔다(값은 눌렀을 때와 날짜별 기록에 있다)
                                    val extreme = extremes.firstOrNull { it.index == barIndex }
                                    if (extreme != null && value != null) {
                                        val lift = V.extremeLift(bars, barIndex, reach) ?: value
                                        ExtremeLabel(extreme.highest, Modifier.align(Alignment.BottomCenter).offset(y = -(ChartHeight * scale.fraction(lift) + 3.dp)))
                                    }
                                    // 꺾은선 차트는 칸을 누르는 영역으로만 쓰고 막대를 그리지 않는다
                                    if (!line) {
                                        // 미는 차트는 칸 사이 간격이 없어 막대끼리 붙는다. 칸 안에서 좌우를 조금 비워 막대를 띄운다
                                        val base = Modifier.padding(horizontal = if (scrolls) 4.dp else 0.dp).widthIn(max = 48.dp).fillMaxWidth()
                                        if (value == null || value == 0.0) {
                                            // 값이 없거나 0인 날은 점선 칸으로 둔다. 비워 두면 그 날이 화면에서 사라져 보인다(웹과 같다)
                                            EmptyDayBox(base)
                                        } else {
                                            val fraction = scale.fraction(value)
                                            // 수집 중인 오늘도 다른 날과 같은 채운 막대로 그리고, 막대 위에 "오늘"이라고만 적는다.
                                            // 예전에는 점선 테두리로 따로 그렸는데 기록 없는 날의 점선 칸과 헷갈렸다
                                            if (bar.inProgress) {
                                                Text(
                                                    "오늘", fontSize = 10.sp, lineHeight = 12.sp, color = Link.Text, maxLines = 1, softWrap = false,
                                                    modifier = Modifier.align(Alignment.BottomCenter).offset(y = -(ChartHeight * fraction + 3.dp)).wrapContentWidth(unbounded = true),
                                                )
                                            }
                                            val color = when (V.barTone(value, average)) { V.Tone.HIGH -> BarHigh; V.Tone.NORMAL -> BarLow; V.Tone.PLAIN -> BarNormal }
                                            Box(
                                                base.fillMaxHeight(fraction).background(color, RoundedCornerShape(6.dp))
                                                    .then(if (bar.key == selected) Modifier.border(2.dp, Link.Text, RoundedCornerShape(6.dp)) else Modifier),
                                            )
                                        }
                                    }
                                }
                            }
                        }
                        if (average != null) {
                            Canvas(Modifier.fillMaxSize()) {
                                val y = size.height * (1f - scale.fraction(average))
                                // 꺾은선과 같은 색이면 겹쳐 보여, 꺾은선 차트의 평균선은 코랄로 둔다
                                drawLine(if (line) BarNormal else BarHigh, Offset(0f, y), Offset(size.width, y), strokeWidth = 1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f)))
                            }
                        }
                        // 이전 기간 평균은 보조 글자색 실선으로 겹쳐 그린다(웹 404)
                        if (previous != null) {
                            Canvas(Modifier.fillMaxSize()) {
                                val y = size.height * (1f - scale.fraction(previous.average))
                                drawLine(Link.Muted, Offset(0f, y), Offset(size.width, y), strokeWidth = 1.5.dp.toPx())
                            }
                        }
                    }
                    // 아래 날짜 글자. 칸보다 넓을 수 있어 칸 폭을 늘리지 않고 가운데에 겹쳐 둔다. 차트와 함께 밀린다
                    Row(Modifier.width(contentWidth).padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(gap)) {
                        bars.forEachIndexed { index, bar ->
                            // 주 단위 막대의 기간 글자("11.11 ~ 11.17")는 칸보다 훨씬 넓다. 가운데에 두면 마지막 글자가 상자 밖으로 넘쳐(실기기에서 약 15dp),
                            // 마지막 칸만 칸의 오른쪽 끝에 맞춘다. 첫 칸은 왼쪽 눈금 글자 아래에 자리가 있어 가운데에 둔다
                            val toEnd = weekly && index == bars.lastIndex && " ~ " in bar.axisLabel
                            Box(Modifier.weight(1f).height(16.dp), contentAlignment = if (toEnd) Alignment.CenterEnd else Alignment.Center) {
                                // 미는 차트는 화면에 일곱 칸만 보이므로 처음·가운데·끝이 아니라 한 칸 걸러(마지막 날짜 포함) 날짜를 쓴다
                                val show = if (scrolls) (bars.lastIndex - index) % 2 == 0 else bar.showLabel
                                if (show) {
                                    Text(
                                        bar.axisLabel, fontSize = 12.sp, lineHeight = 16.sp, color = Link.Text, maxLines = 1, softWrap = false,
                                        modifier = Modifier.wrapContentWidth(if (toEnd) Alignment.End else Alignment.CenterHorizontally, unbounded = true),
                                    )
                                }
                            }
                        }
                    }
                }
                // 고른 막대 옆에 뜨는 팝업(웹 DailyBarChart 와 같은 내용: 값, 날짜). 막대 오른쪽에 두고 자리가 없으면 왼쪽으로 넘긴다.
                // 세로는 막대 꼭대기에 팝업 가운데를 맞추되 차트 위아래로 나가지 않게 가둔다. 값이 없는 날은 바닥에 맞춘다
                val pickedIndex = bars.indexOfFirst { it.key == selected }
                val picked = bars.getOrNull(pickedIndex)
                if (picked != null) {
                    var popup by remember { mutableStateOf(IntSize.Zero) }
                    val viewportWidth = maxWidth
                    val cell = (contentWidth - gap * (bars.size - 1)) / bars.size
                    // 꺾은선 차트는 막대가 없어 점의 반지름만큼만 띄운다
                    val half = if (line) 6.dp else (cell - (if (scrolls) 8.dp else 0.dp)).coerceAtMost(48.dp) / 2
                    val center = (cell + gap) * pickedIndex + cell / 2
                    val top = picked.value?.let { ChartHeight * (1f - scale.fraction(it)) } ?: ChartHeight
                    Column(
                        Modifier
                            .offset {
                                val scrolled = if (scrolls) scroll.value else 0
                                val right = (center + half + 6.dp).toPx() - scrolled
                                val left = (center - half - 6.dp).toPx() - scrolled - popup.width
                                val room = viewportWidth.toPx() - popup.width
                                val x = (if (right <= room) right else left).coerceIn(0f, room.coerceAtLeast(0f))
                                val y = (top.toPx() - popup.height / 2f).coerceIn(0f, (ChartHeight.toPx() - popup.height).coerceAtLeast(0f))
                                IntOffset(x.roundToInt(), y.roundToInt())
                            }
                            .onSizeChanged { popup = it }
                            // 크기를 재기 전 한 장면은 자리가 어긋나므로 보이지 않게 둔다
                            .alpha(if (popup == IntSize.Zero) 0f else 1f)
                            .shadow(8.dp, RoundedCornerShape(12.dp))
                            .background(Link.Background, RoundedCornerShape(12.dp)).border(1.dp, Link.Border, RoundedCornerShape(12.dp))
                            .padding(horizontal = 12.dp, vertical = 8.dp)
                            .clearAndSetSemantics { },
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        Text(
                            if (picked.value == null) "기록 없음" else V.value(metric, picked.value),
                            fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 1, softWrap = false,
                        )
                        Text(picked.title + if (picked.inProgress) " · 수집 중" else "", fontSize = 12.sp, lineHeight = 16.sp, color = Link.Sub, maxLines = 1, softWrap = false)
                    }
                }
            }
        }
        // 지금 보는 자리를 알리는 가는 막대(웹의 차트 아래 막대와 같다). 밀 수 있다는 것도 함께 알린다.
        // 기간 전체 보기에서는 막대를 그리지 않되 같은 높이를 비워 둔다. 비우지 않으면 보기를 바꿀 때마다 상자 높이가 달라져 아래 내용이 들썩였다
        if (available) {
            Spacer(Modifier.height(10.dp))
            if (scrolls) {
                BoxWithConstraints(Modifier.fillMaxWidth().padding(start = AxisWidth + AxisGap).height(4.dp).background(Link.Border, RoundedCornerShape(50)).clearAndSetSemantics { }) {
                    val share = (V.VISIBLE_DAYS.toFloat() / bars.size).coerceAtMost(1f)
                    val progress = if (scroll.maxValue > 0) scroll.value.toFloat() / scroll.maxValue else 1f
                    Box(Modifier.offset(x = maxWidth * (1f - share) * progress).width(maxWidth * share).fillMaxHeight().background(BarNormal, RoundedCornerShape(50)))
                }
            } else {
                Spacer(Modifier.height(4.dp))
            }
        }
        // 두 기간의 평균과 차이. 웹은 평균선 오른쪽 끝에 이름표로 붙이는데 휴대폰 폭에는 자리가 없어 차트 아래에 적는다
        if (previous != null && average != null) {
            Spacer(Modifier.height(10.dp))
            Text(
                "이번 기간 ${V.value(metric, average)} · 이전 기간 ${V.value(metric, previous.average)}\n이전 기간 대비 ${previous.delta}" + if (previous.reference) " · 참고용" else "",
                fontSize = 12.sp, lineHeight = 18.sp, color = Link.Sub,
            )
        }
    }
}

/**
 * 차트 범례(웹 DailyBarChart 와 같은 말). 막대 색은 평균선이 있을 때만 둘로 나뉘므로 그때만 색 범례를 둔다.
 * [empty]는 값이 없는 날의 점선 칸, [average]는 기간 평균 점선, [previous]는 이전 기간 평균 실선이다
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChartLegend(tones: Boolean, empty: Boolean, average: Boolean, previous: Boolean, line: Boolean) {
    if (!tones && !empty && !average && !previous) return
    // 휴대폰 폭에서 범례 세 항목과 보기 전환 버튼이 한 줄에 들어가게 항목 사이를 8로 둔다
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.clearAndSetSemantics { }) {
        @Composable
        fun item(name: String, mark: @Composable () -> Unit) = Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(3.dp)) {
            mark()
            Text(name, fontSize = 12.sp, color = Link.Text, maxLines = 1, softWrap = false)
        }
        if (tones) {
            item("평균 이상") { Box(Modifier.size(10.dp).background(BarHigh, RoundedCornerShape(3.dp))) }
            item("평균 미만") { Box(Modifier.size(10.dp).background(BarLow, RoundedCornerShape(3.dp))) }
        }
        if (empty) {
            item("기록 없음") {
                Canvas(Modifier.size(10.dp)) {
                    drawRoundRect(Link.Muted.copy(alpha = 0.6f), cornerRadius = CornerRadius(3.dp.toPx()), style = Stroke(1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(3f, 3f))))
                }
            }
        }
        if (average) {
            item("기간 평균") {
                Canvas(Modifier.width(16.dp).height(2.dp)) {
                    drawLine(if (line) BarNormal else BarHigh, Offset(0f, size.height / 2), Offset(size.width, size.height / 2), strokeWidth = 1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f)))
                }
            }
        }
        if (previous) {
            item("이전 기간 평균") { Box(Modifier.width(16.dp).height(1.5.dp).background(Link.Muted)) }
        }
    }
}

/** 차트 오른쪽 위의 보기 전환: "일주일"과 기간 전체("한 달", 짧으면 "14일"). 웹 ChartRangeControls 와 같다 */
@Composable
internal fun RangeToggle(whole: Boolean, wholeLabel: String, onChange: (Boolean) -> Unit) {
    Row(Modifier.background(Link.Background, RoundedCornerShape(8.dp)).border(1.dp, Link.Border, RoundedCornerShape(8.dp)).padding(2.dp)) {
        listOf(false to "일주일", true to wholeLabel).forEach { (value, name) ->
            val on = value == whole
            // 글자를 버튼 한가운데에 둔다. 위아래 여백(padding)으로 맞추면 글꼴의 줄 높이가 글자 위쪽으로 더 커서 글자가 가운데보다 아래에 놓였다
            // (실기기에서 재니 글자 위 33px · 아래 15px). 버튼 높이를 정해 가운데 정렬하고 줄 높이의 위아래 여유를 잘라 낸다(위 22px · 아래 21px)
            Box(
                Modifier.height(26.dp).clip(RoundedCornerShape(6.dp)).background(if (on) Link.Pink else Link.Background)
                    .clickable(role = Role.Tab) { onChange(value) }
                    .semantics { stateDescription = if (on) "선택됨" else "선택 안 됨" }
                    .padding(horizontal = 9.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    name, fontSize = 12.sp, lineHeight = 12.sp, fontWeight = if (on) FontWeight.Bold else FontWeight.Normal, color = if (on) Link.Primary else Link.Text, maxLines = 1,
                    style = LocalTextStyle.current.merge(
                        TextStyle(
                            platformStyle = PlatformTextStyle(includeFontPadding = false),
                            lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.Both),
                        ),
                    ),
                )
            }
        }
    }
}

/** 막대 위의 "최고"·"최저" 꼬리표. 좁은 칸보다 넓을 수 있어 칸 폭에 맞추지 않는다 */
@Composable
private fun ExtremeLabel(highest: Boolean, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(50)
    Text(
        if (highest) "최고" else "최저", fontSize = 10.sp, lineHeight = 12.sp, fontWeight = FontWeight.Bold, maxLines = 1, softWrap = false,
        color = if (highest) Color.White else Link.Text,
        modifier = modifier.wrapContentWidth(unbounded = true)
            .then(if (highest) Modifier.background(Link.Primary, shape) else Modifier.background(Link.Background, shape).border(1.dp, Link.Border, shape))
            .padding(horizontal = 5.dp, vertical = 1.dp),
    )
}

@Composable
private fun SleepSessionsPanel(evidence: SleepSessionsEvidence) {
    val view = remember(evidence) { V.sleepSessions(evidence) }
    PeriodHeader("수면 기록", V.period(evidence.periodStart, evidence.periodEnd))
    if (view.sessions.isEmpty()) {
        EmptyBox("이 기간에는 수면 기록이 없어요.")
    } else {
        // 휴대폰 폭의 웹처럼 두 값을 세로로 놓는다
        Panel {
            Stat("주 수면 평균", V.value("sleeps", view.mainAverage), size = 28)
            Stat("수면 기록", "${view.sessions.size}회", if (view.extraCount > 0) "주 수면 ${view.mainCount}회 · 추가 수면 ${view.extraCount}회" else null, size = 28)
        }
        Section {
            SectionTitle("수면 기록")
            RowsPanel(view.sessions) { session ->
                Column(Modifier.padding(vertical = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            V.sleepLabel(session.startAt) + if (session.isMain) "" else " · 추가 수면",
                            fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.weight(1f),
                        )
                        Text(V.value("sleeps", session.sleepMinutes), fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                    }
                    Text("${V.clock(session.startAt)} ~ ${V.clock(session.endAt)}", fontSize = 13.sp, color = Link.Text)
                    Spo2Line(V.spo2Low(session.spo2LowCount, session.spo2LowTimes))
                }
            }
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Note("낮잠과 자다 깨서 다시 잔 수면까지 모두 보여 줘요.")
        Note("밤·아침·낮은 잠든 시각 기준이에요. 주 수면 평균에는 추가 수면을 넣지 않아요.")
    }
}

@Composable
private fun HourlyStepsPanel(evidence: HourlyStepsEvidence) {
    val columns = remember(evidence) { V.hourlyColumns(evidence) }
    PeriodHeader("시간대별 걸음", V.period(evidence.periodStart, evidence.periodEnd))
    Panel {
        Stat(if (columns.isNotEmpty()) "${columns.first()}시~${evidence.endHour}시 걸음 합계" else "걸음 합계", V.value("steps", evidence.total), size = 28)
    }
    Section {
        SectionTitle("날짜별 시간대 걸음")
        // 시간대가 많으면 표만 가로로 넘긴다. 날짜 열은 고정하고 나머지를 민다
        val scroll = rememberScrollState()
        Row(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape)) {
            Column(Modifier.width(104.dp)) {
                TableCell("날짜", bold = true, align = TextAlign.Start, head = true)
                evidence.days.forEach { day ->
                    HorizontalDivider(color = Link.Border)
                    TableCell(
                        buildAnnotatedString {
                            append(V.dayWithWeekday(day.date))
                            // "수집 중"은 웹과 같이 작은 코랄색 글자로 둔다
                            if (day.inProgress) withStyle(SpanStyle(color = Link.CoralMain, fontSize = 12.sp, fontWeight = FontWeight.Normal)) { append("\n수집 중") }
                        },
                        bold = true, align = TextAlign.Start, tall = day.inProgress,
                    )
                }
            }
            Column(Modifier.weight(1f).horizontalScroll(scroll)) {
                Row {
                    columns.forEach { TableCell("${it}시", bold = true, width = 60.dp, head = true) }
                    TableCell("합계", bold = true, width = 84.dp, head = true)
                }
                evidence.days.forEach { day ->
                    HorizontalDivider(Modifier.width(60.dp * columns.size + 84.dp), color = Link.Border)
                    Row {
                        val values = day.values
                        if (values != null) columns.forEachIndexed { i, _ -> TableCell(V.count(values.getOrNull(i)), width = 60.dp, tall = day.inProgress) }
                        else TableCell("기록 없음", width = 60.dp * columns.size, align = TextAlign.Center, tall = day.inProgress)
                        TableCell(if (day.total == null) "-" else V.value("steps", day.total), bold = true, width = 84.dp, tall = day.inProgress)
                    }
                }
            }
        }
    }
    // "선택한 기간·시간대의 걸음 기록이에요"는 위 기간 머리말과 겹치는 말이라 두지 않는다(웹과 같다)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        // 기간 합계는 백엔드가 낸다. 하루라도 기록이 없으면 비어 있고, 앱에서 다시 더하지 않고 이유만 알린다
        if (evidence.total == null && evidence.days.any { it.total != null }) Note("기록 없는 날이 있어 합계를 내지 않았어요.")
        if (evidence.days.any { it.inProgress }) {
            Note(if (evidence.total == null) "오늘 기록은 수집 중이에요." else "오늘 기록은 수집 중이며, 지금까지 걸음도 합계에 들어가요.")
        }
    }
}

@Composable
private fun TableCell(text: String, bold: Boolean = false, width: Dp? = null, align: TextAlign = TextAlign.End, tall: Boolean = false, head: Boolean = false) =
    TableCell(AnnotatedString(text), bold, width, align, tall, head)

/** 표 한 칸. 머리 줄은 13, 본문은 14(웹과 같다) */
@Composable
private fun TableCell(text: AnnotatedString, bold: Boolean = false, width: Dp? = null, align: TextAlign = TextAlign.End, tall: Boolean = false, head: Boolean = false) {
    Text(
        text, fontSize = if (head) 13.sp else 14.sp, lineHeight = 18.sp, fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal, color = Link.Text,
        textAlign = align, maxLines = if (tall) 2 else 1,
        modifier = (if (width != null) Modifier.width(width) else Modifier.fillMaxWidth()).height(if (tall) 58.dp else 44.dp)
            .padding(horizontal = 12.dp).wrapContentHeight(Alignment.CenterVertically),
    )
}
