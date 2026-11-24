package io.candiy.healthconnect.quickstart.coaching

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.candiy.healthconnect.quickstart.Link
import kotlinx.coroutines.delay
import org.json.JSONArray
import java.time.LocalDate

/**
 * debug 빌드 전용 근거 화면 미리보기(S15P31A403-440). 로그인·서버 없이 근거 상세의 차트·달력 모양만 확인한다(에뮬레이터·디자인 점검).
 * 값은 모두 지어낸 것이고 실제 건강 기록이 아니다. 시간대별 걸음도 서버를 부르지 않고 지어낸 값을 돌려준다.
 *
 *   adb shell am start -n io.candiy.healthconnect.quickstart/.MainActivity --ez evidencePreview true --es previewState SLEEP
 *
 * previewState: ALL(기본, 수면·활동·심박·운동 네 탭 28일) · SLEEP(30일과 비교 기간) · STEPS(14일, 오늘 수집 중) · HEART(28일과 겹치는 7일)
 *   · EXERCISE(30일) · SPO2(산소포화도 30일) · ONEDAY(하루 수면) · ONESESSION(운동 한 건) · EMPTY(오늘 운동 기록 없음)
 *   · BEFORELINK(연동 전 하루) · LONG(앞쪽 40일이 연동 전인 90일 걸음) · SPO2CMP(산소포화도 이번 주와 지난주)
 *   · MERGED(나눠 조회한 42일 심박) · ONEDAYLOW(90% 미만이 다섯 번인 하루 수면) · HOURLY(시간대 걸음 탭)
 *   · EXOVERLAP(하루 어긋난 두 기간으로 조회한 운동)
 */
object EvidencePreview {
    private val today: LocalDate get() = LocalDate.now()

    /** [end]까지 [count]일의 날짜 */
    private fun dates(count: Int, end: LocalDate = today) = List(count) { end.minusDays((count - 1 - it).toLong()).toString() }

    /** 날마다 조금씩 다른 규칙적인 값 */
    private fun wave(index: Int, base: Double, spread: Double) = base + (((index * 37) % 11) - 5) * spread

    private const val EMPTY_DETAIL = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[]"""

    private fun metric(
        id: String, position: Int, metric: String, dates: List<String>, values: List<Double?>,
        progressLast: Boolean = false, reasons: Map<Int, String> = emptyMap(), detail: String = EMPTY_DETAIL,
    ): String {
        fun reason(i: Int) = if (values[i] == null) reasons[i] ?: "NO_RECORD" else null
        val counted = dates.indices.filter { !(progressLast && it == dates.lastIndex) && reason(it) != "BEFORE_LINK" }
        val valued = counted.filter { values[it] != null }
        val average = if (valued.isEmpty()) "null" else valued.map { values[it]!! }.average().toString()
        fun dated(i: Int?) = i?.let { """{"date":"${dates[it]}","value":${values[it]}}""" } ?: "null"
        val days = dates.indices.joinToString(",") { i ->
            """{"date":"${dates[i]}","value":${values[i]},"missing":${values[i] == null},"reason":${reason(i)?.let { "\"$it\"" }},"is_in_progress":${progressLast && i == dates.lastIndex}}"""
        }
        val progress = if (!progressLast) "null" else
            """{"date":"${dates.last()}","value":${values.last()},"missing":${values.last() == null},"as_of":"${dates.last()}T05:30:00Z"}"""
        return """{"id":"$id","position":$position,"metric":"$metric","periodStart":"${dates.first()}","periodEnd":"${dates.last()}",
          "snapshot":{"metric":"$metric","period":{"start":"${dates.first()}","end":"${dates.last()}"},"unit":"",
          "summary":{"avg":$average,"daily_min":${dated(valued.minByOrNull { values[it]!! })},"daily_max":${dated(valued.maxByOrNull { values[it]!! })},"total":null,
            "days_with_data":${valued.size},"days_used_for_summary":${valued.size},"completed_days":${counted.size},"missing_days":[],"unsynced_days":[],"linked_from":null},
          "days":[$days],"in_progress":$progress,
          "data_status":{"last_data_at":null,"synced_until":null,"last_sync_status":"success","last_success_at":null,"checked_at":"${dates.last()}T05:30:00Z"},
          "detail":{$detail}}}"""
    }

    /** [lowAll]이면 모든 밤에 90% 미만이 다섯 번 있다(하루 수면 목록 확인용) */
    private fun sleep(id: String, position: Int, count: Int, end: LocalDate = today, nights: Boolean = true, lowAll: Boolean = false): String {
        val list = dates(count, end)
        val values = list.indices.map { wave(it, 430.0, 9.0) }
        val detail = if (!nights) EMPTY_DETAIL else {
            val rows = list.indices.joinToString(",") { i ->
                val previous = LocalDate.parse(list[i]).minusDays(1)
                val deep = 40 + (i * 13) % 45
                // 몇 밤은 산소포화도가 90% 아래로 내려간 적이 있다. 다섯 번이라 앞의 세 번만 보이고 나머지는 접힌다
                val isLow = lowAll || i % 9 == 4
                val low = if (isLow) {
                    """"spo2_low_count":5,"spo2_low_times":[${listOf("01:20", "02:10", "03:05", "04:30", "05:15").joinToString(",") { "\"${list[i]}T$it:00+09:00\"" }}]"""
                } else """"spo2_low_count":0,"spo2_low_times":[]"""
                // 엿새에 한 밤은 수면 단계 기록이 없다(일부 워치)
                val stages = if (i % 6 == 3) """"deep_minutes":null,"rem_minutes":null,"light_minutes":null,"awake_minutes":null"""
                else """"deep_minutes":$deep,"rem_minutes":${95 + (i * 7) % 30},"light_minutes":${250 + (i * 11) % 40},"awake_minutes":${12 + i % 10}"""
                """{"date":"${list[i]}","start_at":"${previous}T23:%02d:00+09:00","end_at":"${list[i]}T06:%02d:00+09:00","sleep_minutes":${values[i]},
                   $stages,
                   $low,"spo2_avg":${95.0 + (i % 5) * 0.4},"spo2_min":${if (isLow) 88.5 else 93.0 + (i % 4) * 0.5},"spo2_max":${97.5 + (i % 3) * 0.5}}""".format(5 + (i * 7) % 50, 20 + (i * 5) % 35)
            }
            """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[$rows]"""
        }
        return metric(id, position, "sleeps", list, values, detail = detail)
    }

    private fun steps(id: String, position: Int, count: Int, progressLast: Boolean = false): String {
        val list = dates(count)
        return metric(id, position, "steps", list, list.indices.map { if (progressLast && it == list.lastIndex) 3200.0 else wave(it, 9200.0, 420.0) }, progressLast)
    }

    private fun heart(id: String, position: Int, count: Int, end: LocalDate = today.minusDays(1), progressLast: Boolean = false): String {
        val list = dates(count, end)
        val values = list.indices.map { if (progressLast && it == list.lastIndex) 58.0 else wave(it, 72.0, 0.8) }
        val ranges = list.indices.joinToString(",") { """{"date":"${list[it]}","min":${44 + it % 8},"max":${136 + (it * 3) % 10},"sample_count":700}""" }
        val detail = """"measured_min":{"date":"${list.first()}","value":44},"measured_max":{"date":"${list[list.size / 2]}","value":145},"nights":[],"sessions":[],"daily_ranges":[$ranges]"""
        return metric(id, position, "heart_rates", list, values, progressLast, detail = detail)
    }

    private fun exercise(id: String, position: Int, count: Int, end: LocalDate = today.minusDays(1)): String {
        val list = dates(count, end)
        // 사흘에 한 번 쉰다
        val values = list.indices.map { if (it % 3 == 2) 0.0 else 55.0 + (it * 13) % 40 }
        val sessions = list.indices.filter { values[it] > 0 }.joinToString(",") { i ->
            """{"date":"${list[i]}","start_at":"${list[i]}T%02d:%02d:00+09:00","duration_minutes":${values[i]},"type_name":"${if (i % 2 == 0) "근력 운동" else "달리기"}","title":null,"counts_as_exercise":true}"""
                .format(if (i % 4 == 0) 10 else 21, 10 + (i * 7) % 40)
        }
        val worked = values.count { it > 0 }
        val activity = list.indices.joinToString(",") { i ->
            val on = values[i] > 0
            """{"date":"${list[i]}","exercise_count":${if (on) 1 else 0},"exercise_calories_kcal":${if (on) 420 + (i * 17) % 160 else 0},"exercise_distance_m":${if (on && i % 2 == 1) 4200 + i * 90 else "null"},
               "is_in_progress":false,"reason":null,"calories_reason":null,"distance_reason":${if (on && i % 2 == 1) "null" else "\"NULL_VALUE\""}}"""
        }
        val detail = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[$sessions],
          "exercise_activity":{"days":[$activity],
            "summary":{"exercise_count":{"total":$worked,"avg":${worked.toDouble() / list.size},"days_with_data":${list.size}},
              "exercise_calories_kcal":{"total":${worked * 500},"avg":500,"days_with_data":$worked},
              "exercise_distance_m":{"total":${worked * 2300},"avg":4600,"days_with_data":${worked / 2}}},
            "source_statuses":{"exercise_sessions":{"last_sync_status":"success"},"calories_burned":{"last_sync_status":"success"},"distances":{"last_sync_status":"success"}}}"""
        return metric(id, position, "exercises", list, values, detail = detail)
    }

    /** 산소포화도. 측정 최저·최고가 있어 요약이 한 상자로 나온다 */
    private fun spo2(id: String, position: Int, count: Int, end: LocalDate, base: Double): String {
        val list = dates(count, end)
        val values = list.indices.map { wave(it, base, 0.3) }
        val detail = """"measured_min":{"date":"${list[1]}","value":${base - 4.1}},"measured_max":{"date":"${list[list.size / 2]}","value":99.0},"nights":[],"daily_ranges":[],"sessions":[]"""
        return metric(id, position, "oxygen_saturations", list, values, detail = detail)
    }

    /** 시간대 걸음(kind hourly_steps). 9시~12시 사흘 */
    private fun hourlyKind(id: String, position: Int): String {
        val list = dates(3, today.minusDays(1))
        val days = list.indices.joinToString(",") { i ->
            val values = List(3) { hour -> 420 + i * 130 + hour * 210 }
            """{"date":"${list[i]}","is_in_progress":false,"reason":null,"values":[${values.joinToString(",")}],"total":${values.sum()}}"""
        }
        return """{"id":"$id","position":$position,"metric":"steps","periodStart":"${list.first()}","periodEnd":"${list.last()}",
          "snapshot":{"kind":"hourly_steps","start_hour":9,"end_hour":12,"days":[$days],"total":${(0 until 3).sumOf { i -> (0 until 3).sumOf { hour -> 420 + i * 130 + hour * 210 } }}}}"""
    }

    private fun items(kind: String): List<String> = when (kind.uppercase()) {
        // 산소포화도 이번 주와 지난주
        "SPO2CMP" -> listOf(spo2("p-spo2-now", 1, 7, today.minusDays(1), 95.6), spo2("p-spo2-before", 2, 7, today.minusDays(8), 94.9))
        // 42일 심박을 31일과 11일로 나눠 조회한 모양. 하나로 합쳐 주별 막대와 달력으로 보여 준다
        "MERGED" -> listOf(heart("p-heart-a", 1, 11, today.minusDays(32)), heart("p-heart-b", 2, 31))
        "ONEDAYLOW" -> listOf(sleep("p-sleep-low", 1, 1, today.minusDays(1), lowAll = true))
        "HOURLY" -> listOf(hourlyKind("p-hourly", 1))
        // 하루 어긋난 두 기간으로 조회한 운동(AI 가 운동과 그날 밤 수면을 견줄 때의 모양)
        "EXOVERLAP" -> listOf(exercise("p-exercise-a", 1, 14, today.minusDays(2)), exercise("p-exercise-b", 2, 14))
        "SLEEP" -> listOf(sleep("p-sleep", 1, 30), sleep("p-sleep-before", 2, 28, today.minusDays(30), nights = false))
        "STEPS" -> listOf(steps("p-steps", 1, 14, progressLast = true))
        "HEART" -> listOf(heart("p-heart", 1, 28), heart("p-heart-week", 2, 7, today, progressLast = true))
        "EXERCISE" -> listOf(exercise("p-exercise", 1, 30))
        "SPO2" -> listOf(metric("p-spo2", 1, "oxygen_saturations", dates(30), List(30) { wave(it, 95.6, 0.3) }))
        "ONEDAY" -> listOf(sleep("p-sleep-day", 1, 1, today.minusDays(1)))
        "ONESESSION" -> listOf(
            metric(
                "p-exercise-day", 1, "exercises", dates(1, today.minusDays(1)), listOf(78.0),
                detail = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[
                  {"date":"${today.minusDays(1)}","start_at":"${today.minusDays(1)}T21:27:00+09:00","duration_minutes":78,"type_name":"근력 운동","title":null,"counts_as_exercise":true}]""",
            ),
        )
        "EMPTY" -> listOf(metric("p-exercise-today", 1, "exercises", dates(1), listOf(null), progressLast = true, reasons = mapOf(0 to "UNSYNCED")))
        "BEFORELINK" -> listOf(metric("p-steps-before", 1, "steps", dates(1, today.minusDays(100)), listOf(null), reasons = mapOf(0 to "BEFORE_LINK")))
        "LONG" -> listOf(
            metric(
                "p-steps-long", 1, "steps", dates(90), List(90) { if (it < 40) null else wave(it, 7300.0, 450.0) },
                reasons = (0 until 40).associateWith { "BEFORE_LINK" },
            ),
        )
        else -> listOf(sleep("p-sleep", 1, 28), steps("p-steps", 2, 28), heart("p-heart", 3, 28), exercise("p-exercise", 4, 28))
    }

    fun evidence(kind: String): List<Evidence> = EvidenceJson.list(JSONArray("[${items(kind).joinToString(",")}]"))

    /** 지어낸 시간대별 걸음. 0~6시는 0, 18시가 가장 많다. 날짜 끝이 0 인 날은 기록이 없는 날이다 */
    fun hourly(): HourlyStepsLoader = HourlyStepsLoader { start, end ->
        delay(250)
        EvidenceView.datesBetween(start, end).associateWith { date ->
            val day = date.takeLast(2).toInt()
            if (day % 10 == 0) null else List(24) { hour -> if (hour < 7) 0.0 else if (hour == 18) 1500.0 + day * 10 else 200.0 + ((hour * 37 + day * 11) % 600) }
        }
    }
}

/** 근거 카드와 상세 창을 바로 띄우는 미리보기 화면. 창을 닫으면 카드의 "차트 보기"로 다시 연다 */
@Composable
fun EvidencePreviewScreen(kind: String) {
    val evidence = remember(kind) { EvidencePreview.evidence(kind) }
    // 근거 창은 열릴 때 앱 화면의 크기로 놓일 자리를 잡는다. 첫 화면이 놓이기 전에 열면 크기가 0이라 내용이 보이지 않아, 잠깐 뒤에 연다
    var open by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { delay(400); open = true }
    Column(
        Modifier.fillMaxSize().background(Link.Background).statusBarsPadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("근거 화면 미리보기 · ${kind.ifBlank { "ALL" }}", fontSize = 16.sp, color = Link.Text)
        Text("지어낸 값이에요. 실제 건강 기록이 아니에요.", fontSize = 13.sp, color = Link.Sub)
        EvidenceCard(evidence) { open = true }
    }
    if (open) {
        // 창을 열 때마다 조회기를 새로 만든다(실제 화면과 같다)
        EvidenceDialog(evidence, hourly = remember(kind, open) { EvidencePreview.hourly() }) { open = false }
    }
}
