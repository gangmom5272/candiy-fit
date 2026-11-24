package io.candiy.healthconnect.quickstart.coaching

import java.time.DayOfWeek
import java.time.LocalDate
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.roundToLong

/*
 * 근거 화면에 쓰는 계산과 표시 글자(S15P31A403-360). 화면 코드 없이 값만 다뤄 단위 테스트로 지킨다.
 * 웹 frontend/web/src/lib/features/coaching 의 evidence.ts · evidence-view.ts · chart.ts · format.ts 와 같은 규칙이다.
 * 웹과 답이 달라지면 같은 답변의 근거가 기기마다 다르게 보이므로, 규칙을 바꿀 때는 웹과 함께 바꾼다.
 */
object EvidenceView {
    private val KST: ZoneId = ZoneId.of("Asia/Seoul")
    private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")

    /** 기록이 이만큼은 있어야 평균선·최저 색이 뜻을 가진다 */
    const val MIN_DAYS_FOR_TREND = 2
    /** 이보다 길면 막대를 주 단위로 묶는다 */
    const val MAX_DAILY_BARS = 31
    /** 날짜를 모두 나열하는 최대 일수. 넘으면 주요 날짜만 먼저 보여 준다 */
    const val MAX_LISTED_DAYS = 14
    /** 기간 비교를 믿을 만하다고 보는 기준. AI 의 판단 기준과 같다(3일 이상이면서 대상 날짜의 절반 이상) */
    const val RELIABLE_MIN_DAYS = 3
    const val RELIABLE_MIN_SHARE = 0.5
    /** 처음부터 펼쳐 둘 운동 기록 수 */
    const val MAX_LISTED_SESSIONS = 8
    /** 펼치기 전에 보여 줄 산소포화도 시각 수 */
    const val SPO2_TIMES_PREVIEW = 3

    val METRIC_LABELS = mapOf(
        "sleeps" to "수면", "steps" to "활동", "heart_rates" to "심박", "oxygen_saturations" to "산소포화도", "exercises" to "운동",
        "weights" to "체중", "body_fats" to "체지방률", "basal_metabolic_rates" to "기초대사량", "heights" to "키",
    )
    val AVERAGE_LABELS = mapOf(
        "sleeps" to "평균 수면", "steps" to "평균 걸음 수", "heart_rates" to "평균 심박", "oxygen_saturations" to "평균 산소포화도",
        "exercises" to "평균 운동 시간", "weights" to "평균 체중", "body_fats" to "평균 체지방률",
        "basal_metabolic_rates" to "평균 기초대사량", "heights" to "평균 키",
    )
    /** 기록이 하루뿐일 때 카드 제목. 평균이라고 부르면 여러 날을 모은 값처럼 읽힌다 */
    val DAY_LABELS = mapOf(
        "sleeps" to "수면 시간", "steps" to "걸음 수", "heart_rates" to "평균 심박", "oxygen_saturations" to "산소포화도",
        "exercises" to "운동 시간", "weights" to "체중", "body_fats" to "체지방률", "basal_metabolic_rates" to "기초대사량", "heights" to "키",
    )
    val CHART_TITLES = mapOf(
        "sleeps" to "일별 수면 시간", "steps" to "일별 걸음 수", "heart_rates" to "일별 평균 심박", "oxygen_saturations" to "일별 산소포화도",
        "exercises" to "일별 운동 시간", "weights" to "일별 체중", "body_fats" to "일별 체지방률",
        "basal_metabolic_rates" to "일별 기초대사량", "heights" to "일별 키",
    )
    val UNIT_LABELS = mapOf(
        "sleeps" to "시간", "steps" to "걸음", "heart_rates" to "bpm", "oxygen_saturations" to "%", "exercises" to "분",
        "weights" to "kg", "body_fats" to "%", "basal_metabolic_rates" to "kcal", "heights" to "cm",
    )
    val MISSING_REASON_LABELS = mapOf(
        "NO_RECORD" to "기록 없음", "NULL_VALUE" to "값 없음", "ZERO_STEPS" to "0걸음", "UNSYNCED" to "동기화 전", "BEFORE_LINK" to "연동 전",
    )

    // ── 글자 형식 ─────────────────────────────────────────────────────────────

    private fun grouped(value: Double): String = String.format(Locale.KOREA, "%,d", value.roundToLong())

    private fun oneDecimal(value: Double): String =
        String.format(Locale.US, "%.1f", (value * 10).roundToLong() / 10.0).removeSuffix(".0")

    private fun minutes(value: Double): String {
        val rounded = abs(value).roundToInt()
        val hours = rounded / 60
        val rest = rounded % 60
        return when {
            hours == 0 -> "${rest}분"
            rest == 0 -> "${hours}시간"
            else -> "${hours}시간 ${rest}분"
        }
    }

    /** 키 m → cm. 1.725 × 100 의 소수 오차를 없앤다 */
    fun metersToCentimeters(meters: Double): Double = (meters * 1000).roundToLong() / 10.0

    /** 지표 값을 단위와 함께 표시한다. 값이 없으면 0 이 아닌 "-" 다 */
    fun value(metric: String, value: Double?): String {
        if (value == null) return "-"
        return when (metric) {
            "sleeps", "exercises" -> minutes(value)
            // AI 답변과 같은 단위로 쓴다(S15P31A403-427)
            "steps" -> "${grouped(value)}걸음"
            // AI 답변은 심박을 소수 첫째 자리까지 쓴다. 정수로 반올림하면 답변 73.8bpm 과 화면 74bpm 처럼 달라 보인다(웹 S15P31A403-420)
            "heart_rates" -> "${oneDecimal(value)}bpm"
            "oxygen_saturations" -> "${String.format(Locale.US, "%.1f", value)}%"
            // 신체 지표는 그날 마지막 측정값이다. 소수 한 자리까지, .0 은 뺀다
            "weights" -> "${oneDecimal(value)}kg"
            "body_fats" -> "${oneDecimal(value)}%"
            "basal_metabolic_rates" -> "${grouped(value)}kcal"
            // 키는 m 로 저장·전달되고 화면에서만 cm 로 바꾼다
            "heights" -> "${oneDecimal(metersToCentimeters(value))}cm"
            else -> oneDecimal(value)
        }
    }

    /** 이전 기간 대비 차이. 부호를 붙인다 */
    fun delta(metric: String, delta: Double): String {
        val sign = if (delta > 0) "+" else if (delta < 0) "-" else ""
        return sign + value(metric, abs(delta))
    }

    /** `2026-10-07` → `10.07` */
    fun shortDate(date: String): String = date.split("-").let { "${it[1]}.${it[2]}" }

    private val WEEKDAYS = mapOf(
        DayOfWeek.SUNDAY to "일", DayOfWeek.MONDAY to "월", DayOfWeek.TUESDAY to "화", DayOfWeek.WEDNESDAY to "수",
        DayOfWeek.THURSDAY to "목", DayOfWeek.FRIDAY to "금", DayOfWeek.SATURDAY to "토",
    )

    /** `2026-10-23` → `10.23(금)`. 근거 날짜는 KST 달력 날짜다 */
    fun dayWithWeekday(date: String): String = "${shortDate(date)}(${WEEKDAYS[LocalDate.parse(date).dayOfWeek]})"

    /** 하루면 `2026.10.27`, 아니면 `2026.10.22 ~ 10.28`. 해가 다르면 끝 날짜에도 연도를 붙인다 */
    fun period(start: String, end: String): String {
        val startText = start.replace("-", ".")
        if (start == end) return startText
        val endText = if (end.take(4) == start.take(4)) shortDate(end) else end.replace("-", ".")
        return "$startText ~ $endText"
    }

    /** 시각 → `14:30` (KST). Z·+09:00 등 오프셋이 붙은 시각을 모두 읽는다 */
    fun clock(iso: String): String =
        runCatching { OffsetDateTime.parse(iso).atZoneSameInstant(KST).format(CLOCK) }.getOrDefault("-")

    /**
     * 잠든 시각으로 붙이는 수면 라벨(계산 규칙 11.1). KST 18~24시 → 그날 밤, 0~6시 → 전날 밤, 6~10시 → 그날 아침, 10~18시 → 그날 낮.
     * 조회 날짜(깬 날)와 다를 수 있다
     */
    fun sleepLabel(startAt: String): String {
        val at = runCatching { OffsetDateTime.parse(startAt).atZoneSameInstant(KST) }.getOrNull() ?: return "-"
        val day = if (at.hour < 6) at.toLocalDate().minusDays(1) else at.toLocalDate()
        val part = if (at.hour < 6 || at.hour >= 18) "밤" else if (at.hour < 10) "아침" else "낮"
        return "${day.monthValue}월 ${day.dayOfMonth}일 $part"
    }

    // ── 지표별 묶음과 비교 ────────────────────────────────────────────────────

    /**
     * @property current 화면에 보여 줄 기간. 나눠 조회한 기록을 합친 묶음이면 처음 날부터 마지막 날까지를 이은 근거다
     *   (평균은 다시 계산하지 않아 avg 가 null 이다. 기간별 평균은 parts 에 있다)
     * @property parts AI 가 실제로 조회한 기간들(시작 순). 둘 이상이면 current 가 이들을 합친 것이다(S15P31A403-368)
     */
    data class Group(
        val metric: String,
        val current: MetricEvidence,
        val comparison: MetricEvidence?,
        val parts: List<MetricEvidence>,
        /**
         * 다른 기간과 날짜가 겹쳐 기간 구분에서 뺀 근거(시작 순). AI 가 받은 평균은 그대로라 화면에 "함께 조회한 기간"으로 적는다.
         * 웹 evidence.ts 의 overlaps 와 같다(웹 418·439, 앱 440)
         */
        val overlaps: List<MetricEvidence> = emptyList(),
    ) {
        /** 나눠 조회한 기록을 합친 묶음인지 */
        val merged get() = parts.size > 1
    }

    /** 한 번에 조회할 수 있는 최대 일수(AI 도구 제한) */
    private const val MAX_QUERY_DAYS = 31L
    /** AI 가 나눠 조회하는 가장 긴 기간. 이보다 길면 나눠 조회하지 않는다 */
    private const val MAX_SPLIT_DAYS = 49L

    private fun dayCount(e: MetricEvidence): Long =
        java.time.temporal.ChronoUnit.DAYS.between(LocalDate.parse(e.periodStart), LocalDate.parse(e.periodEnd)) + 1

    /**
     * 날짜가 이어진 두 기간이 "나눠 부른 한 기간"인지. 근거에는 두 기간의 관계가 실려 오지 않아 모양으로 가린다.
     * AI 도구는 31일을 넘고 49일 이하인 기간만 둘로 나누고 끝 날짜부터 31일을 자른다. 그래서 나눠 부른 모양은
     * 뒤 기간이 정확히 31일, 앞 기간이 남는 1~18일 하나뿐이다(42일이면 11일 + 31일).
     * 그 밖의 두 기간은 길이가 달라도 비교다(수요일의 "이번 주랑 지난주" 7일·3일, 월 중의 "이번 달이랑 지난달").
     * 웹 evidence.ts isSplitPair 와 같다(S15P31A403-374, AI 담당 확인)
     */
    private fun isSplitPair(earlier: MetricEvidence, later: MetricEvidence): Boolean {
        if (LocalDate.parse(earlier.periodEnd).plusDays(1) != LocalDate.parse(later.periodStart)) return false
        return dayCount(later) == MAX_QUERY_DAYS && dayCount(earlier) <= MAX_SPLIT_DAYS - MAX_QUERY_DAYS
    }

    /**
     * 나눠 조회한 기간들을 한 범위로 잇는다. 날짜별 값·밤·운동 기록은 이어 붙이고(같은 날짜가 겹치면 뒤 기간 값),
     * 최저·최고는 각 기간이 준 값 중에서 고른다. 평균은 앱에서 다시 계산하지 않는다 — AI 가 답변에 쓴 숫자와
     * 어긋날 수 있어 조회한 기간별 평균을 그대로 보여 준다
     */
    private fun merge(parts: List<MetricEvidence>): MetricEvidence {
        val last = parts.last()
        val snapshots = parts.map { it.snapshot }
        // 조회한 기간이 서로 겹칠 수 있다(수면 분석이 14일·7일·28일을 함께 부르는 경우 등, S15P31A403-391).
        // 날짜별 값·밤·운동 기록을 그대로 이어 붙이면 같은 기록이 두 번 들어가고 기록 일수도 두 번 세어진다.
        // 겹치는 기록은 하나만 남기고(뒤 기간 값), 일수는 합친 날짜에서 다시 센다. 웹 evidence.ts mergeParts 와 같다(317)
        val days = LinkedHashMap<String, EvidenceDay>().apply { snapshots.forEach { s -> s.days.forEach { put(it.date, it) } } }
            .values.sortedBy { it.date }
        // 밤은 깬 날짜마다 주 수면 하나다
        val nights = LinkedHashMap<String, SleepNight>().apply { snapshots.forEach { s -> s.nights.forEach { put(it.date, it) } } }
            .values.sortedBy { it.date }
        // 운동 기록은 시작 시각이 같으면 같은 기록이다. Z 와 +09:00 이 섞여 와도 같은 시각으로 본다
        fun startKey(session: ExerciseSession): Any =
            runCatching { java.time.OffsetDateTime.parse(session.startAt).toInstant() }.getOrNull() ?: session.startAt
        val sessions = LinkedHashMap<Any, ExerciseSession>().apply { snapshots.forEach { s -> s.sessions.forEach { put(startKey(it), it) } } }
            .values.sortedBy { session -> (startKey(session) as? java.time.Instant)?.toEpochMilli() ?: Long.MAX_VALUE }
        // 통계 대상은 진행 중인 오늘과 연동 전 날짜를 뺀 날, 그중 값이 있는 날이 기록된 날이다(명세 completed_days · days_with_data)
        val completed = days.filter { !it.inProgress && it.reason != "BEFORE_LINK" }
        return MetricEvidence(
            id = parts.joinToString("+") { it.id },
            position = parts.minOf { it.position },
            metric = last.metric,
            periodStart = parts.first().periodStart,
            periodEnd = parts.maxOf { it.periodEnd },
            snapshot = MetricSnapshot(
                avg = null,
                dailyMin = snapshots.mapNotNull { it.dailyMin }.minByOrNull { it.value },
                dailyMax = snapshots.mapNotNull { it.dailyMax }.maxByOrNull { it.value },
                daysWithData = completed.count { !it.missing && it.value != null },
                completedDays = completed.size,
                unsyncedDays = days.count { it.reason == "UNSYNCED" },
                days = days,
                inProgress = last.snapshot.inProgress,
                measuredMin = snapshots.mapNotNull { it.measuredMin }.minByOrNull { it.value },
                measuredMax = snapshots.mapNotNull { it.measuredMax }.maxByOrNull { it.value },
                nights = nights,
                sessions = sessions,
                // 그날 측정 최저·최고도 이어 붙인다. 빠뜨리면 나눠 조회한 기간의 달력에서만 그날 상세에 이 값이 안 나온다(웹 mergeParts 와 같다)
                dailyRanges = snapshots.flatMap { it.dailyRanges },
                // 날짜별 값은 이어 붙이고 합계·평균은 두지 않는다. 수집 실패 표시는 한 기간이라도 실패했으면 남긴다
                activity = snapshots.mapNotNull { it.activity }.takeIf { it.isNotEmpty() }?.let { list ->
                    ExerciseActivity(
                        days = LinkedHashMap<String, ActivityDay>().apply { list.forEach { a -> a.days.forEach { put(it.date, it) } } }.values.sortedBy { it.date },
                        summary = null,
                        failedSources = list.flatMap { it.failedSources }.distinct(),
                    )
                },
            ),
        )
    }

    // ----- 운동 횟수·칼로리·거리(S15P31A403-375). 웹 evidence-view.ts exerciseActivityView 와 같다 -----

    enum class ActivityField(val label: String) { COUNT("운동 횟수"), CALORIES("운동 칼로리"), DISTANCE("운동 거리") }

    /** 날짜별 표를 접지 않고 보여 주는 최대 일수 */
    const val MAX_LISTED_ACTIVITY_DAYS = 7

    private val ACTIVITY_SOURCE_LABELS = mapOf("exercise_sessions" to "운동 횟수", "calories_burned" to "칼로리", "distances" to "거리")

    /**
     * AI 가 답변에 쓰는 표시와 같게 맞춘다: 횟수·칼로리는 반올림한 정수, 거리는 m 를 km 소수 한 자리로.
     * [average]면 횟수만 소수 한 자리까지 둔다(하루 평균 0.4회를 0회로 쓰면 운동을 안 한 것처럼 읽힌다)
     */
    fun activityValue(field: ActivityField, value: Double, average: Boolean = false): String = when (field) {
        ActivityField.COUNT -> (if (average) oneDecimal(value) else grouped(value)) + "회"
        ActivityField.CALORIES -> grouped(value) + "kcal"
        ActivityField.DISTANCE -> String.format(Locale.KOREA, "%.1f", (value / 100).roundToLong() / 10.0) + "km"
    }

    /** captions 는 한 줄씩 따로 놓는다 */
    data class ActivityCard(val label: String, val value: String, val captions: List<String>)
    /** cells 는 횟수·칼로리·거리 순서. 값이 없으면 이유(연동 전·동기화 전·기록 없음)를 글로 둔다 */
    data class ActivityRow(val date: String, val inProgress: Boolean, val cells: List<String>)
    data class ActivityView(val cards: List<ActivityCard>, val rows: List<ActivityRow>, val notes: List<String>)

    private fun activityCell(field: ActivityField, value: Double?, reason: String?): String = when {
        value != null -> activityValue(field, value)
        reason == "BEFORE_LINK" -> "연동 전"
        reason == "UNSYNCED" -> "동기화 전"
        reason == null -> "-"
        else -> "기록 없음"
    }

    /**
     * 합계·평균은 서버가 낸 값을 그대로 쓴다. 오늘(수집 중)은 합계에서 빠져 있어, 합계가 없고 오늘 값만 있으면 그 값을 보여 준다.
     * 값이 실려 오지 않은 근거(예전 근거·다른 지표)는 null 이다
     */
    fun activity(s: MetricSnapshot): ActivityView? {
        val activity = s.activity?.takeIf { it.days.isNotEmpty() } ?: return null
        val days = activity.days.sortedBy { it.date }
        val today = days.firstOrNull { it.inProgress }
        val summary = activity.summary
        val cards = if (summary == null) emptyList() else listOf(
            Triple(ActivityField.COUNT, summary.count, today?.count),
            Triple(ActivityField.CALORIES, summary.calories, today?.calories),
            Triple(ActivityField.DISTANCE, summary.distance, today?.distance),
        ).map { (field, stat, todayValue) ->
            when {
                stat.total != null -> ActivityCard(
                    field.label, activityValue(field, stat.total),
                    listOfNotNull(
                        if (stat.daysWithData > 1 && stat.avg != null) "하루 평균 ${activityValue(field, stat.avg, average = true)}" else null,
                        "기록 ${stat.daysWithData}일",
                    ),
                )
                todayValue != null -> ActivityCard("오늘 ${field.label}", activityValue(field, todayValue), listOf("수집 중"))
                else -> ActivityCard(field.label, "-", listOf("기록 없음"))
            }
        }
        val failed = activity.failedSources.mapNotNull { ACTIVITY_SOURCE_LABELS[it] }
        // 횟수·칼로리·거리가 모두 비어 있으면 "-"만 세 칸인 표가 되어 "기록이 없어요" 안내와 같은 말을 한 번 더 하게 된다.
        // 수집 실패를 알릴 것도 없으면 묶음을 두지 않는다(웹 439)
        if (cards.isNotEmpty() && cards.all { it.value == "-" } && failed.isEmpty()) return null
        val notes = buildList {
            add("횟수는 운동으로 인정된 기록만 세요.")
            add("칼로리·거리는 운동으로 인정되지 않은 기록까지 포함한 값이에요. 하루 전체 소모 칼로리나 이동 거리가 아니에요.")
            if (summary == null) add("합계와 평균은 조회한 기간별로 받아, 여기서 다시 더하지 않았어요.")
            if (today != null && summary != null && days.size > 1) add("오늘 값은 수집 중이며 합계·평균에서 제외해요.")
            if (failed.isNotEmpty()) add("${failed.joinToString("·")} 수집에 실패해 이전에 저장된 기록 기준이에요.")
        }
        return ActivityView(
            cards,
            days.map {
                ActivityRow(
                    it.date, it.inProgress,
                    listOf(
                        activityCell(ActivityField.COUNT, it.count, it.reason),
                        activityCell(ActivityField.CALORIES, it.calories, it.caloriesReason),
                        activityCell(ActivityField.DISTANCE, it.distance, it.distanceReason),
                    ),
                )
            },
            notes,
        )
    }

    /** 서로 겹치지 않고 날짜가 이어지는 다른 기간들만으로 이 기간의 모든 날이 덮이는지 */
    private fun isCoveredBy(item: MetricEvidence, others: List<MetricEvidence>): Boolean {
        fun reach(from: String, first: Boolean): Boolean = others.any { other ->
            val starts = if (first) other.periodStart <= from else other.periodStart == from
            if (!starts || other.periodEnd < from) false
            else other.periodEnd >= item.periodEnd || reach(LocalDate.parse(other.periodEnd).plusDays(1).toString(), false)
        }
        return reach(item.periodStart, true)
    }

    /**
     * 조회한 기간들을 겹치지 않는 구분과 거기에 겹치는 기간으로 가른다. 긴 기간부터 보아, 나머지 기간들로 모든 날이 덮이는 기간을 뺀다.
     * - 이전 30일·최근 30일·두 달 전체 → 두 달 전체가 빠지고 30일 둘이 남는다(비교)
     * - 6주 전체·지난주·이번 주 → 두 주가 빠지고 6주가 남는다
     * 일부만 걸친 기간은 어느 쪽도 덮이지 않아 그대로 남는다. 웹 evidence.ts splitOverlaps 와 같다
     */
    private fun splitOverlaps(items: List<MetricEvidence>): Pair<List<MetricEvidence>, List<MetricEvidence>> {
        var parts = items
        val overlaps = mutableListOf<MetricEvidence>()
        for (item in items.sortedByDescending { dayCount(it) }) {
            val others = parts.filter { it !== item }
            if (others.isNotEmpty() && isCoveredBy(item, others)) {
                parts = others
                overlaps += item
            }
        }
        return parts to overlaps
    }

    /**
     * 날짜별 지표 근거를 지표별로 묶는다. 순서는 각 지표가 답변에서 처음 나온 위치(position)를 따른다.
     * 같은 지표를 여러 기간으로 조회했을 때:
     *  - 두 기간이면 늦게 끝나는 쪽이 현재, 앞쪽이 비교다(이번 주와 지난주).
     *  - 두 기간이 일부만 겹치면 비교가 아니다. 긴 쪽을 보여 주고 짧은 쪽은 overlaps 에 둔다. 길이가 같으면 늦게 끝나는 쪽이다(웹 439).
     *    전에는 늦게 끝나는 쪽을 보여 줘서, 심박을 28일과 최근 7일로 함께 조회하면 심박 탭만 7일이고 다른 탭은 28일이었다
     *  - 다만 두 기간이 한 기간을 나눠 부른 모양이면 한 범위로 합친다(31일 제한으로 둘로 나눈 긴 기간).
     *  - 세 기간 이상이면 모두 합쳐 처음 날부터 마지막 날까지 보여 준다(주별로 여러 번 조회).
     * 같은 기간을 두 번 조회한 근거는 하나로 센다. 다른 기간들과 날짜가 통째로 겹치는 기간은 먼저 빼서 overlaps 에 둔다(웹 418).
     * 웹 evidence.ts groupEvidenceByMetric 과 같은 규칙이다
     */
    fun groups(evidence: List<Evidence>): List<Group> =
        evidence.filterIsInstance<MetricEvidence>().sortedBy { it.position }.groupBy { it.metric }.map { (metric, items) ->
            val byStart = compareBy<MetricEvidence>({ it.periodStart }, { it.periodEnd })
            val (parts, covered) = splitOverlaps(items.distinctBy { it.periodStart to it.periodEnd }.sortedWith(byStart))
            val overlaps = covered.sortedWith(byStart)
            when {
                parts.size == 1 -> Group(metric, parts[0], null, parts, overlaps)
                parts.size >= 3 || isSplitPair(parts[0], parts[1]) -> Group(metric, merge(parts), null, parts, overlaps)
                else -> {
                    val (later, earlier) = parts.sortedByDescending { it.periodEnd }
                    if (earlier.periodEnd < later.periodStart) {
                        Group(metric, later, earlier, listOf(later), overlaps)
                    } else {
                        val current = if (dayCount(earlier) > dayCount(later)) earlier else later
                        val left = if (current === later) earlier else later
                        Group(metric, current, null, listOf(current), (overlaps + left).sortedWith(byStart))
                    }
                }
            }
        }

    /**
     * 지표 묶음의 기록 상태. 합친 묶음은 평균이 비어 있으므로 믿을 만한지는 기록된 날 수로만 본다
     * (3일 이상이면서 대상 날짜의 절반 이상)
     */
    fun coverage(group: Group): Coverage {
        val cover = coverage(group.current.snapshot)
        if (!group.merged) return cover
        return cover.copy(isReliable = cover.recordedDays >= RELIABLE_MIN_DAYS && cover.recordedDays >= cover.countedDays * RELIABLE_MIN_SHARE)
    }

    /** 모양이 다른 근거(수면 세션·시간대 걸음). 지표 묶음 뒤에 근거마다 따로 보여 준다 */
    fun kinds(evidence: List<Evidence>): List<Evidence> =
        evidence.filter { it is SleepSessionsEvidence || it is HourlyStepsEvidence }.sortedBy { it.position }

    fun kindLabel(item: Evidence): String = if (item is SleepSessionsEvidence) "수면 기록" else "시간대 걸음"

    /** 현재와 비교 기간의 평균 차이. 어느 한쪽이라도 평균이 없으면 null */
    fun averageDelta(group: Group): Double? {
        val current = group.current.snapshot.avg ?: return null
        val comparison = group.comparison?.snapshot?.avg ?: return null
        return current - comparison
    }

    // ── 기록 상태 ─────────────────────────────────────────────────────────────

    data class TodayValue(val date: String, val value: Double, val asOf: String?)

    data class Coverage(
        /** 통계 대상 날짜 수(명세 completed_days). 연동 전·진행 중인 오늘은 빠진다 */
        val countedDays: Int,
        /** 그중 값이 있는 날 수(명세 days_with_data) */
        val recordedDays: Int,
        /** 기간 전체 날짜 수 */
        val periodDays: Int,
        /** 평균이 있고 기록이 3일 이상이면서 대상 날짜의 절반 이상. 아니면 비교는 참고용이다 */
        val isReliable: Boolean,
        /** 수집 중인 오늘 값. 통계에는 빠지지만 "오늘 몇 걸음?" 답변은 이 값을 쓴다 */
        val today: TodayValue?,
    ) {
        /** 보여 줄 값이 하나라도 있는지. 지난날 기록이 없어도 수집 중인 오늘 값이 있으면 있다 */
        val hasAnyValue get() = recordedDays > 0 || today != null
    }

    fun todayValue(s: MetricSnapshot): TodayValue? {
        val p = s.inProgress
        if (p != null && !p.missing && p.value != null) return TodayValue(p.date, p.value, p.asOf)
        val day = s.days.firstOrNull { it.inProgress && !it.missing } ?: return null
        return day.value?.let { TodayValue(day.date, it, null) }
    }

    fun coverage(s: MetricSnapshot) = Coverage(
        countedDays = s.completedDays,
        recordedDays = s.daysWithData,
        periodDays = s.days.size,
        isReliable = s.avg != null && s.daysWithData >= RELIABLE_MIN_DAYS && s.daysWithData >= s.completedDays * RELIABLE_MIN_SHARE,
        today = todayValue(s),
    )

    /** 근거 카드에서 지표 이름 옆에 붙일 기록 상태. 기록이 부족할 때만 붙인다 */
    fun coverageNote(c: Coverage): String? {
        if (c.recordedDays == 0) return if (c.today == null) "기록 없음" else if (c.periodDays == 1) "수집 중" else "오늘만 수집 중"
        if (c.isReliable || c.periodDays == 1) return null
        return "${c.countedDays}일 중 ${c.recordedDays}일"
    }

    /** 막대를 그릴 만한지. 기간이 하루이거나 기록이 하루 이하, 또는 모두 0 이면 요약과 같은 내용이라 그리지 않는다 */
    fun showsChart(s: MetricSnapshot): Boolean =
        s.days.size > 1 && s.daysWithData >= MIN_DAYS_FOR_TREND && s.dailyMax?.value != 0.0

    /** 차트를 그리지 않은 이유. 값은 있는데 차트가 없으면 빠진 것으로 읽히므로 한 줄로 알린다 */
    fun chartSkipReason(s: MetricSnapshot): String? {
        if (showsChart(s)) return null
        val c = coverage(s)
        if (!c.hasAnyValue) return null
        return when {
            c.periodDays == 1 -> "하루 기록이라 차트 대신 값으로 보여 줘요."
            c.recordedDays == 0 -> "아직 오늘 기록만 있어 차트를 그리지 않았어요."
            c.recordedDays == 1 -> "기록된 날이 하루뿐이라 차트를 그리지 않았어요."
            else -> "모든 날 기록이 0이라 차트 대신 날짜별 기록으로 보여 줘요."
        }
    }

    /** 기록이 하루뿐이면 그 날짜를 붙여 "10.27(화) 수면 시간"처럼, 아니면 평균으로 부른다 */
    fun statTitle(item: MetricEvidence, c: Coverage, averageTitle: String): String {
        if (c.recordedDays != 1) return averageTitle
        val day = item.snapshot.days.firstOrNull { !it.missing && !it.inProgress } ?: return averageTitle
        return "${dayWithWeekday(day.date)} ${DAY_LABELS[item.metric]}"
    }

    /** 비교 기간 카드 아래 기록된 날 수. 하루 기간이거나 다 기록됐으면 쓰지 않는다 */
    fun statCaption(c: Coverage): String? = when {
        c.periodDays == 1 -> null
        c.recordedDays == 0 -> "기록 없음"
        c.recordedDays == c.countedDays -> null
        else -> "${c.countedDays}일 중 ${c.recordedDays}일 기록"
    }

    /** 기간에서 연동 전이라 뺀 날 수 */
    fun beforeLinkDays(s: MetricSnapshot): Int = s.days.count { it.reason == "BEFORE_LINK" }

    // ── 차트 ─────────────────────────────────────────────────────────────────

    data class Bar(
        val key: String,
        /** 축 아래 날짜 글자 */
        val axisLabel: String,
        /** 휴대폰 폭에서 축 글자를 보일지. 처음·가운데·끝 3개만 */
        val showLabel: Boolean,
        /** 눌렀을 때·보조기기용 날짜(범위) */
        val title: String,
        val value: Double?,
        val inProgress: Boolean,
    )

    data class Bars(val weekly: Boolean, val bars: List<Bar>)

    private fun showLabel(index: Int, count: Int) = count <= 3 || index in setOf(0, (count - 1) / 2, count - 1)

    /** 주 단위 막대의 축 글자를 기간("10.13 ~ 10.19")으로 쓰는 최대 주 수. 넘으면 첫날만 쓴다 */
    private const val MAX_WEEK_RANGE_LABELS = 6

    /**
     * 차트 막대. 31일 이하는 하루 한 칸, 더 길면 7일씩 묶은 하루 평균으로 그린다.
     * 신체 지표는 평균을 다시 내지 않고 그 주의 마지막 측정값을 쓴다. 연동 전 날짜는 빼고, 진행 중인 오늘은 주 값에 넣지 않는다
     */
    fun chartBars(metric: String, s: MetricSnapshot): Bars {
        val days = s.days.filter { it.reason != "BEFORE_LINK" }
        if (days.size <= MAX_DAILY_BARS) {
            return Bars(false, days.mapIndexed { i, d ->
                Bar(d.date, shortDate(d.date), showLabel(i, days.size), dayWithWeekday(d.date), if (d.missing) null else d.value, d.inProgress)
            })
        }
        val weeks = days.chunked(7)
        return Bars(true, weeks.mapIndexed { i, week ->
            val values = week.filter { !it.missing && !it.inProgress }.mapNotNull { it.value }
            val first = week.first().date
            Bar(
                key = first,
                // 막대 하나가 한 주라는 것이 보이게 기간으로 적는다. 주가 많으면 자리가 없어 첫날만 둔다(웹 418)
                axisLabel = if (weeks.size <= MAX_WEEK_RANGE_LABELS) "${shortDate(first)} ~ ${shortDate(week.last().date)}" else shortDate(first),
                showLabel = showLabel(i, weeks.size),
                title = "${shortDate(first)} ~ ${shortDate(week.last().date)}",
                value = if (metric in BODY_METRICS) values.lastOrNull() else values.takeIf { it.isNotEmpty() }?.average(),
                inProgress = false,
            )
        })
    }

    /** 눈금 단위. 수면·운동은 분 단위 값을 시간 눈금으로 나눈다 */
    private val TICK_STEP = mapOf(
        "sleeps" to 60.0, "exercises" to 30.0, "steps" to 2000.0, "heart_rates" to 20.0, "oxygen_saturations" to 5.0,
        "weights" to 10.0, "body_fats" to 5.0, "basal_metabolic_rates" to 500.0, "heights" to 0.5,
    )
    /** 100% 를 넘을 수 없는 지표는 축도 100 에서 멈춘다 */
    private val AXIS_CEILING = mapOf("oxygen_saturations" to 100.0, "body_fats" to 100.0)
    private const val MAX_TICK_INTERVALS = 4

    /** [min]은 축의 맨 아래 값. 대부분 0이고 산소포화도만 90(더 낮은 값이 있으면 그 아래)에서 시작한다 */
    data class Scale(val max: Double, val ticks: List<Double>, val min: Double = 0.0) {
        /** 값이 축에서 차지하는 높이(0~1) */
        fun fraction(value: Double): Float = ((value - min) / (max - min)).toFloat().coerceIn(0f, 1f)
    }

    /** 막대 대신 꺾은선과 점으로 그리는 지표. 산소포화도는 값이 좁은 범위(90~100)에 모여 있어 막대로는 차이가 보이지 않는다 */
    fun isLineChart(metric: String): Boolean = metric == "oxygen_saturations"

    /**
     * 일별 차트가 한 화면에 보여 주는 칸 수(일주일). 웹과 같은 공통 규칙이다(웹 S15P31A403-418, 앱 425):
     * 처음에는 최근 일주일만 보이고, 기간이 일주일을 넘으면 가로로 밀어 본다. 처음에는 꺾은선 차트만 그랬다
     */
    const val VISIBLE_DAYS = 7

    /** 일주일씩 밀어 볼 수 있는 차트인지. 하루 단위이고 일주일을 넘을 때만이다(주 단위로 묶은 막대는 밀지 않는다) */
    fun scrollsByWeek(bars: Bars): Boolean = !bars.weekly && bars.bars.size > VISIBLE_DAYS

    /** 기간 전체 보기 버튼의 이름. 한 달쯤이면 "한 달", 그보다 짧으면 날 수로 적는다 */
    fun wholeViewLabel(days: Int): String = if (days >= 28) "한 달" else "${days}일"

    /**
     * 처음 화면의 맨 왼쪽 칸. 가장 최근 일주일이 기본이고, 최근 며칠에 기록이 없으면 값이 있는 마지막 날이 오른쪽 끝에 오게 한다(웹 420).
     * 일주일쯤 동기화가 안 된 채 한 달을 물으면 처음 화면이 빈 칸 일곱 개뿐이라 평균은 있는데 차트는 빈 것처럼 보였다
     */
    fun firstVisible(bars: List<Bar>): Int {
        val lastStart = max(0, bars.size - VISIBLE_DAYS)
        val latest = bars.indexOfLast { it.value != null && it.value != 0.0 }
        return if (latest < 0) lastStart else min(lastStart, max(0, latest - (VISIBLE_DAYS - 1)))
    }

    /**
     * 꼬리표를 올려 둘 높이의 값(S15P31A403-428). 꼬리표가 칸보다 넓어 양옆 [reach]칸까지 덮으면,
     * 덮는 칸들 가운데 가장 높은 막대 위에 두어야 어떤 막대와도 겹치지 않는다. 기간 전체 보기처럼 칸이 좁을 때다
     */
    fun extremeLift(bars: List<Bar>, index: Int, reach: Int): Double? =
        (index - reach..index + reach).mapNotNull { bars.getOrNull(it)?.value }.maxOrNull()

    /** 꼬리표를 달 칸. [index]는 막대 순서, [highest]면 최고·아니면 최저 */
    data class Extreme(val index: Int, val highest: Boolean, val value: Double)

    /**
     * 최고·최저 꼬리표를 달 칸(웹 404·418·439). 일별은 달력의 체크와 같은 날([calendarExtremes]), 주별은 막대에 이미 그린 값에서 고른다.
     * 수면·걸음·심박·운동 네 탭 모두 단다. 처음에는 심박을 뺐는데 탭마다 차트가 달라 보인다는 의견으로 넷을 맞췄다(웹 439, 앱 440).
     * 심박의 꼬리표는 하루 평균이 가장 높은 날·낮은 날이다. 꺾은선(산소포화도)은 달지 않는다.
     * - 운동은 0분인 날을 빼고 운동한 날 가운데서 고른다
     * - 견줄 날이 없으면(기록이 하루뿐이거나 모든 날이 같은 값) 달지 않는다. 최저가 0이면 최고만 단다
     * - 나눠 조회한 기록의 일별 차트에는 달지 않는다
     */
    fun extremes(metric: String, bars: Bars, s: MetricSnapshot, merged: Boolean): List<Extreme> {
        if (isLineChart(metric)) return emptyList()
        val highest: Pair<String, Double>
        val lowest: Pair<String, Double>?
        if (bars.weekly) {
            val valued = bars.bars.mapNotNull { bar -> bar.value?.let { bar.key to it } }
            if (valued.size < 3) return emptyList()
            highest = valued.maxByOrNull { it.second } ?: return emptyList()
            lowest = valued.minByOrNull { it.second } ?: return emptyList()
            if (highest.second == lowest.second) return emptyList()
        } else {
            if (merged) return emptyList()
            val (max, min) = calendarExtremes(metric, s)
            if (max == null) return emptyList()
            if (metric != "exercises") {
                val low = s.dailyMin
                if (low == null || low.value == max.value) return emptyList()
            }
            highest = max.date to max.value
            lowest = min?.takeIf { it.value != max.value }?.let { it.date to it.value }
        }
        val highIndex = bars.bars.indexOfFirst { it.key == highest.first }
        if (highIndex < 0) return emptyList()
        val lowIndex = lowest?.let { low -> bars.bars.indexOfFirst { it.key == low.first } } ?: -1
        if (bars.weekly && highIndex == lowIndex) return emptyList()
        return buildList {
            add(Extreme(highIndex, true, highest.second))
            if (lowest != null && lowIndex >= 0 && lowIndex != highIndex && lowest.second != 0.0) add(Extreme(lowIndex, false, lowest.second))
        }
    }

    private const val SPO2_AXIS_FLOOR = 90.0
    private const val SPO2_AXIS_TOP = 100.0

    /**
     * 산소포화도 축(S15P31A403-384). 90에서 시작해 100까지, 눈금은 5 간격(90·95·100)이다.
     * 90보다 낮은 값이 있으면 그 값이 들어가도록 아래로 넓히고, 칸이 4개를 넘으면 간격을 10·15…로 늘린다
     */
    private fun spo2Scale(values: List<Double?>, average: Double?): Scale {
        val smallest = (values.filterNotNull() + listOfNotNull(average)).minOrNull() ?: SPO2_AXIS_FLOOR
        var interval = 5.0
        fun floorFor(step: Double) = SPO2_AXIS_TOP - ceil((SPO2_AXIS_TOP - min(smallest, SPO2_AXIS_FLOOR)) / step) * step
        while ((SPO2_AXIS_TOP - floorFor(interval)) / interval > MAX_TICK_INTERVALS) interval += 5.0
        val bottom = max(0.0, floorFor(interval))
        return Scale(SPO2_AXIS_TOP, List(((SPO2_AXIS_TOP - bottom) / interval).roundToInt() + 1) { bottom + it * interval }, bottom)
    }

    /**
     * 값과 평균이 모두 들어가는 축. 눈금 간격은 지표별 단위의 배수이고 칸은 최대 4개다.
     * 가장 큰 값 위에 여유를 따로 두지 않는다(웹 404). 예전에는 10%를 더해 올림해서 8시간 36분이 12h 축이 되고 막대가 상자의 절반만 썼다
     */
    fun chartScale(metric: String, values: List<Double?>, average: Double?): Scale {
        if (isLineChart(metric)) return spo2Scale(values, average)
        val step = TICK_STEP[metric] ?: 1.0
        val largest = max(max(0.0, average ?: 0.0), values.maxOfOrNull { it ?: 0.0 } ?: 0.0)
        val target = min(largest, AXIS_CEILING[metric] ?: Double.MAX_VALUE)
        var interval = step
        var top = max(step, ceil(target / step) * step)
        while (top / interval > MAX_TICK_INTERVALS) {
            interval += step
            top = ceil(target / interval) * interval
        }
        return Scale(top, List((top / interval).roundToInt() + 1) { it * interval })
    }

    fun axisValue(metric: String, value: Double): String = when (metric) {
        "sleeps" -> "${(value / 60).roundToInt()}h"
        "exercises" -> "${value.roundToInt()}분"
        "steps" -> if (value == 0.0) "0" else "${String.format(Locale.US, "%.1f", value / 1000).removeSuffix(".0")}k"
        "heights" -> "${(value * 100).roundToInt()}"
        else -> "${value.roundToInt()}"
    }

    enum class Tone { HIGH, NORMAL, PLAIN }

    /**
     * 막대 색 기준: 평균 이상은 진하게, 평균 미만은 옅게. 두 가지뿐이다(웹 404, 앱 440).
     * 기간 최저값을 따로 칠하던 세 번째 색은 없앴다. 가장 높은 날과 낮은 날은 막대 위 꼬리표로 알린다.
     * 수집 중인 오늘도 같은 규칙으로 칠하고 막대 위에 "오늘"이라고만 적는다. 예전에는 점선 테두리로 따로 그렸는데 기록 없는 날의 점선 칸과 헷갈렸다.
     * 평균선이 없는 차트(기록이 적거나 나눠 조회한 기간)는 한 가지 색([Tone.PLAIN])으로 둔다
     */
    fun barTone(value: Double, average: Double?): Tone = when {
        average == null -> Tone.PLAIN
        value >= average -> Tone.HIGH
        else -> Tone.NORMAL
    }

    /** 긴 기간도 하루 한 칸으로 둔 막대. 날짜별 수면 단계 차트는 주로 묶지 않고 밤마다 그린다 */
    fun dayBars(s: MetricSnapshot): List<Bar> {
        val days = s.days.filter { it.reason != "BEFORE_LINK" }
        return days.mapIndexed { i, d -> Bar(d.date, shortDate(d.date), showLabel(i, days.size), dayWithWeekday(d.date), if (d.missing) null else d.value, d.inProgress) }
    }

    // ── 날짜별 기록 달력(웹 430·435·438·439, 앱 440). 웹 evidence-view.ts 의 calendar* · donut* 과 같은 규칙이다 ──

    /** 달력으로 보여 주는 지표. 산소포화도·신체 지표는 값이 좁은 범위에 모여 있어 평균과 견준 원이 뜻이 없다 */
    val CALENDAR_METRICS = setOf("steps", "exercises", "sleeps", "heart_rates")

    /** 원 한 바퀴의 기준이 어디서 왔는지. 안내 팝업의 문구가 달라진다 */
    enum class CalendarBasis { SUMMARY, MERGED, ACTIVE_DAYS }

    data class CalendarBase(val value: Double, val basis: CalendarBasis)

    /** 달력이 세는 날: 오늘(수집 중)·연동 전·값 없는 날을 뺀 날의 값. 백엔드 평균과 같은 제외 규칙이다 */
    private fun countedValues(s: MetricSnapshot): List<DatedValue> =
        s.days.filter { !it.inProgress && it.reason != "BEFORE_LINK" && !it.missing && it.value != null }.map { DatedValue(it.date, it.value!!) }

    /**
     * 원 한 바퀴의 기준.
     * - 한 번에 조회한 기간은 AI 가 받은 기간 평균을 그대로 쓴다.
     * - 나눠 조회해 합친 긴 기간(6개월 등)은 전체 평균이 실려 오지 않아, 백엔드 평균과 같은 제외 규칙으로 하루 값을 평균한다.
     * - 운동은 운동한 날(0분보다 긴 날)만 평균한다. 쉬는 날을 넣으면 운동한 날이 모두 평균을 크게 넘어 차이가 보이지 않는다.
     * 기준이 없거나 0이면 달력을 두지 않는다(null)
     */
    fun calendarAverage(metric: String, s: MetricSnapshot): CalendarBase? {
        if (metric !in CALENDAR_METRICS) return null
        if (metric == "exercises") {
            val active = countedValues(s).filter { it.value > 0 }.map { it.value }
            return if (active.isEmpty()) null else CalendarBase(active.average(), CalendarBasis.ACTIVE_DAYS)
        }
        s.avg?.let { return if (it > 0) CalendarBase(it, CalendarBasis.SUMMARY) else null }
        val values = countedValues(s).map { it.value }
        val computed = if (values.isEmpty()) return null else values.average()
        return if (computed > 0) CalendarBase(computed, CalendarBasis.MERGED) else null
    }

    /**
     * 달력에 체크로 표시할 기간 최고·최저 날. 차트 꼬리표도 같은 날을 가리킨다([extremes]).
     * - 기본은 AI 가 받은 가장 높은 날·낮은 날. 최저가 0이면 여러 날 가운데 하나만 가리키게 되어 두지 않는다.
     * - 운동은 운동한 날 가운데 가장 긴 날·짧은 날이다(쉰 날 0분은 최저로 치지 않는다). 같으면 이른 날.
     * 최고와 최저가 같은 날이면 최고 하나만 둔다
     */
    fun calendarExtremes(metric: String, s: MetricSnapshot): Pair<DatedValue?, DatedValue?> {
        if (metric == "exercises") {
            val active = countedValues(s).filter { it.value > 0 }
            val max = active.maxByOrNull { it.value }
            val min = active.minByOrNull { it.value }
            return max to min?.takeIf { it.date != max?.date }
        }
        val max = s.dailyMax
        val min = s.dailyMin
        return max to min?.takeIf { it.value != 0.0 && it.date != max?.date }
    }

    /**
     * 달력에 올리는 날. 앱 연동 전 날짜는 기록이 생길 수 없어 뺀다(웹 439).
     * 6개월 기간이면 "연동 전"만 나오는 빈 달을 여러 번 넘겨야 했다. 연동한 달의 연동 전 날짜는 기간 밖 날과 같이 흐린 숫자로만 남는다
     */
    fun calendarDays(s: MetricSnapshot): List<EvidenceDay> = s.days.filter { it.reason != "BEFORE_LINK" }

    /**
     * 달력을 쓰는지. 걸음·운동·수면·심박은 기간 길이와 관계없이 이틀 이상이면 달력이다(웹 439).
     * 서지 않는 경우: 하루 기간, 기록 없음, 오늘(수집 중) 값만 있는 기간, 운동이 모두 0분인 기간, 산소포화도·신체 지표
     */
    fun showsCalendar(metric: String, s: MetricSnapshot, cover: Coverage): Boolean =
        calendarAverage(metric, s) != null && cover.hasAnyValue && cover.periodDays > 1

    /** 기간이 걸친 달(`2026-10`)을 앞에서부터 */
    fun calendarMonths(days: List<EvidenceDay>): List<String> = days.map { it.date.take(7) }.distinct().sorted()

    /**
     * 달력을 처음 열 때 고를 날과 보여 줄 달. 마지막으로 값이 있는 날(오늘 수집 중 포함), 값이 하나도 없으면 기간의 마지막 날이다.
     * 달은 그 날이 있는 달로 둔다
     */
    fun calendarStart(days: List<EvidenceDay>): EvidenceDay? {
        val sorted = days.sortedBy { it.date }
        return sorted.lastOrNull { !it.missing && it.value != null } ?: sorted.lastOrNull()
    }

    /**
     * @property weekday 0 일요일 ~ 6 토요일
     * @property inMonth 보고 있는 달의 날인지. 앞뒤 달의 날은 흐리게 그린다
     * @property entry 기간 안의 날이면 그날 기록. 같은 달이라도 기간 밖이면 null 이다
     */
    data class CalendarCell(val date: String, val day: Int, val weekday: Int, val inMonth: Boolean, val entry: EvidenceDay?)

    /** 달력은 기본 5주다. 늘 6주로 두면 대부분의 달에서 빈 줄이 하나 남는다(웹 438) */
    const val CALENDAR_MIN_WEEKS = 5

    /** 그 달에 필요한 주 수(4~6). 1일 앞 빈 칸과 날 수로 정해진다 */
    fun weeksInMonth(month: String): Int {
        val first = LocalDate.parse("$month-01")
        return ceil((first.dayOfWeek.value % 7 + first.lengthOfMonth()) / 7.0).toInt()
    }

    /** 한 달의 달력 칸(일요일 시작). 5주가 기본이고 6주가 필요한 달만 한 줄 늘린다. 같은 주 수끼리 높이가 같다 */
    fun calendarWeeks(month: String, days: List<EvidenceDay>): List<List<CalendarCell>> {
        val byDate = days.associateBy { it.date }
        val first = LocalDate.parse("$month-01")
        val start = first.minusDays((first.dayOfWeek.value % 7).toLong())
        return List(max(CALENDAR_MIN_WEEKS, weeksInMonth(month))) { week ->
            List(7) { weekday ->
                val date = start.plusDays((week * 7 + weekday).toLong())
                val text = date.toString()
                val inMonth = text.startsWith(month)
                CalendarCell(text, date.dayOfMonth, weekday, inMonth, if (inMonth) byDate[text] else null)
            }
        }
    }

    /** `2026-10` → `10월`. 해가 바뀌는 기간만 연도를 붙인다 */
    fun monthTitle(month: String, months: List<String>): String {
        val (year, number) = month.split("-").map { it.toInt() }
        return if (months.first().take(4) == months.last().take(4)) "${number}월" else "${year}년 ${number}월"
    }

    private val WEEKDAY_NAMES = listOf("일", "월", "화", "수", "목", "금", "토")

    /** 0 일요일 ~ 6 토요일 */
    fun weekdayIndex(date: String): Int = LocalDate.parse(date).dayOfWeek.value % 7

    fun weekdayName(date: String): String = WEEKDAY_NAMES[weekdayIndex(date)]

    /**
     * 칸·상세에 쓸 그날 값. 값이 없으면 null(점선 원, "기록 없음").
     * 운동 0분인 날은 기록 없는 날과 구분하지 않는다. 오늘(수집 중)은 아직 모이는 중이라 0분이어도 값으로 둔다
     */
    fun shownValue(metric: String, day: EvidenceDay): Double? {
        if (day.missing || day.value == null) return null
        return if (metric == "exercises" && day.value == 0.0 && !day.inProgress) null else day.value
    }

    /**
     * 원이 얼마나 찰지. first 는 첫 바퀴(0에서 평균까지), second 는 두 바퀴째(평균에서 기간 최고까지)다.
     * 평균을 넘은 날이 모두 꽉 찬 같은 원으로 보이지 않게 두 바퀴째를 진한 색으로 그린다. 가장 높은 날만 두 바퀴를 다 채운다
     */
    fun donutFill(value: Double, average: Double, max: Double?): Pair<Float, Float> {
        val first = (value / average).coerceIn(0.0, 1.0).toFloat()
        if (value <= average || max == null || max <= average) return first to 0f
        return first to ((value - average) / (max - average)).coerceAtMost(1.0).toFloat()
    }

    private val COMPARE_WORDS = mapOf(
        "steps" to ("많아요" to "적어요"), "exercises" to ("길어요" to "짧아요"),
        "sleeps" to ("길어요" to "짧아요"), "heart_rates" to ("높아요" to "낮아요"),
    )

    /**
     * 달력에서 고른 날의 "평균보다 ○ 많아요". 화면에 보이는 자리로 반올림한 두 값의 차이로 낸다(걸음·분은 정수, 심박은 소수 첫째 자리).
     * 반올림 전 값으로 빼면 그날 값과 평균이 같은 "7시간 2분"으로 보이는데 "1분 길어요"라고 나올 수 있다
     */
    fun compareToAverage(metric: String, value: Double, average: Double): String {
        val step = if (metric == "heart_rates") 0.1 else 1.0
        val gap = (value / step).roundToLong() - (average / step).roundToLong()
        if (gap == 0L) return "평균과 같아요"
        val (more, less) = COMPARE_WORDS[metric] ?: ("높아요" to "낮아요")
        return "평균보다 ${value(metric, abs(gap) * step)} ${if (gap > 0) more else less}"
    }

    /** 달력 한 칸을 보조기기에 읽어 주는 글: "10월 27일 화요일, 7시간 2분, 가장 길게 잔 날" */
    fun cellLabel(metric: String, day: EvidenceDay, mark: String?): String {
        val date = LocalDate.parse(day.date)
        val shown = shownValue(metric, day)
        val text = when {
            shown == null -> MISSING_REASON_LABELS[day.reason] ?: "기록 없음"
            day.inProgress -> "${value(metric, shown)}, 수집 중"
            else -> value(metric, shown)
        }
        return "${date.monthValue}월 ${date.dayOfMonth}일 ${weekdayName(day.date)}요일, $text" + (mark?.let { ", $it" } ?: "")
    }

    /** 그날 실제 측정 최저·최고 심박. 같은 날이 여러 번 실려 오면 뒤의 것을 쓴다. 둘 다 없으면 null */
    fun dailyRange(metric: String, s: MetricSnapshot, date: String): DailyRange? {
        if (metric != "heart_rates") return null
        return s.dailyRanges.lastOrNull { it.date == date }?.takeIf { it.min != null || it.max != null }
    }

    /** 그 밤에 90% 미만이 있었는지. 달력 날짜 옆 빨간 점에 쓴다 */
    fun hasLowSpo2(state: Spo2Low): Boolean = state is Spo2Low.Times || (state is Spo2Low.CountOnly && state.count > 0)

    /** 가장 높은 날·낮은 날을 부르는 말(지표에 맞는 말) */
    fun highlightLabels(metric: String): Pair<String, String> = HIGHLIGHT_LABELS[metric] ?: ("가장 높은 날" to "가장 낮은 날")

    /** 원의 기준을 부르는 말. 운동은 쉰 날을 뺀 운동한 날 평균이다 */
    fun baseName(basis: CalendarBasis): String = if (basis == CalendarBasis.ACTIVE_DAYS) "운동한 날 평균" else "하루 평균"

    /** 물은 기간이 통째로 앱 연동 전인지. "8월 10일 걸음 수"처럼 연동 전 하루만 물은 경우다(웹 439) */
    fun allBeforeLink(s: MetricSnapshot): Boolean = s.days.isNotEmpty() && s.days.all { it.reason == "BEFORE_LINK" }

    // ── 시간대별 걸음(웹 438·439, 앱 440). 활동 달력에서 고른 날의 0~23시 걸음 ──

    /** 그날이 든 달 가운데 근거 기간 안쪽. 하루씩이 아니라 달 단위로 불러 날짜를 바꿔도 다시 부르지 않는다(조회 한도 31일) */
    fun hourlyRange(date: String, periodStart: String, periodEnd: String): Pair<String, String> {
        val first = date.take(7) + "-01"
        val last = LocalDate.parse(first).let { it.withDayOfMonth(it.lengthOfMonth()).toString() }
        return (if (first < periodStart) periodStart else first) to (if (last > periodEnd) periodEnd else last)
    }

    fun datesBetween(start: String, end: String): List<String> {
        val last = LocalDate.parse(end)
        return generateSequence(LocalDate.parse(start)) { it.plusDays(1) }.takeWhile { !it.isAfter(last) }.map { it.toString() }.toList()
    }

    /** 가장 많이 걸은 시간. 하루가 모두 0걸음이면 null(기록 없음과 같이 둔다) */
    fun hourlyPeak(hours: List<Double>?): Pair<Int, Double>? {
        val top = hours?.maxOrNull() ?: return null
        return if (top > 0) hours.indexOf(top) to top else null
    }

    // ── 날짜별 수면 단계 차트(웹 420, 앱 440). 밤마다 깊은 잠·렘수면·얕은 잠을 쌓아 그린다 ──

    enum class Stage(val label: String) { LIGHT("얕은 잠"), REM("렘수면"), DEEP("깊은 잠") }

    data class StagePart(val stage: Stage, val minutes: Double)

    /** [parts]는 위에서 아래로(얕은 잠 → 깊은 잠) 놓는 순서다. [total]은 세 단계의 합(깬 시간은 넣지 않는다) */
    data class StageColumn(val bar: Bar, val parts: List<StagePart>, val total: Double)

    fun stageColumns(bars: List<Bar>, nights: List<SleepNight>): List<StageColumn> {
        val byDate = nights.associateBy { it.date }
        return bars.map { bar ->
            val night = byDate[bar.key]
            val parts = if (night == null) emptyList() else listOfNotNull(
                night.lightMinutes?.let { StagePart(Stage.LIGHT, it) },
                night.remMinutes?.let { StagePart(Stage.REM, it) },
                night.deepMinutes?.let { StagePart(Stage.DEEP, it) },
            )
            StageColumn(bar, parts, parts.sumOf { it.minutes })
        }
    }

    /** 보조기기와 눌렀을 때 쓰는 글. 깊은 잠부터 적는다 */
    fun stageText(column: StageColumn): String =
        if (column.total > 0) column.parts.reversed().joinToString(", ") { "${it.stage.label} ${value("sleeps", it.minutes)}" } else "단계 기록 없음"

    // ── 날짜별 목록 ───────────────────────────────────────────────────────────

    sealed interface DayItem {
        data class Day(val date: String, val value: Double, val inProgress: Boolean) : DayItem
        data class Gap(val start: String, val end: String, val count: Int, val reason: String?) : DayItem
    }

    /** 날짜별 목록. 이어지는 기록 없는 날은 이유가 같으면 한 줄로 묶는다 */
    fun dayItems(days: List<EvidenceDay>): List<DayItem> {
        val items = mutableListOf<DayItem>()
        for (day in days) {
            if (!day.missing && day.value != null) {
                items += DayItem.Day(day.date, day.value, day.inProgress)
                continue
            }
            val previous = items.lastOrNull()
            if (previous is DayItem.Gap && previous.reason == day.reason) {
                items[items.lastIndex] = previous.copy(end = day.date, count = previous.count + 1)
            } else {
                items += DayItem.Gap(day.date, day.date, 1, day.reason)
            }
        }
        return items
    }

    data class Highlight(val label: String, val date: String, val value: Double)

    /**
     * 가장 높은 날·낮은 날을 부르는 말(S15P31A403-427). 잠과 걸음을 "높다·낮다"로 부르면 어색해 지표에 맞는 말을 쓴다.
     * 심박·산소포화도·신체 지표는 수치의 높낮이가 그대로 뜻이라 "가장 높은 날·낮은 날"로 둔다
     */
    private val HIGHLIGHT_LABELS = mapOf(
        "sleeps" to ("가장 길게 잔 날" to "가장 짧게 잔 날"),
        "steps" to ("가장 많이 걸은 날" to "가장 적게 걸은 날"),
        "exercises" to ("가장 오래 운동한 날" to "가장 짧게 운동한 날"),
    )

    /** 긴 기간에서 먼저 보여 줄 날: 가장 높은 날·낮은 날·오늘(수집 중) */
    fun dayHighlights(metric: String, s: MetricSnapshot): List<Highlight> = buildList {
        val (highest, lowest) = HIGHLIGHT_LABELS[metric] ?: ("가장 높은 날" to "가장 낮은 날")
        s.dailyMax?.let { add(Highlight(highest, it.date, it.value)) }
        s.dailyMin?.takeIf { it.date != s.dailyMax?.date }?.let { add(Highlight(lowest, it.date, it.value)) }
        s.days.firstOrNull { it.inProgress && !it.missing && it.value != null }
            ?.let { add(Highlight("오늘(수집 중)", it.date, it.value!!)) }
    }

    fun listsAllDays(s: MetricSnapshot): Boolean = s.days.size <= MAX_LISTED_DAYS

    // ── 지표별 추가 정보 ──────────────────────────────────────────────────────

    /** 심박·산소포화도의 실제 측정 최저·최고. 하루 평균의 최저·최고와 다르다 */
    fun measuredRange(metric: String, s: MetricSnapshot): Pair<DatedValue?, DatedValue?>? {
        if (metric != "heart_rates" && metric != "oxygen_saturations") return null
        return if (s.measuredMin != null || s.measuredMax != null) s.measuredMin to s.measuredMax else null
    }

    data class StageAverage(val label: String, val minutes: Double, val nights: Int)

    /** 수면 단계별 밤 평균(분). 단계 값이 있는 밤만 평균하고, 어느 밤에도 없는 단계는 뺀다 */
    fun sleepStageAverages(s: MetricSnapshot): List<StageAverage> =
        listOf<Pair<String, (SleepNight) -> Double?>>(
            "깊은 잠" to { it.deepMinutes }, "렘수면" to { it.remMinutes }, "얕은 잠" to { it.lightMinutes }, "깬 시간" to { it.awakeMinutes },
        ).mapNotNull { (label, pick) ->
            val values = s.nights.mapNotNull(pick)
            if (values.isEmpty()) null else StageAverage(label, values.average().roundToInt().toDouble(), values.size)
        }

    data class SessionItem(val session: ExerciseSession, val isLongest: Boolean)

    /**
     * 운동 기록. 최근 것부터, 인정된 운동 중 가장 긴 것 하나에 표시를 붙인다.
     * 견줄 기록이 있어야 "가장 길게"가 뜻이 있어, 인정된 운동이 한 건뿐이면 표시하지 않는다(웹 439)
     */
    fun sessionItems(s: MetricSnapshot): List<SessionItem> {
        val sessions = s.sessions.sortedByDescending { it.startAt }
        val counted = sessions.filter { it.countsAsExercise }
        var longest: ExerciseSession? = null
        if (counted.size > 1) {
            for (session in counted) if (longest == null || session.durationMinutes > longest.durationMinutes) longest = session
        }
        return sessions.map { SessionItem(it, it === longest) }
    }

    /**
     * 수면 중 산소포화도 90% 미만 상태(백엔드 합의 네 가지).
     * 횟수 null·시각 null → 측정 없음 / 횟수만 있음 → 시각 정보 없음 / 0·빈 목록 → 없음 / N·시각 → 시각 목록
     */
    sealed interface Spo2Low {
        data object Unmeasured : Spo2Low
        data class CountOnly(val count: Int) : Spo2Low
        data object None : Spo2Low
        data class Times(val times: List<String>) : Spo2Low
    }

    fun spo2Low(count: Int?, times: List<String>?): Spo2Low = when {
        // Z 와 +09:00 이 섞여 와도 실제 시각 순으로 둔다(글자 순 정렬은 틀린다)
        !times.isNullOrEmpty() -> Spo2Low.Times(times.sortedBy { runCatching { OffsetDateTime.parse(it).toInstant() }.getOrNull() })
        times != null || count == 0 -> Spo2Low.None
        count != null -> Spo2Low.CountOnly(count)
        else -> Spo2Low.Unmeasured
    }

    /** 산소포화도 한 줄 글자. expanded 가 false 면 앞의 몇 개만 쓴다 */
    fun spo2Text(state: Spo2Low, expanded: Boolean): String {
        val label = "산소포화도 90% 미만"
        return when (state) {
            Spo2Low.Unmeasured -> "$label · 측정 없음"
            Spo2Low.None -> "$label · 없음"
            is Spo2Low.CountOnly -> "$label · ${state.count}회 (시각 정보 없음)"
            is Spo2Low.Times -> {
                val shown = if (expanded) state.times else state.times.take(SPO2_TIMES_PREVIEW)
                "$label ${state.times.size}회 · ${shown.joinToString(", ") { clock(it) }}"
            }
        }
    }

    /**
     * "90% 미만" 칸에 쓰는 값(달력 상세·날짜별 표). 이름은 머리글이나 줄 이름에 따로 있어 상태만 적는다.
     * [expanded]가 false 면 시각을 앞의 몇 개만 쓴다(화면은 그 뒤에 "외 N회 보기"를 붙인다)
     */
    fun spo2Bare(state: Spo2Low, expanded: Boolean = true): String = when (state) {
        Spo2Low.Unmeasured -> "측정 없음"
        Spo2Low.None -> "없음"
        is Spo2Low.CountOnly -> "${state.count}회 (시각 정보 없음)"
        is Spo2Low.Times -> "${state.times.size}회 · ${(if (expanded) state.times else state.times.take(SPO2_TIMES_PREVIEW)).joinToString(", ") { clock(it) }}"
    }

    /** [average]는 그 밤 수면 중 평균 산소포화도. 90% 미만 여부만으로는 평소 수준을 알 수 없어 함께 보여 준다(S15P31A403-384) */
    /** [date]는 기록 날짜(깬 날). 화면의 다른 날짜와 같은 기준으로 쓴다(웹 420) */
    data class NightSpo2(val date: String, val state: Spo2Low, val average: Double? = null)

    /** 공통 수면 근거의 밤 목록에 산소포화도 칸이 있으면(새 근거) 밤별로 보여 준다 */
    fun nightSpo2(s: MetricSnapshot): List<NightSpo2> =
        s.nights.filter { it.hasSpo2 }.sortedBy { it.startAt }.map { NightSpo2(it.date, spo2Low(it.spo2LowCount, it.spo2LowTimes), it.spo2Avg) }

    /** 밤별 산소포화도를 접지 않고 다 보여 주는 최대 밤 수. 한 주는 그대로, 더 길면 요약을 먼저 보여 준다 */
    const val MAX_LISTED_SPO2_NIGHTS = 7

    /** 요약의 한 칸. [date]는 그 값이 측정된 수면의 기록 날짜(깬 날) */
    data class Spo2Point(val value: Double, val date: String)

    /**
     * 긴 기간의 수면 중 산소포화도 요약(S15P31A403-384). 한 달처럼 밤이 많으면 목록이 화면을 다 차지해,
     * 평균·최저·최고를 먼저 보여 주고 밤별 목록은 접어 둔다.
     * - [average]: 밤별 평균을 다시 평균한 값이다. 근거에 기간 평균이 따로 실려 오지 않아 앱에서 낸다(AI 가 받은 값이 아니다)
     * - [lowest]·[highest]: 밤마다 실려 온 최저·최고 중에서 고른 값(새로 계산하지 않는다)
     * - [nights]: 산소포화도 칸이 있는 밤 수, [lowNights]: 90% 미만이 한 번이라도 있었던 밤 수
     * 밤이 [MAX_LISTED_SPO2_NIGHTS] 이하면 null(목록을 그대로 보여 준다)
     */
    data class Spo2Summary(val average: Double?, val lowest: Spo2Point?, val highest: Spo2Point?, val nights: Int, val lowNights: Int)

    fun spo2Summary(s: MetricSnapshot): Spo2Summary? {
        val nights = s.nights.filter { it.hasSpo2 }
        if (nights.size <= MAX_LISTED_SPO2_NIGHTS) return null
        val averages = nights.mapNotNull { it.spo2Avg }
        return Spo2Summary(
            average = if (averages.isEmpty()) null else averages.average(),
            lowest = nights.mapNotNull { n -> n.spo2Min?.let { Spo2Point(it, n.date) } }.minByOrNull { it.value },
            highest = nights.mapNotNull { n -> n.spo2Max?.let { Spo2Point(it, n.date) } }.maxByOrNull { it.value },
            nights = nights.size,
            lowNights = nights.count { (it.spo2LowCount ?: 0) > 0 },
        )
    }

    /**
     * 요약 아래 안내. 목록을 접으면 90% 미만이 있었는지가 가려지므로 그 밤 수만 알린다.
     * 며칠의 기록인지는 위 기간 머리말에 이미 있어 다시 쓰지 않는다
     */
    fun spo2SummaryNote(summary: Spo2Summary): String =
        if (summary.lowNights > 0) "90% 미만으로 측정된 날은 ${summary.lowNights}일이에요." else "90% 미만으로 측정된 날은 없어요."

    /**
     * 수면 단계 평균 아래 안내. 며칠 중 며칠의 평균인지를 날 수로 말한다(S15P31A403-384, "밤 3개 평균"은 어색하다는 의견).
     * [periodDays] 조회 기간, [counted] 단계 기록이 있는 밤, [skipped] 수면 기록은 있는데 단계 기록이 없는 밤
     */
    fun stageNote(periodDays: Int, counted: Int, skipped: Int): String {
        val base = when {
            periodDays <= 1 -> "이 날 하루의 수면 단계예요."
            counted >= periodDays -> "${periodDays}일 평균이에요."
            else -> "${periodDays}일 중 ${counted}일의 평균이에요."
        }
        return base + if (skipped > 0) " 수면 단계 기록이 없는 ${skipped}일은 뺐어요." else ""
    }

    /** 측정 최저·최고 아래 안내. 하루 평균의 최저·최고가 아니라 기간 전체에서 실제로 잰 값이라는 뜻을 쉬운 말로 쓴다 */
    fun measuredNote(periodDays: Int): String =
        if (periodDays <= 1) "이 날 측정된 값 중 가장 낮은 값과 가장 높은 값이에요."
        else "${periodDays}일 동안 측정된 값 중 가장 낮은 값과 가장 높은 값이에요."

    data class SleepSessionsSummary(val sessions: List<SleepSession>, val mainCount: Int, val extraCount: Int, val mainAverage: Double?)

    /** 수면 세션 목록. 시작 순서대로, 주 수면 평균은 주 수면만으로 낸다 */
    fun sleepSessions(e: SleepSessionsEvidence): SleepSessionsSummary {
        val sessions = e.sessions.sortedBy { it.startAt }
        val mains = sessions.filter { it.isMain }.mapNotNull { it.sleepMinutes }
        return SleepSessionsSummary(sessions, sessions.count { it.isMain }, sessions.count { !it.isMain }, mains.takeIf { it.isNotEmpty() }?.average())
    }

    /** 시간대 걸음 표의 열(6시, 7시, …). endHour 는 끝 시각이라 열에 넣지 않는다 */
    fun hourlyColumns(e: HourlyStepsEvidence): List<Int> = List(max(0, e.endHour - e.startHour)) { e.startHour + it }

    /** 시간대 걸음 한 칸. 값은 천 단위 구분만 붙인다 */
    fun count(value: Double?): String = if (value == null) "-" else grouped(value)
}
