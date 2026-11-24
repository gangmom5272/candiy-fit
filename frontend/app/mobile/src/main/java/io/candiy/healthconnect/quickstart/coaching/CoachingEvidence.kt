package io.candiy.healthconnect.quickstart.coaching

import org.json.JSONArray
import org.json.JSONObject
import java.time.OffsetDateTime

/*
 * AI 답변의 근거(명세 2.3 evidence)를 앱에서 읽는 모델과 JSON 해석(S15P31A403-360).
 * 웹 frontend/web/src/lib/features/coaching 의 types.ts · parse.ts 와 같은 계약이다. 화면에 쓰는 값만 남긴다.
 * 근거 하나의 모양이 다르면 그 근거만 숨기고 나머지 근거와 답변은 그대로 보여 준다(웹과 같은 방침).
 * 값은 로그에 남기지 않는다.
 */

/** 명세의 지표 코드. 순서는 웹 HEALTH_METRICS 와 같다 */
val HEALTH_METRICS = listOf(
    "sleeps", "steps", "heart_rates", "oxygen_saturations", "exercises",
    "weights", "body_fats", "basal_metabolic_rates", "heights",
)

/** 신체 지표. 날짜 값이 그날 마지막 측정값이라 앱에서 합치거나 평균 내지 않는다 */
val BODY_METRICS = setOf("weights", "body_fats", "basal_metabolic_rates", "heights")

private val MISSING_REASONS = setOf("NO_RECORD", "NULL_VALUE", "ZERO_STEPS", "UNSYNCED", "BEFORE_LINK")
private val HOURLY_REASONS = setOf("NO_RECORD", "NULL_VALUE")

data class DatedValue(val date: String, val value: Double)

data class EvidenceDay(
    val date: String,
    val value: Double?,
    val missing: Boolean,
    val reason: String?,
    val inProgress: Boolean,
)

/** 수집 중인 오늘 값(명세 in_progress) */
data class InProgressValue(val date: String, val value: Double?, val missing: Boolean, val asOf: String?)

/** 명세 2.2 detail.nights. 주 수면만, 날짜는 깬 날(KST) */
data class SleepNight(
    val date: String,
    val startAt: String,
    val deepMinutes: Double?,
    val remMinutes: Double?,
    val lightMinutes: Double?,
    val awakeMinutes: Double?,
    /** 산소포화도 칸이 있는 새 근거인지. 예전 근거(false)는 "측정 없음"으로 보이지 않게 표시하지 않는다 */
    val hasSpo2: Boolean,
    val spo2LowCount: Int?,
    val spo2LowTimes: List<String>?,
    /** 그 밤 수면 중 평균 산소포화도(%). 측정이 없거나 예전 근거면 null */
    val spo2Avg: Double? = null,
    /** 그 밤 수면 중 가장 낮게·높게 측정된 산소포화도(%) */
    val spo2Min: Double? = null,
    val spo2Max: Double? = null,
    /** 깬 시각. 달력 상세와 하루 수면 기록의 "깬 시각"에 쓴다(S15P31A403-440). 없거나 형식이 다르면 null */
    val endAt: String? = null,
)

/** 그날 실제로 측정된 최저·최고 심박(명세 detail.daily_ranges). 달력 상세에 쓴다(S15P31A403-440) */
data class DailyRange(val date: String, val min: Double?, val max: Double?)

/** 명세 2.2 detail.sessions 중 화면에 쓰는 값 */
data class ExerciseSession(
    val date: String,
    val startAt: String,
    val durationMinutes: Double,
    val typeName: String,
    val title: String?,
    /** 운동 인정 여부. false 면 목록에는 두고 운동 시간에서는 뺀다 */
    val countsAsExercise: Boolean,
)

/** 운동 활동값 한 항목의 기간 통계. 값이 있는 완료 날짜만 센다(오늘·연동 전 제외) */
data class ActivityStat(val total: Double?, val avg: Double?, val daysWithData: Int)

/** 운동 활동값의 하루. 횟수는 인정 운동만, 칼로리(kcal)·거리(m)는 미인정 운동까지 포함한 운동 기록의 값이다 */
data class ActivityDay(
    val date: String,
    val count: Double?,
    val calories: Double?,
    val distance: Double?,
    val inProgress: Boolean,
    /** 횟수가 없는 이유(운동 시간과 같은 판정) */
    val reason: String?,
    val caloriesReason: String?,
    val distanceReason: String?,
)

/** 항목별 기간 통계. 서버가 낸 값을 그대로 쓴다 */
data class ActivitySummary(val count: ActivityStat, val calories: ActivityStat, val distance: ActivityStat)

/**
 * 운동 근거의 detail.exercise_activity(S15P31A403-375). AI 가 운동 요약·추이에서 읽는 횟수·칼로리·거리다.
 * 운동 시간은 공통 days·summary 와 같은 값이라 읽지 않는다.
 * [summary]는 나눠 조회한 기간을 합친 범위에서는 null 이다(앱에서 다시 더하지 않는다).
 * [failedSources]는 최근 수집에 실패한 원천(exercise_sessions·calories_burned·distances)이다
 */
data class ExerciseActivity(val days: List<ActivityDay>, val summary: ActivitySummary?, val failedSources: List<String>)

/** 명세 2.3 날짜별 지표 공통 원형 중 화면에 쓰는 값 */
data class MetricSnapshot(
    val avg: Double?,
    val dailyMin: DatedValue?,
    val dailyMax: DatedValue?,
    val daysWithData: Int,
    val completedDays: Int,
    val unsyncedDays: Int,
    val days: List<EvidenceDay>,
    val inProgress: InProgressValue?,
    val measuredMin: DatedValue?,
    val measuredMax: DatedValue?,
    val nights: List<SleepNight>,
    val sessions: List<ExerciseSession>,
    /** 운동 지표에만 온다. 예전 근거·다른 지표는 null */
    val activity: ExerciseActivity? = null,
    /** 심박 지표의 날짜별 측정 최저·최고. 없으면 빈 목록 */
    val dailyRanges: List<DailyRange> = emptyList(),
)

/** 낮잠 포함 전체 수면 세션 한 건(kind sleep_sessions) */
data class SleepSession(
    val sessionId: String,
    /** 주 수면이면 true. false 는 낮잠만이 아니라 밤에 다시 잠든 두 번째 수면도 된다 */
    val isMain: Boolean,
    val startAt: String,
    val endAt: String,
    val sleepMinutes: Double?,
    val spo2LowCount: Int?,
    val spo2LowTimes: List<String>?,
)

/** 시간대별 걸음의 하루(kind hourly_steps). values[i] 는 startHour + i 시의 걸음 */
data class HourlyDay(val date: String, val inProgress: Boolean, val values: List<Double>?, val total: Double?)

sealed interface Evidence {
    val id: String
    val position: Int
    val metric: String
    val periodStart: String
    val periodEnd: String
}

data class MetricEvidence(
    override val id: String,
    override val position: Int,
    override val metric: String,
    override val periodStart: String,
    override val periodEnd: String,
    val snapshot: MetricSnapshot,
) : Evidence

data class SleepSessionsEvidence(
    override val id: String,
    override val position: Int,
    override val metric: String,
    override val periodStart: String,
    override val periodEnd: String,
    val sessions: List<SleepSession>,
) : Evidence

data class HourlyStepsEvidence(
    override val id: String,
    override val position: Int,
    override val metric: String,
    override val periodStart: String,
    override val periodEnd: String,
    val startHour: Int,
    val endHour: Int,
    val days: List<HourlyDay>,
    /** 기간 합. 백엔드가 낸 값이며 하루라도 기록이 없으면 null 이다. 앱에서 다시 더하지 않는다 */
    val total: Double?,
) : Evidence

object EvidenceJson {
    private class FormatError(field: String) : Exception(field)

    /** 오프셋이 붙은 시각(RFC 3339, Z·+09:00 모두). 웹 parse.ts 와 같은 형식만 받는다 */
    private val ISO_WITH_OFFSET = Regex("""^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$""")

    /**
     * 근거 목록. 근거 하나가 형식이 다르거나 모르는 종류(snapshot.kind)면 그 근거만 뺀다.
     * 목록이 없으면 빈 목록이다
     */
    fun list(items: JSONArray?): List<Evidence> {
        if (items == null) return emptyList()
        return (0 until items.length()).mapNotNull { i ->
            runCatching { items.optJSONObject(i)?.let(::evidence) }.getOrNull()
        }
    }

    private fun evidence(o: JSONObject): Evidence? {
        val id = o.string("id")
        val position = o.number("position").toInt()
        val metric = o.string("metric").also { if (it !in HEALTH_METRICS) throw FormatError("metric") }
        val start = o.string("periodStart")
        val end = o.string("periodEnd")
        val snapshot = o.obj("snapshot")
        if (!snapshot.has("kind")) return MetricEvidence(id, position, metric, start, end, snapshot(snapshot))
        return when (snapshot.opt("kind")) {
            "sleep_sessions" -> SleepSessionsEvidence(id, position, metric, start, end, snapshot.array("sessions").objects().map(::sleepSession))
            "hourly_steps" -> HourlyStepsEvidence(
                id, position, metric, start, end,
                startHour = snapshot.number("start_hour").toInt(),
                endHour = snapshot.number("end_hour").toInt(),
                days = snapshot.array("days").objects().map(::hourlyDay),
                total = snapshot.numberOrNull("total"),
            )
            // 운동 구간 등 아직 모양이 정해지지 않은 종류는 숨긴다
            else -> null
        }
    }

    private fun snapshot(o: JSONObject): MetricSnapshot {
        val summary = o.obj("summary")
        val detail = o.obj("detail")
        val progress = if (o.isNull("in_progress")) null else o.obj("in_progress")
        return MetricSnapshot(
            avg = summary.numberOrNull("avg"),
            dailyMin = summary.datedOrNull("daily_min"),
            dailyMax = summary.datedOrNull("daily_max"),
            daysWithData = summary.number("days_with_data").toInt(),
            completedDays = summary.number("completed_days").toInt(),
            unsyncedDays = summary.array("unsynced_days").length(),
            days = o.array("days").objects().map { d ->
                EvidenceDay(
                    date = d.string("date"),
                    value = d.numberOrNull("value"),
                    missing = d.bool("missing"),
                    reason = d.stringOrNull("reason")?.also { if (it !in MISSING_REASONS) throw FormatError("reason") },
                    inProgress = d.bool("is_in_progress"),
                )
            },
            inProgress = progress?.let {
                InProgressValue(it.string("date"), it.numberOrNull("value"), it.bool("missing"), it.stringOrNull("as_of"))
            },
            measuredMin = detail.datedOrNull("measured_min"),
            measuredMax = detail.datedOrNull("measured_max"),
            nights = detail.array("nights").objects().map(::night),
            sessions = detail.array("sessions").objects().map { s ->
                ExerciseSession(
                    date = s.string("date"),
                    startAt = s.string("start_at"),
                    durationMinutes = s.number("duration_minutes"),
                    typeName = s.string("type_name"),
                    title = s.stringOrNull("title"),
                    countsAsExercise = s.bool("counts_as_exercise"),
                )
            },
            // 모양이 다르면 이 묶음만 버린다. 덧붙은 값 하나 때문에 운동 시간·세션 근거까지 잃지 않게 한다
            activity = detail.optJSONObject("exercise_activity")?.let { runCatching { activity(it) }.getOrNull() },
            // 덧붙는 값이라 모양이 다른 줄은 그 줄만 버린다
            dailyRanges = detail.optJSONArray("daily_ranges")?.let { a ->
                (0 until a.length()).mapNotNull { i ->
                    val range = a.optJSONObject(i) ?: return@mapNotNull null
                    val date = range.opt("date") as? String ?: return@mapNotNull null
                    fun finite(name: String) = (range.opt(name) as? Number)?.toDouble()?.takeIf { it.isFinite() }
                    DailyRange(date, finite("min"), finite("max"))
                }
            } ?: emptyList(),
        )
    }

    private val ACTIVITY_SOURCES = listOf("exercise_sessions", "calories_burned", "distances")

    private fun activity(o: JSONObject): ExerciseActivity {
        val summary = o.obj("summary")
        fun stat(name: String) = summary.obj(name).let { ActivityStat(it.numberOrNull("total"), it.numberOrNull("avg"), it.number("days_with_data").toInt()) }
        fun JSONObject.reason(name: String) = stringOrNull(name)?.also { if (it !in MISSING_REASONS) throw FormatError(name) }
        val statuses = o.optJSONObject("source_statuses")
        return ExerciseActivity(
            days = o.array("days").objects().map { d ->
                ActivityDay(
                    date = d.string("date"),
                    count = d.numberOrNull("exercise_count"),
                    calories = d.numberOrNull("exercise_calories_kcal"),
                    distance = d.numberOrNull("exercise_distance_m"),
                    inProgress = d.bool("is_in_progress"),
                    reason = d.reason("reason"),
                    caloriesReason = d.reason("calories_reason"),
                    distanceReason = d.reason("distance_reason"),
                )
            },
            summary = ActivitySummary(stat("exercise_count"), stat("exercise_calories_kcal"), stat("exercise_distance_m")),
            failedSources = ACTIVITY_SOURCES.filter { statuses?.optJSONObject(it)?.opt("last_sync_status") == "fail" },
        )
    }

    private fun night(o: JSONObject): SleepNight {
        val hasSpo2 = o.has("spo2_low_count") || o.has("spo2_low_times")
        return SleepNight(
            date = o.string("date"),
            // 산소포화도 칸이 있는 밤은 잠든 시각으로 라벨을 만들므로 시각 형식을 엄격히 본다
            startAt = if (hasSpo2) o.isoTime("start_at") else o.string("start_at"),
            deepMinutes = o.numberOrNull("deep_minutes"),
            remMinutes = o.numberOrNull("rem_minutes"),
            lightMinutes = o.numberOrNull("light_minutes"),
            awakeMinutes = o.numberOrNull("awake_minutes"),
            hasSpo2 = hasSpo2,
            spo2LowCount = if (hasSpo2) o.numberOrNull("spo2_low_count")?.toInt() else null,
            spo2LowTimes = if (hasSpo2) o.isoTimesOrNull("spo2_low_times") else null,
            // 덧붙는 값이라 형식이 달라도 밤 기록 전체를 버리지 않고 이 값만 비운다
            spo2Avg = o.percentOrNull("spo2_avg"),
            spo2Min = o.percentOrNull("spo2_min"),
            spo2Max = o.percentOrNull("spo2_max"),
            endAt = (o.opt("end_at") as? String)?.takeIf { ISO_WITH_OFFSET.matches(it) && runCatching { OffsetDateTime.parse(it) }.isSuccess },
        )
    }

    private fun sleepSession(o: JSONObject) = SleepSession(
        sessionId = o.string("session_id"),
        isMain = o.bool("is_main"),
        startAt = o.isoTime("start_at"),
        endAt = o.isoTime("end_at"),
        sleepMinutes = o.numberOrNull("sleep_minutes"),
        // null 은 그대로 두고 빈 목록으로 바꾸지 않는다(측정 없음과 "없음"은 다른 말이다)
        spo2LowCount = o.numberOrNull("spo2_low_count")?.toInt(),
        spo2LowTimes = o.isoTimesOrNull("spo2_low_times"),
    )

    private fun hourlyDay(o: JSONObject): HourlyDay {
        o.stringOrNull("reason")?.let { if (it !in HOURLY_REASONS) throw FormatError("reason") }
        val values = if (o.isNull("values")) null else o.array("values").let { a ->
            List(a.length()) { i -> (a.opt(i) as? Number)?.toDouble() ?: throw FormatError("values") }
        }
        return HourlyDay(o.string("date"), o.bool("is_in_progress"), values, o.numberOrNull("total"))
    }

    // 읽기 도구. org.json 의 getString·getDouble 은 구현마다 다른 타입을 바꿔 읽기도 해서 타입을 직접 확인한다
    private fun JSONObject.string(name: String): String = opt(name) as? String ?: throw FormatError(name)
    private fun JSONObject.stringOrNull(name: String): String? = if (isNull(name)) null else string(name)
    private fun JSONObject.number(name: String): Double =
        (opt(name) as? Number)?.toDouble()?.takeIf { it.isFinite() } ?: throw FormatError(name)
    private fun JSONObject.numberOrNull(name: String): Double? = if (isNull(name)) null else number(name)
    private fun JSONObject.bool(name: String): Boolean = opt(name) as? Boolean ?: throw FormatError(name)
    private fun JSONObject.obj(name: String): JSONObject = optJSONObject(name) ?: throw FormatError(name)
    private fun JSONObject.array(name: String): JSONArray = optJSONArray(name) ?: throw FormatError(name)
    private fun JSONArray.objects(): List<JSONObject> = List(length()) { optJSONObject(it) ?: throw FormatError("[]") }

    /** 0 초과 100 이하의 비율 값. 없거나 형식이 다르면 예외 없이 null */
    private fun JSONObject.percentOrNull(name: String): Double? =
        (opt(name) as? Number)?.toDouble()?.takeIf { it.isFinite() && it > 0 && it <= 100 }

    private fun JSONObject.datedOrNull(name: String): DatedValue? =
        if (isNull(name)) null else obj(name).let { DatedValue(it.string("date"), it.number("value")) }

    private fun iso(value: Any?, name: String): String {
        val text = value as? String ?: throw FormatError(name)
        if (!ISO_WITH_OFFSET.matches(text) || runCatching { OffsetDateTime.parse(text) }.isFailure) throw FormatError(name)
        return text
    }

    private fun JSONObject.isoTime(name: String): String = iso(opt(name), name)
    private fun JSONObject.isoTimesOrNull(name: String): List<String>? =
        if (isNull(name)) null else array(name).let { a -> List(a.length()) { iso(a.opt(it), name) } }
}
