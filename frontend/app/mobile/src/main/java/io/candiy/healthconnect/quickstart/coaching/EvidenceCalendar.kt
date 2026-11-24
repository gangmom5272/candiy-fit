package io.candiy.healthconnect.quickstart.coaching

import androidx.compose.foundation.Canvas
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
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import io.candiy.healthconnect.quickstart.Link
import io.candiy.healthconnect.quickstart.LinkIcons
import kotlin.math.PI
import kotlin.math.max
import kotlin.math.roundToInt
import io.candiy.healthconnect.quickstart.coaching.EvidenceView as V

/*
 * 근거 상세의 "날짜별 기록" 달력, 그날 상세, 시간대별 걸음, 날짜별 수면 단계 차트(S15P31A403-440).
 * 웹 DayCalendar · HourlyStepsMini · SleepStageBars 를 앱 화면 폭에 맞게 옮겼다. 값과 문구 규칙은 EvidenceView 에 있고 여기서는 배치만 한다.
 * 웹과 다른 점: 휴대폰 폭이라 달력 아래에 그날 상세를 두고, 마우스가 없어 막대는 눌러서 값을 본다.
 */

/** 웹 warning. 수집 중인 오늘의 원과 "수집 중" 글자 */
private val Warning = Color(0xFFE0A447)
/** 웹 info. 달력의 토요일 */
private val Info = Color(0xFF6D8EDB)
private val InnerShape = RoundedCornerShape(12.dp)

/**
 * 날짜별 기록 달력(웹 430~439). 칸마다 숫자 대신 원을 그린다: 첫 바퀴는 0에서 평균까지, 두 바퀴째는 평균에서 기간 최고까지.
 * 기간의 최고·최저 날은 원 가운데 체크로 알린다. 날짜를 누르면 아래에 그날 기록이 나온다.
 * 처음에는 마지막으로 기록이 있는 날과 그 달을 보여 준다. 달은 기본 5주이고 6주가 필요한 달만 한 줄 늘린다
 */
@Composable
internal fun DayCalendar(metric: String, s: MetricSnapshot, base: V.CalendarBase, hourly: HourlyStepsLoader?) {
    val days = remember(s) { V.calendarDays(s) }
    if (days.isEmpty()) return
    val months = remember(days) { V.calendarMonths(days) }
    val start = remember(days) { V.calendarStart(days) }
    var chosenMonth by remember(s) { mutableStateOf<String?>(null) }
    var chosenDate by remember(s) { mutableStateOf<String?>(null) }
    val monthIndex = chosenMonth?.let { months.indexOf(it) }?.takeIf { it >= 0 } ?: max(0, months.indexOf(start?.date?.take(7)))
    val month = months[monthIndex]
    val weeks = remember(month, days) { V.calendarWeeks(month, days) }
    val selected = days.firstOrNull { it.date == chosenDate } ?: start
    val extremes = remember(s) { V.calendarExtremes(metric, s) }
    val (highLabel, lowLabel) = V.highlightLabels(metric)
    fun markOf(date: String): Boolean? = when (date) { extremes.first?.date -> true; extremes.second?.date -> false; else -> null }
    fun markLabel(mark: Boolean?): String? = when (mark) { true -> highLabel; false -> lowLabel; else -> null }
    val spo2 = remember(s) { if (metric == "sleeps") V.nightSpo2(s).associateBy { it.date } else emptyMap() }
    val sessions = remember(s) { V.sessionItems(s) }
    val activity = remember(s) { V.activity(s) }
    val period = remember(days) { days.minOf { it.date } to days.maxOf { it.date } }
    var help by remember { mutableStateOf(false) }

    // 보고 있는 달의 시간대별 걸음을 미리 불러 둔다. 날짜를 누를 때는 이미 와 있다. 실패는 날짜를 눌렀을 때 알린다
    if (metric == "steps" && hourly != null) {
        LaunchedEffect(month, hourly) {
            val (from, to) = V.hourlyRange("$month-01", period.first, period.second)
            hourly.load(from, to)
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Column(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(horizontal = 8.dp, vertical = 12.dp)) {
            Box(Modifier.fillMaxWidth()) {
                Row(Modifier.align(Alignment.Center), verticalAlignment = Alignment.CenterVertically) {
                    MonthButton("이전 달", LinkIcons.ChevronLeft, enabled = monthIndex > 0) { chosenMonth = months[monthIndex - 1] }
                    Text(
                        V.monthTitle(month, months), fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text,
                        modifier = Modifier.padding(horizontal = 6.dp).semantics { heading() },
                    )
                    MonthButton("다음 달", LinkIcons.ChevronRight, enabled = monthIndex < months.lastIndex) { chosenMonth = months[monthIndex + 1] }
                }
                Text(
                    "?", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Sub, textAlign = TextAlign.Center,
                    modifier = Modifier.align(Alignment.CenterEnd).padding(end = 4.dp).size(28.dp).clip(CircleShape).background(Link.Background)
                        .border(1.dp, Link.Border, CircleShape).clickable(role = Role.Button) { help = true }
                        .semantics { contentDescription = "달력 보는 법" }.wrapContentHeight(Alignment.CenterVertically),
                )
            }
            Spacer(Modifier.height(8.dp))
            // 요일·날짜 숫자는 글자 확대를 따르지 않는다. 휴대폰 폭 일곱 칸에 글자를 키우면 두 자리 날짜가 옆 칸 숫자와 붙는다(웹 438)
            val small = with(LocalDensity.current) { 12.dp.toSp() }
            Row(Modifier.fillMaxWidth().clearAndSetSemantics { }) {
                listOf("일", "월", "화", "수", "목", "금", "토").forEachIndexed { index, name ->
                    Text(
                        name, fontSize = small, textAlign = TextAlign.Center, color = if (index == 0) Link.Primary else if (index == 6) Info else Link.Sub,
                        modifier = Modifier.weight(1f).padding(vertical = 4.dp),
                    )
                }
            }
            weeks.forEach { week ->
                Row(Modifier.fillMaxWidth().padding(top = 4.dp)) {
                    week.forEach { cell ->
                        Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                            val entry = cell.entry
                            val mark = if (entry != null) markOf(cell.date) else null
                            DayCell(
                                metric, cell, selected = selected?.date == cell.date, average = base.value, max = extremes.first?.value, mark = mark,
                                label = entry?.let { V.cellLabel(metric, it, markLabel(mark)) },
                                lowDot = spo2[cell.date]?.let { V.hasLowSpo2(it.state) } == true,
                            ) { chosenDate = cell.date }
                        }
                    }
                }
            }
        }

        selected?.let { day ->
            val mark = markOf(day.date)
            DayDetail(
                metric, s, day, base.value, mark, markLabel(mark),
                daySessions = sessions.filter { it.session.date == day.date },
                activityRow = activity?.rows?.firstOrNull { it.date == day.date },
                night = spo2[day.date],
                nightRecord = if (metric == "sleeps") s.nights.firstOrNull { it.date == day.date } else null,
                hourly = hourly, period = period,
            )
        }

        // 기간의 운동 횟수·칼로리·거리. AI 가 운동 요약에 쓰는 값이라 달력 아래에 늘 둔다. 합계·평균은 서버가 낸 값 그대로다
        if (metric == "exercises" && activity != null) PeriodActivity(activity)
    }

    if (help) {
        CalendarHelp(
            metric, base, max = extremes.first?.value, highLabel = highLabel.takeIf { extremes.first != null }, lowLabel = lowLabel.takeIf { extremes.second != null },
            inProgress = s.days.any { it.inProgress }, lowSpo2 = spo2.values.any { V.hasLowSpo2(it.state) },
        ) { help = false }
    }
}

@Composable
private fun MonthButton(name: String, icon: androidx.compose.ui.graphics.vector.ImageVector, enabled: Boolean, onClick: () -> Unit) {
    // 첫 달·마지막 달에서는 그쪽 버튼을 감추되 자리는 남겨, 달 이름이 좌우로 움직이지 않게 한다
    if (!enabled) { Spacer(Modifier.size(32.dp)); return }
    Box(
        Modifier.size(32.dp).clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onClick).semantics { contentDescription = name },
        contentAlignment = Alignment.Center,
    ) { Icon(icon, contentDescription = null, tint = Link.Sub, modifier = Modifier.size(18.dp)) }
}

/** 달력 한 칸. 기간 밖의 날은 숫자만 두고(같은 달은 회색, 앞뒤 달은 흐리게) 누를 수 없다 */
@Composable
private fun DayCell(
    metric: String, cell: V.CalendarCell, selected: Boolean, average: Double, max: Double?, mark: Boolean?, label: String?, lowDot: Boolean, onClick: () -> Unit,
) {
    val tone = when (cell.weekday) { 0 -> Link.Primary; 6 -> Info; else -> Link.Text }
    val number = with(LocalDensity.current) { 14.dp.toSp() }
    val entry = cell.entry
    if (entry == null) {
        Column(
            Modifier.widthIn(max = 48.dp).fillMaxWidth().padding(vertical = 6.dp).alpha(if (cell.inMonth) 1f else 0.35f).clearAndSetSemantics { },
            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(cell.day.toString(), fontSize = number, fontWeight = if (cell.inMonth) FontWeight.Normal else FontWeight.Bold, color = if (cell.inMonth) Link.Disabled else tone)
            Spacer(Modifier.size(30.dp))
        }
        return
    }
    Box(
        Modifier.widthIn(max = 48.dp).fillMaxWidth().clip(InnerShape)
            .then(if (selected) Modifier.background(Link.Background).border(2.dp, Link.Disabled, InnerShape) else Modifier)
            .clickable(role = Role.Button, onClick = onClick)
            .clearAndSetSemantics { contentDescription = label.orEmpty(); this.selected = selected }
            .padding(vertical = 6.dp),
    ) {
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(cell.day.toString(), fontSize = number, fontWeight = FontWeight.Bold, color = tone)
            Donut(V.shownValue(metric, entry), entry.inProgress, average, max, mark, Modifier.size(30.dp))
        }
        // 수면 중 산소포화도가 90% 미만으로 내려간 밤
        if (lowDot) Box(Modifier.align(Alignment.TopEnd).padding(top = 4.dp, end = 6.dp).size(6.dp).background(Link.Primary, CircleShape))
    }
}

/**
 * 한 바퀴(0~1)를 원호로 그린다. 둥근 끝은 선 양쪽으로 굵기의 절반씩 튀어나와 그대로 그리면 실제보다 길어 보인다
 * (95% 인 날이 꽉 찬 원과 같아 보였다, 웹 438). 그 길이만큼 줄이고 시작점을 밀어 둥근 끝까지 합친 길이가 값과 같게 한다.
 * 다 찬 바퀴는 끝이 맞닿아 평평한 끝으로 그린다
 */
private fun DrawScope.lap(amount: Float, color: Color, center: Offset, radius: Float, stroke: Float) {
    if (amount <= 0f) return
    val topLeft = Offset(center.x - radius, center.y - radius)
    val arc = Size(radius * 2, radius * 2)
    if (amount >= 1f) {
        drawArc(color, -90f, 360f, false, topLeft, arc, style = Stroke(stroke, cap = StrokeCap.Butt))
        return
    }
    val cap = (stroke / 2f) / (2f * PI.toFloat() * radius)
    val length = (amount - cap * 2f).coerceAtLeast(0.0001f)
    drawArc(color, -90f + cap * 360f, length * 360f, false, topLeft, arc, style = Stroke(stroke, cap = StrokeCap.Round))
}

/** 체크 표시. 30칸 기준 좌표를 [unit]배로 그린다 */
private fun DrawScope.check(color: Color, unit: Float, origin: Offset = Offset.Zero, width: Float = 2.4f) {
    val path = Path().apply {
        moveTo(origin.x + 10.6f * unit, origin.y + 15.3f * unit)
        lineTo(origin.x + 13.6f * unit, origin.y + 18.3f * unit)
        lineTo(origin.x + 19.4f * unit, origin.y + 12.1f * unit)
    }
    drawPath(path, color, style = Stroke(width * unit, cap = StrokeCap.Round, join = StrokeJoin.Round))
}

/**
 * 그날 값을 평균과 견준 원. 값이 없으면 점선 원이다. 수집 중인 오늘은 노란색으로 그린다.
 * [mark]가 true 면 기간 최고(진한 레드 체크), false 면 기간 최저(옅은 코랄 체크)다
 */
@Composable
internal fun Donut(value: Double?, today: Boolean, average: Double, max: Double?, mark: Boolean?, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        val unit = size.minDimension / 30f
        val radius = 11f * unit
        val center = Offset(size.width / 2, size.height / 2)
        if (value == null) {
            drawCircle(Link.Disabled, radius, center, style = Stroke(1.5f * unit, pathEffect = PathEffect.dashPathEffect(floatArrayOf(2f * unit, 3f * unit))))
        } else {
            val stroke = 4f * unit
            drawCircle(Link.Border, radius, center, style = Stroke(stroke))
            val (first, second) = V.donutFill(value, average, max)
            lap(first, if (today) Warning else Link.CoralMain, center, radius, stroke)
            lap(second, Link.Primary, center, radius, stroke)
        }
        if (mark != null) check(if (mark) Link.Primary else Link.Coral, unit)
    }
}

/** 두 칸 값 상자(잠든·깬 시각, 측정 최저·최고) */
@Composable
private fun PairBox(leftName: String, leftValue: String, rightName: String, rightValue: String) {
    Row(Modifier.fillMaxWidth().background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape).padding(vertical = 12.dp)) {
        listOf(leftName to leftValue, rightName to rightValue).forEachIndexed { index, (name, text) ->
            if (index > 0) Box(Modifier.width(1.dp).height(36.dp).background(Link.Border))
            Column(Modifier.weight(1f).padding(horizontal = 8.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(name, fontSize = 12.sp, color = Link.Sub)
                Text(text, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 1)
            }
        }
    }
}

/**
 * 이름과 값이 한 줄에 놓이는 목록 줄. 이름은 글자 폭만큼 차지하고 값이 남은 폭에서 오른쪽에 붙는다.
 * 처음에는 이름이 남은 폭을 가졌는데, 값이 길면 값이 한 줄을 다 차지해 이름이 밀려 사라졌다
 */
@Composable
private fun PlainRow(name: String, value: @Composable () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.Top) {
        Text(name, fontSize = 14.sp, color = Link.Text, maxLines = 1, softWrap = false)
        Spacer(Modifier.width(12.dp))
        Box(Modifier.weight(1f), contentAlignment = Alignment.TopEnd) { value() }
    }
}

@Composable
private fun PlainRow(name: String, value: String) = PlainRow(name) {
    Text(value, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, textAlign = TextAlign.End)
}

/** 달력에서 고른 날의 기록. 달을 먼저 작게, 날짜와 요일을 크게 쓴다("27 화요일 10월" 순서는 읽기 어색했다, 웹 435) */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DayDetail(
    metric: String, s: MetricSnapshot, day: EvidenceDay, average: Double, mark: Boolean?, markLabel: String?,
    daySessions: List<V.SessionItem>, activityRow: V.ActivityRow?, night: V.NightSpo2?, nightRecord: SleepNight?,
    hourly: HourlyStepsLoader?, period: Pair<String, String>,
) {
    Column(
        Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("${day.date.substring(5, 7).toInt()}월", fontSize = 13.sp, color = Link.Sub, modifier = Modifier.alignByBaseline())
            Text(day.date.substring(8).toInt().toString(), fontSize = 28.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.alignByBaseline())
            Text("${V.weekdayName(day.date)}요일", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.alignByBaseline())
        }
        val shown = V.shownValue(metric, day)
        if (shown == null) {
            Text(V.MISSING_REASON_LABELS[day.reason] ?: "기록 없음", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Sub)
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                // 심박 칸의 값은 하루 평균이다. 아래 측정 최저·최고와 헷갈리지 않게 이름을 붙인다
                if (metric == "heart_rates") Text("하루 평균", fontSize = 12.sp, color = Link.Sub)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(V.value(metric, shown), fontSize = 20.sp, lineHeight = 28.sp, fontWeight = FontWeight.Bold, color = Link.Text)
                    if (mark != null && markLabel != null) MarkPill(mark, markLabel, Modifier.align(Alignment.CenterVertically))
                }
                Text(
                    if (day.inProgress) "수집 중" else V.compareToAverage(metric, shown, average),
                    fontSize = 13.sp, color = if (day.inProgress) Warning else Link.Sub,
                )
            }
        }

        // 활동 탭은 그날 시간대별 걸음을 함께 보여 준다(웹 438)
        if (metric == "steps" && hourly != null) HourlyStepsMini(day.date, period, hourly)

        // 그날 실제 측정 최저·최고 심박
        V.dailyRange(metric, s, day.date)?.let { PairBox("측정 최저", V.value(metric, it.min), "측정 최고", V.value(metric, it.max)) }

        // 그 밤에 잠든 시각과 깬 시각. 날짜는 깬 날이라 잠든 시각이 전날 밤일 수 있다
        if (nightRecord != null) PairBox("잠든 시각", V.clock(nightRecord.startAt), "깬 시각", nightRecord.endAt?.let { V.clock(it) } ?: "-")

        if (metric == "exercises") {
            // 그날 운동 횟수·칼로리·거리
            if (activityRow != null) {
                Row(Modifier.fillMaxWidth().background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape).padding(vertical = 12.dp)) {
                    listOf("횟수", "칼로리", "거리").forEachIndexed { index, name ->
                        if (index > 0) Box(Modifier.width(1.dp).height(36.dp).background(Link.Border))
                        Column(Modifier.weight(1f).padding(horizontal = 6.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(name, fontSize = 12.sp, color = Link.Sub)
                            Text(activityRow.cells[index], fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 1, softWrap = false)
                        }
                    }
                }
            }
            if (daySessions.isNotEmpty()) {
                Column(Modifier.fillMaxWidth().background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape).padding(horizontal = 14.dp)) {
                    daySessions.forEachIndexed { index, item ->
                        if (index > 0) HorizontalDivider(color = Link.Border)
                        val session = item.session
                        val color = if (session.countsAsExercise) Link.Text else Link.Sub
                        Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(V.clock(session.startAt), fontSize = 13.sp, color = Link.Sub)
                            Spacer(Modifier.width(8.dp))
                            Text(session.title ?: session.typeName, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = color, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            Spacer(Modifier.width(8.dp))
                            if (item.isLongest) {
                                Text("가장 길게", fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Link.Primary, modifier = Modifier.background(Link.Pink, RoundedCornerShape(50)).padding(horizontal = 8.dp, vertical = 2.dp))
                                Spacer(Modifier.width(8.dp))
                            } else if (!session.countsAsExercise) {
                                Text("운동 미인정", fontSize = 12.sp, color = Link.Sub)
                                Spacer(Modifier.width(8.dp))
                            }
                            Text(V.value("exercises", session.durationMinutes), fontSize = 14.sp, fontWeight = FontWeight.Bold, color = color, maxLines = 1)
                        }
                    }
                }
            }
            if (daySessions.any { !it.session.countsAsExercise }) Note("운동으로 인정되지 않은 기록은 운동 시간에서 뺐어요.")
        }

        // 그 밤 수면 중 산소포화도. 따로 있던 밤별 목록을 달력 상세로 옮겼다
        if (night != null) {
            Column(Modifier.fillMaxWidth().background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape).padding(horizontal = 14.dp)) {
                PlainRow("수면 중 평균 산소포화도", V.value("oxygen_saturations", night.average))
                if (nightRecord?.spo2Min != null || nightRecord?.spo2Max != null) {
                    HorizontalDivider(color = Link.Border)
                    PlainRow("최저 · 최고", "${V.value("oxygen_saturations", nightRecord.spo2Min)} · ${V.value("oxygen_saturations", nightRecord.spo2Max)}")
                }
                HorizontalDivider(color = Link.Border)
                // 시각이 많으면 앞의 세 개만 두고 "외 N회 보기"로 접는다(웹과 같다)
                PlainRow("90% 미만") { Spo2Bare(night.state, end = true, bold = true) }
            }
        }
    }
}

/** 고른 날이 기간 최고·최저일 때 값 옆에 붙이는 꼬리표. 원 가운데 체크와 같은 색이다 */
@Composable
private fun MarkPill(highest: Boolean, label: String, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(50)
    Row(
        modifier.then(if (highest) Modifier.background(Link.Pink, shape) else Modifier.background(Link.Background, shape).border(1.dp, Link.Coral, shape))
            .padding(horizontal = 8.dp, vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Canvas(Modifier.size(14.dp)) { (size.minDimension / 12f).let { unit -> check(if (highest) Link.Primary else Link.Coral, unit, Offset(-9f * unit, -9.2f * unit), width = 1.8f) } }
        Text(label, fontSize = 12.sp, fontWeight = FontWeight.Bold, color = if (highest) Link.Primary else Link.Text)
    }
}

/** 이 기간의 운동 횟수·칼로리·거리 합계(웹 "이 기간 합계"). 한 줄에 하나씩, 왼쪽에 이름과 보조 글, 오른쪽에 값을 둔다 */
@Composable
private fun PeriodActivity(activity: V.ActivityView) {
    Column(
        Modifier.fillMaxWidth().background(Link.Surface, BoxShape).border(1.dp, Link.Border, BoxShape).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("이 기간 합계", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.semantics { heading() })
        activity.cards.forEach { card ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(card.label, fontSize = 12.sp, color = Link.Sub)
                    Text(card.captions.joinToString(" · "), fontSize = 12.sp, color = Link.Sub)
                }
                Spacer(Modifier.width(12.dp))
                Text(card.value, fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 1)
            }
        }
        HorizontalDivider(color = Link.Border)
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            activity.notes.forEach { Text(it, fontSize = 12.sp, lineHeight = 18.sp, color = Link.Sub) }
        }
    }
}

/** 물음표를 누르면 뜨는 안내. 원을 읽는 법과 이 기간의 기준 값을 알린다 */
@Composable
private fun CalendarHelp(
    metric: String, base: V.CalendarBase, max: Double?, highLabel: String?, lowLabel: String?, inProgress: Boolean, lowSpo2: Boolean, onClose: () -> Unit,
) {
    val baseName = V.baseName(base.basis)
    val samples = buildList<Pair<Double?, String>> {
        add(base.value * 0.6 to "평균 미만")
        add(base.value to "평균")
        if (max != null && max > base.value) {
            add((base.value + max) / 2 to "평균 초과")
            add(max to "기간 최고")
        }
        add(null to "기록 없음")
    }
    Dialog(onDismissRequest = onClose) {
        Column(
            Modifier.fillMaxWidth().background(Link.Background, RoundedCornerShape(24.dp)).padding(start = 24.dp, end = 24.dp, top = 24.dp, bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("한 바퀴가 ${baseName}이에요", fontSize = 18.sp, fontWeight = FontWeight.Bold, color = Link.Text, modifier = Modifier.semantics { heading() })
                Text(
                    "날마다 기록을 이 기간 ${baseName}과 비교해 원을 채워요. 평균을 넘은 날은 진한 색으로 한 번 더 감기고, 기간에서 가장 높은 날이 두 바퀴를 다 채워요.",
                    fontSize = 14.sp, lineHeight = 22.sp, color = Link.Sub,
                )
            }
            Row(Modifier.fillMaxWidth().background(Link.Surface, InnerShape).padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("이 기간 $baseName", fontSize = 14.sp, color = Link.Sub, modifier = Modifier.weight(1f))
                Text(V.value(metric, base.value), fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.Text)
            }
            Row(Modifier.fillMaxWidth()) {
                samples.forEach { (sample, name) ->
                    Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Donut(sample, false, base.value, max, null, Modifier.size(40.dp))
                        Text(name, fontSize = 11.sp, color = Link.Sub, textAlign = TextAlign.Center, maxLines = 1, softWrap = false)
                    }
                }
            }
            HorizontalDivider(color = Link.Border)
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (highLabel != null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        HelpMark(true, highLabel)
                        if (lowLabel != null) HelpMark(false, lowLabel)
                    }
                }
                if (inProgress) Note("오늘은 기록이 모이는 중이라 노란색으로 보여요.")
                if (lowSpo2) Note("날짜 옆 빨간 점은 수면 중 산소포화도가 90% 미만으로 내려간 밤이에요.")
                when (base.basis) {
                    V.CalendarBasis.ACTIVE_DAYS -> Note("운동 기록이 없는 날은 쉬는 날이라 평균과 최저에서 뺐어요. 위의 평균 운동 시간은 그날까지 넣은 값이에요.")
                    V.CalendarBasis.MERGED -> Note("나눠 조회한 기간을 합쳐 이 달력에서만 쓰는 평균이에요. AI 가 받은 평균은 위 기간별 평균이에요.")
                    V.CalendarBasis.SUMMARY -> Unit
                }
                Note("날짜를 누르면 그날 기록을 볼 수 있어요.")
            }
            Text(
                "확인", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = Link.OnPrimary, textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().clip(InnerShape).background(Link.Primary).clickable(role = Role.Button, onClick = onClose)
                    .height(44.dp).wrapContentHeight(Alignment.CenterVertically),
            )
        }
    }
}

@Composable
private fun HelpMark(highest: Boolean, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Canvas(Modifier.size(14.dp)) { (size.minDimension / 12f).let { unit -> check(if (highest) Link.Primary else Link.Coral, unit, Offset(-9f * unit, -9.2f * unit), width = 1.8f) } }
        Text(label, fontSize = 13.sp, color = Link.Sub)
    }
}

private sealed interface HourlyState {
    data object Loading : HourlyState
    data object Failed : HourlyState
    data class Ready(val hours: List<Double>?) : HourlyState
}

/**
 * 활동 달력에서 고른 날의 시간대별 걸음(웹 438·439). 그날이 든 달을 한 번에 불러와, 같은 달 안에서는 날짜를 바꿔도 다시 부르지 않는다.
 * 불러오는 중·기록 없음·실패·값 모두 같은 세 줄(문장·막대·시각)로 그려, 날짜를 빠르게 바꿔도 상자 높이가 그대로다.
 * 막대를 누르면 그 시간의 걸음이 막대 위에 뜬다. 다시 누르거나 날짜를 바꾸면 닫힌다
 */
@Composable
private fun HourlyStepsMini(date: String, period: Pair<String, String>, loader: HourlyStepsLoader) {
    var retry by remember { mutableIntStateOf(0) }
    var state by remember(date) { mutableStateOf<HourlyState>(if (loader.has(date)) HourlyState.Ready(loader.peek(date)) else HourlyState.Loading) }
    LaunchedEffect(date, retry) {
        if (loader.has(date)) { state = HourlyState.Ready(loader.peek(date)); return@LaunchedEffect }
        state = HourlyState.Loading
        val (from, to) = V.hourlyRange(date, period.first, period.second)
        state = if (loader.load(from, to)) HourlyState.Ready(loader.peek(date)) else HourlyState.Failed
    }
    val hours = (state as? HourlyState.Ready)?.hours
    val peak = remember(hours) { V.hourlyPeak(hours) }
    var picked by remember(date) { mutableStateOf<Int?>(null) }
    val barArea = 64.dp
    fun barHeight(value: Double, top: Double) = if (value == 0.0) 2.dp else (barArea * (value / top).toFloat()).coerceAtLeast(4.dp)

    Column(
        Modifier.fillMaxWidth().background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("시간대별 걸음", fontSize = 12.sp, color = Link.Sub, modifier = Modifier.semantics { heading() })
        // 첫 줄: 가장 많이 걸은 시간. 값이 없을 때는 그 자리에 안내를 같은 줄 높이로 둔다
        Box(Modifier.fillMaxWidth().height(22.dp), contentAlignment = Alignment.CenterStart) {
            when {
                state is HourlyState.Loading -> Unit
                state is HourlyState.Failed -> Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("시간대별 걸음을 불러오지 못했어요.", fontSize = 13.sp, color = Link.Sub, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Text(
                        "다시 불러오기", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Link.Text,
                        modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button) { retry += 1 }.padding(horizontal = 8.dp, vertical = 2.dp),
                    )
                }
                hours == null || peak == null -> Text("이날은 시간대별 걸음 기록이 없어요.", fontSize = 13.sp, color = Link.Sub, maxLines = 1, overflow = TextOverflow.Ellipsis)
                else -> Text(
                    androidx.compose.ui.text.buildAnnotatedString {
                        append("가장 많이 걸은 시간 ")
                        pushStyle(androidx.compose.ui.text.SpanStyle(fontWeight = FontWeight.Bold)); append("${peak.first}시"); pop()
                        append(" · ${V.value("steps", peak.second)}")
                    },
                    fontSize = 14.sp, color = Link.Text, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        // 둘째 줄: 막대 24개(0시~23시). 가장 많이 걸은 시간만 진하게 둔다
        if (state is HourlyState.Loading) {
            Box(Modifier.fillMaxWidth().height(barArea).background(Link.Surface, RoundedCornerShape(8.dp)).semantics { contentDescription = "시간대별 걸음 불러오는 중" })
        } else if (hours != null && peak != null) {
            val summary = remember(hours) { hours.mapIndexed { hour, value -> "${hour}시 ${V.value("steps", value)}" }.joinToString(", ") }
            BoxWithConstraints(Modifier.fillMaxWidth().height(barArea).clearAndSetSemantics { contentDescription = summary }) {
                Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(1.dp), verticalAlignment = Alignment.Bottom) {
                    hours.forEachIndexed { hour, value ->
                        // 막대가 가늘고 0걸음인 시간은 바닥 선뿐이라, 막대가 아니라 그 시간의 칸 전체가 눌린다
                        Box(Modifier.weight(1f).fillMaxHeight().clickable { picked = if (picked == hour) null else hour }, contentAlignment = Alignment.BottomCenter) {
                            val color = when {
                                value == 0.0 -> Link.Border
                                hour == peak.first -> Link.Primary
                                else -> Link.CoralMain
                            }
                            Box(
                                Modifier.fillMaxWidth().height(barHeight(value, peak.second))
                                    .background(color, RoundedCornerShape(topStart = 2.dp, topEnd = 2.dp))
                                    .then(if (picked == hour) Modifier.background(Color.Black.copy(alpha = 0.25f), RoundedCornerShape(topStart = 2.dp, topEnd = 2.dp)) else Modifier),
                            )
                        }
                    }
                }
                picked?.let { hour ->
                    // 팝업은 막대 바로 위에 띄운다. 가로 자리는 차트 폭에서 그 시간이 놓인 비율만큼 팝업 자신도 왼쪽으로 당겨,
                    // 0시는 왼쪽 끝·23시는 오른쪽 끝에 맞고 그 사이는 차츰 옮겨 간다. 차트 밖으로 나가지 않는다
                    val share = (hour + 0.5f) / 24f
                    var popup by remember { mutableStateOf(0) }
                    val density = LocalDensity.current
                    val widthPx = with(density) { maxWidth.toPx() }
                    val lift = with(density) { (barHeight(hours[hour], peak.second) + 6.dp).toPx() }
                    Column(
                        Modifier.align(Alignment.BottomStart)
                            .offset { IntOffset((widthPx * share - popup * share).roundToInt(), -lift.roundToInt()) }
                            .onSizeChanged { popup = it.width }
                            .background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape).padding(horizontal = 12.dp, vertical = 8.dp),
                    ) {
                        Text(V.value("steps", hours[hour]), fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 1, softWrap = false)
                        Text("${hour}시", fontSize = 12.sp, color = Link.Sub, maxLines = 1)
                    }
                }
            }
        } else {
            // 기록이 없거나 불러오지 못한 날은 바닥 선만 둔다(값이 0인 시간과 같은 모양)
            Row(Modifier.fillMaxWidth().height(barArea).clearAndSetSemantics { }, horizontalArrangement = Arrangement.spacedBy(1.dp), verticalAlignment = Alignment.Bottom) {
                repeat(24) { Box(Modifier.weight(1f).height(2.dp).background(Link.Border, RoundedCornerShape(topStart = 2.dp, topEnd = 2.dp))) }
            }
        }
        Row(Modifier.fillMaxWidth().clearAndSetSemantics { }) {
            listOf(0, 6, 12, 18).forEach { Text("${it}시", fontSize = 11.sp, color = Link.Sub, modifier = Modifier.weight(1f)) }
        }
        Text("지금 저장된 기록 기준이에요.", fontSize = 11.sp, color = Link.Sub)
    }
}

/** 수면 단계 막대의 최대 폭과, 미는 차트에서 칸 좌우에 비우는 폭 */
private val StageBarMax = 48.dp
private val StageBarInset = 4.dp

/** 수면 단계 색. 웹과 같다: 얕은 잠(옅은 코랄) · 렘수면(코랄) · 깊은 잠(진한 레드) */
private fun stageColor(stage: V.Stage): Color = when (stage) {
    V.Stage.LIGHT -> Link.Coral
    V.Stage.REM -> Link.CoralMain
    V.Stage.DEEP -> Link.Primary
}

/**
 * 날짜별 수면 단계 차트(웹 SleepStageBars). 밤마다 얕은 잠·렘수면·깊은 잠을 아래부터 깊은 잠 순으로 쌓는다.
 * 일별 차트 공통 규칙을 따른다: 처음에는 최근 일주일, 일주일을 넘으면 가로로 밀어 보고 오른쪽 위에서 기간 전체로 바꾼다.
 * 세로축은 기간 전체 기준이라 밀어도 눈금이 바뀌지 않는다.
 * 단계 구간을 하나씩 누르면 그 구간 옆에 단계 이름·시간·날짜가 팝업으로 뜬다(웹은 마우스를 올리면 뜬다).
 * 처음에는 막대를 통째로 고르고 세 단계 시간을 차트 위 한 줄에 적었는데, 어느 구간이 얼마인지 눈으로 짝짓기 어려웠다.
 * 같은 구간을 다시 누르거나 차트의 빈 곳을 누르거나 차트를 밀면 닫힌다
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun StageChart(columns: List<V.StageColumn>, whole: Boolean, onWhole: (Boolean) -> Unit) {
    val scale = remember(columns) { V.chartScale("sleeps", columns.map { it.total.takeIf { total -> total > 0 } }, null) }
    // 고른 구간: 날짜와 단계
    var selected by remember(columns) { mutableStateOf<Pair<String, V.Stage>?>(null) }
    val available = columns.size > V.VISIBLE_DAYS
    val scrolls = available && !whole
    val gap = if (scrolls) 0.dp else if (columns.size > 14) 1.dp else 8.dp
    val scroll = rememberScrollState()
    var viewport by remember { mutableStateOf(0) }
    var placed by remember(columns, whole) { mutableStateOf(false) }
    val valued = remember(columns) { columns.map { it.bar.copy(value = it.total.takeIf { total -> total > 0 }) } }
    LaunchedEffect(scroll.maxValue, viewport, columns, whole) {
        if (scrolls && !placed && scroll.maxValue > 0 && viewport > 0) {
            scroll.scrollTo((V.firstVisible(valued) * viewport / V.VISIBLE_DAYS).coerceAtMost(scroll.maxValue))
            placed = true
        }
    }
    val description = remember(columns) { "날짜별 수면 단계. " + columns.joinToString(". ") { "${it.bar.title} ${V.stageText(it)}" } }
    // 차트를 밀면 팝업이 가리키던 구간이 자리를 옮기므로 닫는다
    LaunchedEffect(scroll.isScrollInProgress) { if (scroll.isScrollInProgress) selected = null }

    Column(Modifier.fillMaxWidth().background(Link.Surface, BoxShape).padding(start = 12.dp, end = 12.dp, top = 16.dp, bottom = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            FlowRow(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                listOf(V.Stage.DEEP, V.Stage.REM, V.Stage.LIGHT).forEach { stage ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        Box(Modifier.size(10.dp).background(stageColor(stage), RoundedCornerShape(3.dp)))
                        Text(stage.label, fontSize = 12.sp, color = Link.Text)
                    }
                }
            }
            if (available) RangeToggle(whole, V.wholeViewLabel(columns.size)) { onWhole(it); selected = null }
        }
        Spacer(Modifier.height(20.dp))
        Row(Modifier.fillMaxWidth().clearAndSetSemantics { contentDescription = description }) {
            BoxWithConstraints(Modifier.width(AxisWidth).height(ChartHeight)) {
                scale.ticks.forEach { tick ->
                    Text(
                        V.axisValue("sleeps", tick), fontSize = 12.sp, lineHeight = 16.sp, color = Link.Text, textAlign = TextAlign.End, maxLines = 1, softWrap = false,
                        modifier = Modifier.fillMaxWidth().offset(y = maxHeight * (1f - scale.fraction(tick)) - 8.dp),
                    )
                }
            }
            Spacer(Modifier.width(AxisGap))
            BoxWithConstraints(Modifier.weight(1f).onSizeChanged { viewport = it.width }) {
                val contentWidth = if (scrolls) maxWidth / V.VISIBLE_DAYS * columns.size else maxWidth
                Column(if (scrolls) Modifier.horizontalScroll(scroll) else Modifier) {
                    Box(Modifier.width(contentWidth).height(ChartHeight)) {
                        Canvas(Modifier.fillMaxSize()) {
                            scale.ticks.forEach { tick ->
                                val y = size.height * (1f - scale.fraction(tick))
                                drawLine(Link.Border, Offset(0f, y), Offset(size.width, y), strokeWidth = 1.dp.toPx())
                            }
                        }
                        Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(gap), verticalAlignment = Alignment.Bottom) {
                            columns.forEach { column ->
                                // 칸의 빈 곳을 누르면 팝업을 닫는다(눌린 표시는 두지 않는다). 구간은 아래에서 따로 눌린다
                                Box(
                                    Modifier.weight(1f).fillMaxHeight()
                                        .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) { selected = null },
                                    contentAlignment = Alignment.BottomCenter,
                                ) {
                                    if (column.total > 0) {
                                        Column(Modifier.padding(horizontal = if (scrolls) StageBarInset else 0.dp).widthIn(max = StageBarMax).fillMaxWidth().clip(RoundedCornerShape(6.dp))) {
                                            column.parts.forEach { part ->
                                                val id = column.bar.key to part.stage
                                                // 구간 하나가 버튼이다. 고른 구간은 안쪽에 테두리를 그려 칸 밖으로 잘리지 않게 한다(웹과 같다)
                                                Box(
                                                    Modifier.fillMaxWidth().height(ChartHeight * scale.fraction(part.minutes)).background(stageColor(part.stage))
                                                        .clickable { selected = if (selected == id) null else id }
                                                        .then(if (selected == id) Modifier.border(2.dp, Link.Text) else Modifier),
                                                )
                                            }
                                        }
                                    }
                                    // 단계 기록이 없는 밤(일부 워치)은 막대를 비운다. 없는 값을 0이나 점선 칸으로 그리지 않는다(웹과 같다).
                                    // 점선 칸은 일별 차트에서 "기록 없음"이라는 뜻인데, 이 밤은 수면 기록은 있고 단계만 없다
                                }
                            }
                        }
                    }
                    Row(Modifier.width(contentWidth).padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(gap)) {
                        columns.forEachIndexed { index, column ->
                            Box(Modifier.weight(1f).height(16.dp), contentAlignment = Alignment.Center) {
                                val show = if (scrolls) (columns.lastIndex - index) % 2 == 0 else column.bar.showLabel
                                if (show) Text(column.bar.axisLabel, fontSize = 12.sp, lineHeight = 16.sp, color = Link.Text, maxLines = 1, softWrap = false, modifier = Modifier.wrapContentWidth(unbounded = true))
                            }
                        }
                    }
                }
                // 고른 구간 옆에 뜨는 팝업(웹 SleepStageBars 와 같은 내용: 단계 이름, 시간, 날짜).
                // 막대 오른쪽에 두고 자리가 없으면 왼쪽으로 넘긴다. 세로는 구간 가운데에 맞추되 차트 위아래로 나가지 않게 가둔다
                val index = columns.indexOfFirst { it.bar.key == selected?.first }
                val column = columns.getOrNull(index)
                val part = column?.parts?.firstOrNull { it.stage == selected?.second }
                if (column != null && part != null) {
                    var popup by remember { mutableStateOf(IntSize.Zero) }
                    val viewportWidth = maxWidth
                    val cell = (contentWidth - gap * (columns.size - 1)) / columns.size
                    val barWidth = (cell - (if (scrolls) StageBarInset * 2 else 0.dp)).coerceAtMost(StageBarMax)
                    val center = (cell + gap) * index + cell / 2
                    // 막대는 위에서부터 얕은 잠·렘수면·깊은 잠 순으로 놓인다. 고른 구간 위에 있는 구간들의 높이를 더해 구간 가운데를 구한다
                    val above = column.parts.takeWhile { it.stage != part.stage }.sumOf { it.minutes }
                    val middle = ChartHeight * (1f - scale.fraction(column.total)) + ChartHeight * scale.fraction(above) + ChartHeight * scale.fraction(part.minutes) / 2
                    Column(
                        Modifier
                            .offset {
                                val scrolled = if (scrolls) scroll.value else 0
                                val right = (center + barWidth / 2 + 6.dp).toPx() - scrolled
                                val left = (center - barWidth / 2 - 6.dp).toPx() - scrolled - popup.width
                                val room = viewportWidth.toPx() - popup.width
                                val x = (if (right <= room) right else left).coerceIn(0f, room.coerceAtLeast(0f))
                                val y = (middle.toPx() - popup.height / 2f).coerceIn(0f, (ChartHeight.toPx() - popup.height).coerceAtLeast(0f))
                                IntOffset(x.roundToInt(), y.roundToInt())
                            }
                            .onSizeChanged { popup = it }
                            // 크기를 재기 전 한 장면은 자리가 어긋나므로 보이지 않게 둔다
                            .alpha(if (popup == IntSize.Zero) 0f else 1f)
                            .shadow(8.dp, InnerShape)
                            .background(Link.Background, InnerShape).border(1.dp, Link.Border, InnerShape)
                            .padding(horizontal = 12.dp, vertical = 8.dp)
                            .clearAndSetSemantics { },
                        verticalArrangement = Arrangement.spacedBy(2.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Box(Modifier.size(8.dp).background(stageColor(part.stage), CircleShape))
                            Text(part.stage.label, fontSize = 12.sp, lineHeight = 16.sp, color = Link.Text, maxLines = 1, softWrap = false)
                        }
                        Text(V.value("sleeps", part.minutes), fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.Bold, color = Link.Text, maxLines = 1, softWrap = false)
                        Text(column.bar.title, fontSize = 12.sp, lineHeight = 16.sp, color = Link.Sub, maxLines = 1, softWrap = false)
                    }
                }
            }
        }
        // 기간 전체 보기에서도 아래 위치 막대의 높이를 비워 둔다. 보기를 바꿀 때 상자 높이가 달라지지 않게 한다(일별 차트와 같다)
        if (available) {
            Spacer(Modifier.height(10.dp))
            if (scrolls) {
                BoxWithConstraints(Modifier.fillMaxWidth().padding(start = AxisWidth + AxisGap).height(4.dp).background(Link.Border, RoundedCornerShape(50)).clearAndSetSemantics { }) {
                    val share = (V.VISIBLE_DAYS.toFloat() / columns.size).coerceAtMost(1f)
                    val progress = if (scroll.maxValue > 0) scroll.value.toFloat() / scroll.maxValue else 1f
                    Box(Modifier.offset(x = maxWidth * (1f - share) * progress).width(maxWidth * share).fillMaxHeight().background(Link.CoralMain, RoundedCornerShape(50)))
                }
            } else {
                Spacer(Modifier.height(4.dp))
            }
        }
    }
}

/** 값이 없는 날의 점선 칸. 비워 두면 그 날이 화면에서 사라져 보이고, 낮은 막대로 채우면 조금 한 날과 구분되지 않는다(웹과 같다) */
@Composable
internal fun EmptyDayBox(modifier: Modifier = Modifier) {
    Canvas(modifier.height(12.dp)) {
        drawRoundRect(
            Link.Muted.copy(alpha = 0.6f), cornerRadius = androidx.compose.ui.geometry.CornerRadius(6.dp.toPx()),
            style = Stroke(1.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(4.dp.toPx(), 3.dp.toPx()))),
        )
    }
}
