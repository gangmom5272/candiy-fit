package io.candiy.healthconnect.quickstart.coaching

import org.json.JSONArray
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import io.candiy.healthconnect.quickstart.coaching.EvidenceView as V

/**
 * AI 답변 근거의 해석과 표시 규칙(S15P31A403-360). 웹 evidence-view · chart · format 단위 테스트와 같은 기대값을 쓴다.
 * 값은 모두 합성이다(명세 2.3 예시와 같은 모양).
 */
class EvidenceTest {
    private data class D(val date: String, val value: Double?, val reason: String? = null, val progress: Boolean = false)

    private fun day(d: D) =
        """{"date":"${d.date}","value":${d.value},"missing":${d.value == null},"reason":${d.reason?.let { "\"$it\"" }},"is_in_progress":${d.progress}}"""

    /** 날짜별 지표 근거 한 건. 평균·최저·최고는 진행 중이 아닌 값으로 낸다(백엔드와 같은 규칙) */
    private fun metric(
        metric: String,
        days: List<D>,
        id: String = "e-$metric-${days.first().date}",
        position: Int = 0,
        detail: String = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[]""",
    ): String {
        val counted = days.filter { !it.progress && it.reason != "BEFORE_LINK" }
        val values = counted.filter { it.value != null }
        val avg = if (values.isEmpty()) "null" else values.map { it.value!! }.average().toString()
        fun dated(d: D?) = d?.let { """{"date":"${it.date}","value":${it.value}}""" } ?: "null"
        return """{"id":"$id","position":$position,"metric":"$metric","periodStart":"${days.first().date}","periodEnd":"${days.last().date}",
          "snapshot":{"metric":"$metric","period":{"start":"${days.first().date}","end":"${days.last().date}"},"unit":"steps",
          "summary":{"avg":$avg,"daily_min":${dated(values.minByOrNull { it.value!! })},"daily_max":${dated(values.maxByOrNull { it.value!! })},"total":null,
            "days_with_data":${values.size},"days_used_for_summary":${values.size},"completed_days":${counted.size},
            "missing_days":[],"unsynced_days":[],"linked_from":null},
          "days":[${days.joinToString(",") { day(it) }}],"in_progress":null,
          "data_status":{"last_data_at":null,"synced_until":null,"last_sync_status":null,"last_success_at":null,"checked_at":"2026-10-08T00:00:00Z"},
          "detail":{$detail}}}"""
    }

    private fun parse(vararg items: String): List<Evidence> = EvidenceJson.list(JSONArray("[${items.joinToString(",")}]"))

    private fun week(start: Int, values: List<Double?>) = values.mapIndexed { i, v -> D("2026-10-%02d".format(start + i), v, if (v == null) "NO_RECORD" else null) }

    private val thisWeek = week(8, listOf(7000.0, 9000.0, null, 4000.0, 12000.0, 8000.0, 8000.0))
    private val lastWeek = week(1, listOf(6000.0, 6000.0, 6000.0, 6000.0, 6000.0, 6000.0, 6000.0))

    @Test fun `날짜별 지표 근거를 읽고 화면에 쓰는 값만 남긴다`() {
        val item = parse(metric("steps", thisWeek)).single() as MetricEvidence
        assertEquals("steps", item.metric)
        assertEquals(7, item.snapshot.days.size)
        assertEquals(6, item.snapshot.daysWithData)
        assertEquals(8000.0, item.snapshot.avg!!, 0.001)
        assertEquals(DatedValue("2026-10-11", 4000.0), item.snapshot.dailyMin)
        assertTrue(item.snapshot.days[2].missing)
    }

    @Test fun `형식이 다른 근거와 모르는 종류는 그 근거만 뺀다`() {
        val unknownKind = """{"id":"k","position":3,"metric":"exercises","periodStart":"2026-10-08","periodEnd":"2026-10-08","snapshot":{"kind":"exercise_intervals","period":{"start":"2026-10-08","end":"2026-10-08"}}}"""
        val badMetric = metric("steps", thisWeek).replace("\"metric\":\"steps\"", "\"metric\":\"unknown\"")
        val items = parse("{}", unknownKind, badMetric, metric("sleeps", lastWeek))
        assertEquals(listOf("sleeps"), items.map { it.metric })
        assertEquals(emptyList<Evidence>(), EvidenceJson.list(null))
    }

    @Test fun `같은 지표의 두 기간은 늦게 끝나는 쪽이 현재, 앞 기간이 비교다`() {
        val groups = V.groups(parse(metric("steps", lastWeek, position = 0), metric("steps", thisWeek, position = 1), metric("sleeps", thisWeek, position = 2)))
        assertEquals(listOf("steps", "sleeps"), groups.map { it.metric })
        assertEquals("2026-10-08", groups[0].current.periodStart)
        assertEquals("2026-10-01", groups[0].comparison!!.periodStart)
        assertEquals(2000.0, V.averageDelta(groups[0])!!, 0.001)
        assertNull(groups[1].comparison)
        assertNull(V.averageDelta(groups[1]))
    }

    @Test fun `주별로 여러 번 나눠 조회하면 한 범위로 합치고 조회한 기간을 모두 남긴다`() {
        // 조회는 최근 주부터 했다고 가정한다(position 이 작을수록 최근 주)
        val startDays = listOf(36, 29, 22, 15, 8, 1)
        val items = startDays.mapIndexed { i, start ->
            val first = java.time.LocalDate.of(2026, 9, 2).plusDays(start.toLong())
            metric("sleeps", List(7) { D(first.plusDays(it.toLong()).toString(), 380.0 + i) }, id = "w$i", position = i)
        }
        val group = V.groups(parse(*items.toTypedArray())).single()
        assertTrue(group.merged)
        assertNull(group.comparison)
        assertEquals(6, group.parts.size)
        assertEquals("2026-09-03", group.current.periodStart)
        assertEquals("2026-10-14", group.current.periodEnd)
        assertEquals(42, group.current.snapshot.days.size)
        assertEquals(listOf("2026-09-03", "2026-09-10", "2026-09-17"), group.parts.take(3).map { it.periodStart })
        // 전체 평균은 다시 계산하지 않고 기간별 평균을 그대로 둔다
        assertNull(group.current.snapshot.avg)
        assertTrue(group.parts.all { it.snapshot.avg != null })
        assertEquals(42, group.current.snapshot.daysWithData)
        assertTrue(V.coverage(group).isReliable)
        assertNull(V.coverageNote(V.coverage(group)))
    }

    @Test fun `31일 제한으로 둘로 나눠 조회한 이어진 기간은 비교가 아니라 한 범위다`() {
        fun range(start: String, count: Int, base: Double) = List(count) { D(java.time.LocalDate.parse(start).plusDays(it.toLong()).toString(), base + it) }
        val split = V.groups(parse(metric("steps", range("2026-10-01", 11, 7000.0), position = 0), metric("steps", range("2026-10-12", 31, 8000.0), position = 1))).single()
        assertTrue(split.merged)
        assertNull(split.comparison)
        assertEquals("2026.10.01 ~ 11.11", V.period(split.current.periodStart, split.current.periodEnd))
        assertEquals(8030.0, split.current.snapshot.dailyMax!!.value, 0.001)
        assertEquals(7000.0, split.current.snapshot.dailyMin!!.value, 0.001)

        fun pair(start: String, days: Int, nextStart: String, nextDays: Int) =
            V.groups(parse(metric("steps", range(start, days, 7000.0)), metric("steps", range(nextStart, nextDays, 8000.0), position = 1))).single()

        // 달력 한 달씩 맞춘 두 기간(지난달·이번 달)은 이어져도 비교다
        val months = pair("2026-09-01", 30, "2026-10-01", 31)
        assertFalse(months.merged)
        assertEquals("2026-09-01", months.comparison!!.periodStart)

        // AI 는 49일까지만 "남는 날 + 뒤 31일"로 나눈다. 그 모양이 아니면 길이가 달라도 비교다(S15P31A403-374)
        // 수요일에 묻는 이번 주와 지난주(7일·3일)
        val weeks = pair("2026-11-02", 7, "2026-11-09", 3)
        assertFalse(weeks.merged)
        assertEquals("2026-11-02", weeks.comparison!!.periodStart)
        // 월 중에 묻는 이번 달과 지난달(31일·12일)
        assertFalse(pair("2026-10-01", 31, "2026-11-01", 12).merged)
        // 경계: 앞 18일 + 뒤 31일(49일)까지 한 범위, 앞이 19일이면 비교
        assertTrue(pair("2026-09-24", 18, "2026-10-12", 31).merged)
        assertTrue(pair("2026-10-11", 1, "2026-10-12", 31).merged)
        assertFalse(pair("2026-09-23", 19, "2026-10-12", 31).merged)
        // 31일씩 이어진 두 기간(62일은 나눠 조회하지 않는다), 뒤 기간이 31일이 아닌 경우도 비교
        assertFalse(pair("2026-09-11", 31, "2026-10-12", 31).merged)
        assertFalse(pair("2026-10-01", 11, "2026-10-12", 30).merged)

        // 같은 기간을 두 번 조회한 근거는 하나로 센다
        val again = V.groups(parse(metric("steps", range("2026-10-01", 7, 7000.0), id = "a"), metric("steps", range("2026-10-01", 7, 7000.0), id = "b", position = 5))).single()
        assertFalse(again.merged)
        assertEquals("a", again.current.id)
    }

    @Test fun `기록 상태에 따라 안내와 차트 여부가 달라진다`() {
        fun snapshot(days: List<D>) = (parse(metric("steps", days)).single() as MetricEvidence).snapshot

        val full = snapshot(thisWeek)
        assertTrue(V.coverage(full).isReliable)
        assertNull(V.coverageNote(V.coverage(full)))
        assertTrue(V.showsChart(full))
        assertNull(V.chartSkipReason(full))

        val sparse = snapshot(week(8, listOf(7000.0, null, null, null, null, null, 5000.0)))
        assertFalse(V.coverage(sparse).isReliable)
        assertEquals("7일 중 2일", V.coverageNote(V.coverage(sparse)))
        assertTrue(V.showsChart(sparse))

        val single = snapshot(week(8, listOf(null, 7000.0, null)))
        assertFalse(V.showsChart(single))
        assertEquals("기록된 날이 하루뿐이라 차트를 그리지 않았어요.", V.chartSkipReason(single))
        assertEquals("10.09(금) 걸음 수", V.statTitle(parse(metric("steps", week(8, listOf(null, 7000.0, null)))).single() as MetricEvidence, V.coverage(single), "평균 걸음 수"))

        val oneDay = snapshot(listOf(D("2026-10-08", 7000.0)))
        assertEquals("하루 기록이라 차트 대신 값으로 보여 줘요.", V.chartSkipReason(oneDay))

        val none = snapshot(week(8, listOf(null, null, null)))
        assertEquals("기록 없음", V.coverageNote(V.coverage(none)))
        assertNull(V.chartSkipReason(none))

        val todayOnly = snapshot(listOf(D("2026-10-07", null, "NO_RECORD"), D("2026-10-08", 3000.0, progress = true)))
        assertEquals("오늘만 수집 중", V.coverageNote(V.coverage(todayOnly)))
        assertEquals(3000.0, V.coverage(todayOnly).today!!.value, 0.001)
        assertEquals("아직 오늘 기록만 있어 차트를 그리지 않았어요.", V.chartSkipReason(todayOnly))

        val zeros = snapshot(week(8, listOf(0.0, 0.0, 0.0)))
        assertEquals("모든 날 기록이 0이라 차트 대신 날짜별 기록으로 보여 줘요.", V.chartSkipReason(zeros))
    }

    @Test fun `막대는 31일까지 하루 한 칸, 더 길면 7일씩 묶는다`() {
        val daily = V.chartBars("steps", (parse(metric("steps", thisWeek)).single() as MetricEvidence).snapshot)
        assertFalse(daily.weekly)
        assertEquals(listOf("10.08", "10.11", "10.14"), daily.bars.filter { it.showLabel }.map { it.axisLabel })
        assertEquals("10.10(토)", daily.bars[2].title)
        assertNull(daily.bars[2].value)

        // 연동 전 날짜는 차트에서 뺀다
        val linked = listOf(D("2026-10-07", null, "BEFORE_LINK")) + thisWeek
        assertEquals(7, V.chartBars("steps", (parse(metric("steps", linked)).single() as MetricEvidence).snapshot).bars.size)

        val long = List(35) { D(java.time.LocalDate.of(2026, 9, 1).plusDays(it.toLong()).toString(), 60.0 + it) }
        val weeklySteps = V.chartBars("steps", (parse(metric("steps", long)).single() as MetricEvidence).snapshot)
        assertTrue(weeklySteps.weekly)
        assertEquals(5, weeklySteps.bars.size)
        assertEquals("09.01 ~ 09.07", weeklySteps.bars[0].title)
        // 주가 6개 이하면 축 글자도 기간으로 적어 막대 하나가 한 주임을 알린다. 더 많으면 자리가 없어 첫날만 쓴다
        assertEquals("09.01 ~ 09.07", weeklySteps.bars[0].axisLabel)
        assertEquals("09.29 ~ 10.05", weeklySteps.bars[4].axisLabel)
        val sevenWeeks = List(49) { D(java.time.LocalDate.of(2026, 9, 1).plusDays(it.toLong()).toString(), 60.0 + it) }
        assertEquals("09.01", V.chartBars("steps", (parse(metric("steps", sevenWeeks)).single() as MetricEvidence).snapshot).bars[0].axisLabel)
        assertEquals(63.0, weeklySteps.bars[0].value!!, 0.001)
        // 신체 지표는 평균을 다시 내지 않고 그 주 마지막 측정값을 쓴다
        assertEquals(66.0, V.chartBars("weights", (parse(metric("weights", long)).single() as MetricEvidence).snapshot).bars[0].value!!, 0.001)
    }

    @Test fun `축은 지표별 단위의 배수로 칸이 4개 이하가 된다`() {
        // 가장 큰 값 위에 여유를 두지 않는다. 12,000 이 맨 위 눈금이다(웹 404와 같다)
        assertEquals(listOf(0.0, 4000.0, 8000.0, 12000.0), V.chartScale("steps", listOf(7000.0, 12000.0, null), 8000.0).ticks)
        // 8시간 36분이 12h 축이 되지 않고 9h 에서 멈춘다
        assertEquals(listOf(0.0, 180.0, 360.0, 540.0), V.chartScale("sleeps", listOf(387.0, 516.0, 184.0), 343.0).ticks)
        assertEquals(listOf(0.0, 20.0, 40.0, 60.0, 80.0), V.chartScale("heart_rates", listOf(72.0, 80.0, 68.0), 74.0).ticks)
        assertEquals(listOf(0.0, 120.0, 240.0, 360.0, 480.0), V.chartScale("sleeps", listOf(420.0, 390.0), 405.0).ticks)
        // 100% 를 넘을 수 없는 지표는 축도 100 에서 멈춘다
        assertEquals(100.0, V.chartScale("oxygen_saturations", listOf(97.0, 98.0), 97.5).max, 0.001)
        assertEquals(listOf(0.0, 2000.0), V.chartScale("steps", listOf(null), null).ticks)
        assertEquals(listOf("0", "4k", "480분".replace("480분", "8h")), listOf(V.axisValue("steps", 0.0), V.axisValue("steps", 4000.0), V.axisValue("sleeps", 480.0)))
        // 막대 색은 평균 이상·미만 두 가지다. 기간 최저와 수집 중인 오늘을 따로 칠하지 않는다(웹 404, 앱 440)
        assertEquals(V.Tone.NORMAL, V.barTone(4000.0, 8000.0))
        assertEquals(V.Tone.HIGH, V.barTone(9000.0, 8000.0))
        assertEquals(V.Tone.HIGH, V.barTone(8000.0, 8000.0))
        // 평균선이 없는 차트는 한 가지 색이다
        assertEquals(V.Tone.PLAIN, V.barTone(9000.0, null))
    }

    @Test fun `값과 날짜를 웹과 같은 글자로 보여 준다`() {
        assertEquals("-", V.value("steps", null))
        assertEquals("7,203걸음", V.value("steps", 7203.4))
        assertEquals("6시간 45분", V.value("sleeps", 405.0))
        assertEquals("45분", V.value("exercises", 45.0))
        assertEquals("7시간", V.value("sleeps", 420.0))
        assertEquals("71.6bpm", V.value("heart_rates", 71.6))
        assertEquals("72bpm", V.value("heart_rates", 72.0))
        assertEquals("97.0%", V.value("oxygen_saturations", 97.0))
        assertEquals("68.5kg", V.value("weights", 68.52))
        assertEquals("68kg", V.value("weights", 68.0))
        assertEquals("1,520kcal", V.value("basal_metabolic_rates", 1520.0))
        assertEquals("172.5cm", V.value("heights", 1.725))
        assertEquals("+2,000걸음", V.delta("steps", 2000.0))
        assertEquals("-30분", V.delta("sleeps", -30.0))
        assertEquals("2026.10.01 ~ 10.07", V.period("2026-10-01", "2026-10-07"))
        assertEquals("2026.10.07", V.period("2026-10-07", "2026-10-07"))
        assertEquals("2026.12.29 ~ 2027.01.04", V.period("2026-12-29", "2027-01-04"))
        assertEquals("10.23(금)", V.dayWithWeekday("2026-10-23"))
        assertEquals("14:30", V.clock("2026-10-29T05:30:00Z"))
        assertEquals("14:30", V.clock("2026-10-29T14:30:00+09:00"))
    }

    @Test fun `수면 라벨은 잠든 시각으로 붙인다`() {
        assertEquals("11월 4일 밤", V.sleepLabel("2026-11-04T23:10:00+09:00"))
        assertEquals("11월 4일 밤", V.sleepLabel("2026-11-05T01:20:00+09:00"))
        assertEquals("11월 5일 아침", V.sleepLabel("2026-11-05T07:00:00+09:00"))
        assertEquals("11월 5일 낮", V.sleepLabel("2026-11-05T04:30:00Z"))
    }

    @Test fun `산소포화도 90% 미만은 네 가지 상태를 구분한다`() {
        assertEquals(V.Spo2Low.Unmeasured, V.spo2Low(null, null))
        assertEquals(V.Spo2Low.CountOnly(3), V.spo2Low(3, null))
        assertEquals(V.Spo2Low.None, V.spo2Low(0, emptyList()))
        assertEquals(V.Spo2Low.None, V.spo2Low(0, null))
        // Z 와 +09:00 이 섞여 와도 실제 시각 순으로 둔다
        val times = V.spo2Low(4, listOf("2026-11-05T03:10:00+09:00", "2026-11-04T17:40:00Z", "2026-11-05T04:00:00+09:00", "2026-11-05T05:00:00+09:00")) as V.Spo2Low.Times
        assertEquals("산소포화도 90% 미만 4회 · 02:40, 03:10, 04:00", V.spo2Text(times, expanded = false))
        assertEquals("산소포화도 90% 미만 4회 · 02:40, 03:10, 04:00, 05:00", V.spo2Text(times, expanded = true))
        assertEquals("산소포화도 90% 미만 · 3회 (시각 정보 없음)", V.spo2Text(V.Spo2Low.CountOnly(3), false))
        assertEquals("산소포화도 90% 미만 · 측정 없음", V.spo2Text(V.Spo2Low.Unmeasured, false))
    }

    @Test fun `이어지는 기록 없는 날은 이유가 같으면 한 줄로 묶는다`() {
        val days = (parse(metric("steps", listOf(
            D("2026-10-01", 5000.0), D("2026-10-02", null, "NO_RECORD"), D("2026-10-03", null, "NO_RECORD"),
            D("2026-10-04", null, "UNSYNCED"), D("2026-10-05", 6000.0, progress = true),
        ))).single() as MetricEvidence).snapshot.days
        assertEquals(
            listOf(
                V.DayItem.Day("2026-10-01", 5000.0, false),
                V.DayItem.Gap("2026-10-02", "2026-10-03", 2, "NO_RECORD"),
                V.DayItem.Gap("2026-10-04", "2026-10-04", 1, "UNSYNCED"),
                V.DayItem.Day("2026-10-05", 6000.0, true),
            ),
            V.dayItems(days),
        )
    }

    @Test fun `수면 단계·운동 기록·측정 최저 최고를 근거에서 꺼낸다`() {
        val nights = """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[
          {"date":"2026-10-08","start_at":"2026-10-07T23:00:00+09:00","end_at":"2026-10-08T06:00:00+09:00","sleep_minutes":400,"deep_minutes":80,"rem_minutes":90,"light_minutes":200,"awake_minutes":30,"spo2_low_count":0,"spo2_low_times":[]},
          {"date":"2026-10-09","start_at":"2026-10-08T23:30:00+09:00","end_at":"2026-10-09T06:30:00+09:00","sleep_minutes":410,"deep_minutes":null,"rem_minutes":null,"light_minutes":null,"awake_minutes":null}]"""
        val sleep = (parse(metric("sleeps", week(8, listOf(400.0, 410.0)), detail = nights)).single() as MetricEvidence).snapshot
        assertEquals(listOf("깊은 잠" to 1, "렘수면" to 1, "얕은 잠" to 1, "깬 시간" to 1), V.sleepStageAverages(sleep).map { it.label to it.nights })
        // 산소포화도 칸이 없는 예전 밤은 "측정 없음"으로 보이지 않게 뺀다
        assertEquals(listOf(V.NightSpo2("2026-10-08", V.Spo2Low.None)), V.nightSpo2(sleep))

        val sessions = """"measured_min":{"date":"2026-10-08","value":48},"measured_max":{"date":"2026-10-09","value":151},"nights":[],"daily_ranges":[],"sessions":[
          {"date":"2026-10-08","start_at":"2026-10-08T07:00:00+09:00","duration_minutes":30,"type_name":"달리기","title":null,"counts_as_exercise":true},
          {"date":"2026-10-09","start_at":"2026-10-09T19:00:00+09:00","duration_minutes":90,"type_name":"걷기","title":"산책","counts_as_exercise":false},
          {"date":"2026-10-09","start_at":"2026-10-09T07:00:00+09:00","duration_minutes":50,"type_name":"자전거","title":null,"counts_as_exercise":true}]"""
        val exercise = (parse(metric("exercises", week(8, listOf(30.0, 50.0)), detail = sessions)).single() as MetricEvidence).snapshot
        val items = V.sessionItems(exercise)
        assertEquals(listOf("걷기", "자전거", "달리기"), items.map { it.session.typeName })
        // 가장 긴 표시는 운동으로 인정된 기록에만 붙는다
        assertEquals(listOf(false, true, false), items.map { it.isLongest })
        assertNull(V.measuredRange("exercises", exercise))
        assertEquals(DatedValue("2026-10-09", 151.0), V.measuredRange("heart_rates", exercise)!!.second)
    }

    @Test fun `수면 기록과 시간대 걸음 근거를 읽는다`() {
        val sleepSessions = """{"id":"s1","position":1,"metric":"sleeps","periodStart":"2026-11-04","periodEnd":"2026-11-05","snapshot":{"kind":"sleep_sessions","period":{"start":"2026-11-04","end":"2026-11-05"},"sessions":[
          {"session_id":"b","is_main":false,"date":"2026-11-05","start_at":"2026-11-05T13:00:00+09:00","end_at":"2026-11-05T13:40:00+09:00","sleep_minutes":35,"session_minutes":40,"spo2_low_count":null,"spo2_low_times":null},
          {"session_id":"a","is_main":true,"date":"2026-11-05","start_at":"2026-11-04T23:00:00+09:00","end_at":"2026-11-05T06:30:00+09:00","sleep_minutes":420,"session_minutes":450,"spo2_low_count":2,"spo2_low_times":["2026-11-05T02:00:00+09:00","2026-11-05T03:00:00+09:00"]}]}}"""
        val hourly = """{"id":"h1","position":2,"metric":"steps","periodStart":"2026-11-04","periodEnd":"2026-11-05","snapshot":{"kind":"hourly_steps","period":{"start":"2026-11-04","end":"2026-11-05"},"start_hour":6,"end_hour":9,"days":[
          {"date":"2026-11-04","is_in_progress":false,"values":[120,800,1500],"total":2420,"reason":null},
          {"date":"2026-11-05","is_in_progress":true,"values":null,"total":null,"reason":"NO_RECORD"}],"total":null}}"""
        val badTime = sleepSessions.replace("2026-11-05T13:00:00+09:00", "13:00")
        val items = parse(sleepSessions, hourly, badTime)
        assertEquals(2, items.size)
        assertEquals(listOf("수면 기록", "시간대 걸음"), V.kinds(items).map(V::kindLabel))
        assertEquals(emptyList<V.Group>(), V.groups(items))

        val view = V.sleepSessions(items[0] as SleepSessionsEvidence)
        assertEquals(listOf("a", "b"), view.sessions.map { it.sessionId })
        assertEquals(1, view.mainCount)
        assertEquals(1, view.extraCount)
        // 주 수면 평균에는 추가 수면을 넣지 않는다
        assertEquals(420.0, view.mainAverage!!, 0.001)
        assertEquals(V.Spo2Low.Unmeasured, V.spo2Low(view.sessions[1].spo2LowCount, view.sessions[1].spo2LowTimes))

        val steps = items[1] as HourlyStepsEvidence
        assertEquals(listOf(6, 7, 8), V.hourlyColumns(steps))
        assertEquals("1,500", V.count(steps.days[0].values!![2]))
        assertNull(steps.days[1].values)
        assertNull(steps.total)
    }

    // ----- 운동 횟수·칼로리·거리(S15P31A403-375). 웹 evidence-view.spec 과 같은 합성 값·기대값 -----

    private fun activityDay(date: String, count: Int?, kcal: Int?, meters: Int?, reason: String? = null, distanceReason: String? = null, progress: Boolean = false): String {
        fun q(v: String?) = v?.let { "\"$it\"" }
        return """{"date":"$date","exercise_minutes":30,"exercise_count":$count,"exercise_calories_kcal":$kcal,"exercise_distance_m":$meters,
          "computed_at":"2026-10-28T05:00:00Z","is_in_progress":$progress,"reason":${q(reason)},"calories_reason":${q(reason)},"distance_reason":${q(reason ?: distanceReason)}}"""
    }

    private val activityDays = listOf(
        activityDay("2026-10-22", 1, 240, 4200),
        activityDay("2026-10-23", 0, 0, 0),
        activityDay("2026-10-24", 2, 410, null, distanceReason = "NULL_VALUE"),
        activityDay("2026-10-25", null, null, null, reason = "NO_RECORD"),
        activityDay("2026-10-26", 1, 520, 8300),
        activityDay("2026-10-27", 0, 0, 0),
        activityDay("2026-10-28", 1, 100, 2000, progress = true),
    )

    private fun activityDetail(days: List<String> = activityDays, summary: String = """"exercise_minutes":{"total":135,"avg":27,"days_with_data":5},
        "exercise_count":{"total":4,"avg":0.8,"days_with_data":5},"exercise_calories_kcal":{"total":1170,"avg":234,"days_with_data":5},
        "exercise_distance_m":{"total":12500,"avg":3125,"days_with_data":4}""") =
        """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[],
        "exercise_activity":{"days":[${days.joinToString(",")}],"summary":{$summary},
        "source_statuses":{"exercise_sessions":{"ready":true,"last_sync_status":"success","last_data_at":null,"last_success_at":null},
          "calories_burned":{"ready":true,"last_sync_status":"fail","last_data_at":null,"last_success_at":null},
          "distances":{"ready":null,"last_sync_status":null,"last_data_at":null,"last_success_at":null}}}"""

    private val exerciseWeek = listOf(D("2026-10-22", 30.0), D("2026-10-23", 0.0), D("2026-10-24", 45.0), D("2026-10-25", null, "NO_RECORD"), D("2026-10-26", 60.0), D("2026-10-27", 0.0), D("2026-10-28", 20.0, progress = true))

    @Test fun `운동 근거의 횟수·칼로리·거리를 읽어 합계와 날짜별 값으로 보여 준다`() {
        val item = parse(metric("exercises", exerciseWeek, detail = activityDetail())).single() as MetricEvidence
        val view = V.activity(item.snapshot)!!

        assertEquals(
            listOf(
                V.ActivityCard("운동 횟수", "4회", listOf("하루 평균 0.8회", "기록 5일")),
                V.ActivityCard("운동 칼로리", "1,170kcal", listOf("하루 평균 234kcal", "기록 5일")),
                V.ActivityCard("운동 거리", "12.5km", listOf("하루 평균 3.1km", "기록 4일")),
            ),
            view.cards,
        )
        // 0은 값으로, 값이 없으면 항목별 이유로 쓴다
        assertEquals(
            listOf(
                listOf("1회", "240kcal", "4.2km"), listOf("0회", "0kcal", "0.0km"), listOf("2회", "410kcal", "기록 없음"),
                listOf("기록 없음", "기록 없음", "기록 없음"), listOf("1회", "520kcal", "8.3km"), listOf("0회", "0kcal", "0.0km"),
                listOf("1회", "100kcal", "2.0km"),
            ),
            view.rows.map { it.cells },
        )
        assertTrue(view.rows.last().inProgress)
        assertTrue(view.notes[1].contains("하루 전체 소모 칼로리나 이동 거리가 아니에요"))
        assertTrue("오늘 값은 수집 중이며 합계·평균에서 제외해요." in view.notes)
        assertTrue("칼로리 수집에 실패해 이전에 저장된 기록 기준이에요." in view.notes)
    }

    @Test fun `운동 횟수·칼로리·거리가 없거나 모양이 다르면 그 묶음만 빼고 근거는 남긴다`() {
        // 예전 근거: 키가 없다
        val old = parse(metric("exercises", exerciseWeek)).single() as MetricEvidence
        assertNull(old.snapshot.activity)
        assertNull(V.activity(old.snapshot))

        // 통계 한 항목이 빠진 근거: 묶음만 버리고 운동 시간 근거는 그대로다
        val broken = parse(metric("exercises", exerciseWeek, detail = activityDetail(summary = """"exercise_count":{"total":4,"avg":0.8,"days_with_data":5}"""))).single() as MetricEvidence
        assertNull(broken.snapshot.activity)
        assertEquals(7, broken.snapshot.days.size)
    }

    @Test fun `합계 없이 오늘 값만 있으면 오늘 값을 보여 주고, 연동 전·동기화 전은 이유를 쓴다`() {
        val empty = """"exercise_count":{"total":null,"avg":null,"days_with_data":0},"exercise_calories_kcal":{"total":null,"avg":null,"days_with_data":0},
            "exercise_distance_m":{"total":null,"avg":null,"days_with_data":0}"""
        val today = parse(metric("exercises", exerciseWeek.takeLast(1), detail = activityDetail(activityDays.takeLast(1), empty))).single() as MetricEvidence
        val view = V.activity(today.snapshot)!!
        assertEquals(V.ActivityCard("오늘 운동 횟수", "1회", listOf("수집 중")), view.cards.first())
        assertFalse("오늘 값은 수집 중이며 합계·평균에서 제외해요." in view.notes)

        val days = listOf(
            activityDay("2026-10-22", null, null, null, reason = "BEFORE_LINK"),
            """{"date":"2026-10-23","exercise_count":0,"exercise_calories_kcal":null,"exercise_distance_m":0,"is_in_progress":false,"reason":null,"calories_reason":"UNSYNCED","distance_reason":null}""",
        )
        val reasons = parse(metric("exercises", exerciseWeek.take(2), detail = activityDetail(days))).single() as MetricEvidence
        assertEquals(listOf(listOf("연동 전", "연동 전", "연동 전"), listOf("0회", "동기화 전", "0.0km")), V.activity(reasons.snapshot)!!.rows.map { it.cells })
    }

    @Test fun `나눠 조회한 운동 근거는 날짜별 값을 잇고 합계는 다시 내지 않는다`() {
        fun part(from: Int, to: Int, position: Int) =
            metric("exercises", exerciseWeek.subList(from, to), position = position, detail = activityDetail(activityDays.subList(from, to)))
        val group = V.groups(parse(part(4, 7, 0), part(2, 4, 1), part(0, 2, 2))).single()
        val view = V.activity(group.current.snapshot)!!

        assertTrue(group.merged)
        assertEquals(7, view.rows.size)
        assertEquals("2026-10-22", view.rows.first().date)
        assertTrue(view.cards.isEmpty())
        assertTrue("합계와 평균은 조회한 기간별로 받아, 여기서 다시 더하지 않았어요." in view.notes)
        assertTrue(view.notes.any { it.startsWith("칼로리 수집에 실패") })
    }

    @Test fun `횟수·칼로리는 정수, 거리는 km 소수 한 자리, 하루 평균 횟수만 소수 한 자리다`() {
        assertEquals("4회", V.activityValue(V.ActivityField.COUNT, 4.0))
        assertEquals("1,170kcal", V.activityValue(V.ActivityField.CALORIES, 1170.4))
        assertEquals("12.5km", V.activityValue(V.ActivityField.DISTANCE, 12500.0))
        assertEquals("0.0km", V.activityValue(V.ActivityField.DISTANCE, 0.0))
        assertEquals("0.8회", V.activityValue(V.ActivityField.COUNT, 0.8, average = true))
        assertEquals("2회", V.activityValue(V.ActivityField.COUNT, 2.0, average = true))
        assertEquals("235kcal", V.activityValue(V.ActivityField.CALORIES, 234.5, average = true))
    }

    // ----- 근거 화면 문구 다듬기(S15P31A403-384) -----

    @Test fun `수면 단계 평균 안내는 며칠 중 며칠의 평균인지 날 수로 말한다`() {
        assertEquals("7일 중 3일의 평균이에요.", V.stageNote(periodDays = 7, counted = 3, skipped = 0))
        assertEquals("7일 중 5일의 평균이에요. 수면 단계 기록이 없는 2일은 뺐어요.", V.stageNote(7, 5, 2))
        assertEquals("7일 평균이에요.", V.stageNote(7, 7, 0))
        assertEquals("이 날 하루의 수면 단계예요.", V.stageNote(1, 1, 0))
    }

    @Test fun `측정 최저 최고 안내는 기간의 날 수로 말한다`() {
        assertEquals("7일 동안 측정된 값 중 가장 낮은 값과 가장 높은 값이에요.", V.measuredNote(7))
        assertEquals("이 날 측정된 값 중 가장 낮은 값과 가장 높은 값이에요.", V.measuredNote(1))
    }

    @Test fun `밤별 산소포화도에 그 밤의 평균을 함께 싣고, 값이 없거나 형식이 다르면 평균만 비운다`() {
        fun night(extra: String) = """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[
          {"date":"2026-10-08","start_at":"2026-10-07T23:00:00+09:00","end_at":"2026-10-08T06:00:00+09:00","sleep_minutes":400,"deep_minutes":80,
           "rem_minutes":90,"light_minutes":200,"awake_minutes":30,"spo2_low_count":0,"spo2_low_times":[]$extra}]"""
        fun parsed(extra: String) = V.nightSpo2((parse(metric("sleeps", listOf(D("2026-10-08", 400.0)), detail = night(extra))).single() as MetricEvidence).snapshot).single()

        assertEquals(96.4, parsed(""","spo2_avg":96.4""").average!!, 0.001)
        assertNull(parsed("").average)
        assertNull(parsed(""","spo2_avg":null""").average)
        // 형식이 달라도 밤 기록과 90% 미만 여부는 그대로 남는다
        val broken = parsed(""","spo2_avg":"높음"""")
        assertNull(broken.average)
        assertEquals(V.Spo2Low.None, broken.state)
    }

    @Test fun `밤이 많으면 산소포화도를 평균·최저·최고로 요약하고, 한 주 이하면 요약하지 않는다`() {
        // 합성 값: 10일 밤. 평균은 95.0~95.9, 최저는 넷째 밤, 최고는 일곱째 밤, 90% 미만은 두 밤
        fun nights(count: Int) = (1..count).joinToString(",") { i ->
            val d = "2026-10-%02d".format(i + 1)
            val low = if (i == 4 || i == 9) 2 else 0
            """{"date":"$d","start_at":"2026-10-%02dT23:00:00+09:00","end_at":"${d}T06:00:00+09:00","sleep_minutes":400,"deep_minutes":80,
               "rem_minutes":90,"light_minutes":200,"awake_minutes":30,"spo2_low_count":$low,"spo2_low_times":null,
               "spo2_avg":${95.0 + (i - 1) * 0.1},"spo2_min":${if (i == 4) 86.0 else 91.0 + i * 0.1},"spo2_max":${if (i == 7) 99.5 else 97.0}}""".format(i)
        }
        fun snapshot(count: Int) = (parse(metric("sleeps", List(count) { D("2026-10-%02d".format(it + 2), 400.0) },
            detail = """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[${nights(count)}]""")).single() as MetricEvidence).snapshot

        assertNull(V.spo2Summary(snapshot(7)))

        val summary = V.spo2Summary(snapshot(10))!!
        assertEquals(95.45, summary.average!!, 0.001)
        assertEquals(V.Spo2Point(86.0, "2026-10-05"), summary.lowest)
        assertEquals(V.Spo2Point(99.5, "2026-10-08"), summary.highest)
        assertEquals(10, summary.nights)
        assertEquals(2, summary.lowNights)
        assertEquals("90% 미만으로 측정된 날은 2일이에요.", V.spo2SummaryNote(summary))
        assertEquals("90% 미만으로 측정된 날은 없어요.", V.spo2SummaryNote(summary.copy(lowNights = 0)))
        // 밤별 목록은 그대로 남아 펼치면 모두 보인다
        assertEquals(10, V.nightSpo2(snapshot(10)).size)
    }

    @Test fun `산소포화도 차트는 꺾은선이고 축이 90에서 시작한다, 더 낮은 값이 있으면 아래로 넓힌다`() {
        assertTrue(V.isLineChart("oxygen_saturations"))
        assertFalse(V.isLineChart("heart_rates"))

        val usual = V.chartScale("oxygen_saturations", listOf(96.0, 97.5, null, 95.2), 96.2)
        assertEquals(90.0, usual.min, 0.001)
        assertEquals(listOf(90.0, 95.0, 100.0), usual.ticks)
        assertEquals(0.0f, usual.fraction(90.0), 0.001f)
        assertEquals(0.5f, usual.fraction(95.0), 0.001f)
        assertEquals(1.0f, usual.fraction(100.0), 0.001f)

        // 90보다 낮은 값이 있으면 그 값이 축 안에 들어가게 한다
        assertEquals(listOf(85.0, 90.0, 95.0, 100.0), V.chartScale("oxygen_saturations", listOf(96.0, 86.0), 91.0).ticks)
        // 칸이 4개를 넘게 되면 간격을 10으로 늘린다
        assertEquals(listOf(70.0, 80.0, 90.0, 100.0), V.chartScale("oxygen_saturations", listOf(96.0, 72.0), null).ticks)
        // 기록이 없어도 90~100 축을 둔다
        assertEquals(listOf(90.0, 95.0, 100.0), V.chartScale("oxygen_saturations", listOf(null), null).ticks)

        // 다른 지표는 0에서 시작한다
        assertEquals(0.0, V.chartScale("heart_rates", listOf(62.0, 70.0), 66.0).min, 0.001)
    }

    // ----- 겹치는 기간을 합칠 때 같은 기록을 한 번만 센다(S15P31A403-391, 웹 317과 같은 규칙) -----

    @Test fun `조회한 기간이 겹쳐도 밤·날짜·기록 일수를 한 번씩만 센다`() {
        fun date(day: Int) = "2026-10-%02d".format(day)
        fun nightsJson(from: Int, to: Int) = (from..to).joinToString(",") { d ->
            """{"date":"${date(d)}","start_at":"2026-10-%02dT23:00:00+09:00","end_at":"${date(d)}T06:00:00+09:00","sleep_minutes":${400 + d},"deep_minutes":80,
               "rem_minutes":90,"light_minutes":200,"awake_minutes":30,"spo2_low_count":0,"spo2_low_times":null,"spo2_avg":96.0}""".format(d - 1)
        }
        fun part(from: Int, to: Int, position: Int) = metric(
            "sleeps", (from..to).map { D(date(it), 400.0 + it) }, position = position,
            detail = """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[${nightsJson(from, to)}]""",
        )
        // 10.02~10.08, 10.06~10.12, 10.10~10.16: 서로 일부만 겹치는 세 번의 조회(어느 기간도 다른 기간에 통째로 덮이지 않는다)
        val group = V.groups(parse(part(10, 16, 0), part(6, 12, 1), part(2, 8, 2))).single()
        val s = group.current.snapshot

        assertTrue(group.merged)
        assertTrue(group.overlaps.isEmpty())
        assertEquals("2026.10.02 ~ 10.16", V.period(group.current.periodStart, group.current.periodEnd))
        assertEquals(15, s.days.size)
        assertEquals(15, s.nights.size)
        assertEquals(s.nights.map { it.date }, s.nights.map { it.date }.distinct().sorted())
        // 기록 일수도 합친 날짜에서 다시 센다(겹친 만큼 더해지지 않는다)
        assertEquals(15, s.daysWithData)
        assertEquals(15, s.completedDays)
        assertEquals(15, V.coverage(group).recordedDays)
        // 밤별 산소포화도 요약과 수면 단계 평균도 겹친 밤을 한 번만 쓴다
        assertEquals(15, V.spo2Summary(s)!!.nights)
        assertEquals(15, V.sleepStageAverages(s).maxOf { it.nights })
        // 조회한 기간별 평균은 그대로 세 줄이다
        assertEquals(3, group.parts.size)
    }

    @Test fun `겹치는 기간의 운동 기록은 시작 시각이 같으면 하나로 본다`() {
        fun session(start: String, minutes: Int) =
            """{"date":"2026-10-05","start_at":"$start","duration_minutes":$minutes,"type_name":"달리기","title":null,"counts_as_exercise":true}"""
        fun part(from: Int, to: Int, position: Int, sessions: String) = metric(
            "exercises", (from..to).map { D("2026-10-%02d".format(it), 30.0) }, position = position,
            detail = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[$sessions]""",
        )
        // 같은 운동이 한 번은 UTC(Z), 한 번은 +09:00 으로 왔다. 다른 운동 하나는 한쪽에만 있다
        val group = V.groups(parse(
            part(1, 7, 0, session("2026-10-05T10:00:00Z", 40)),
            part(4, 10, 1, session("2026-10-05T19:00:00+09:00", 40) + "," + session("2026-10-08T10:00:00Z", 25)),
            part(9, 12, 2, ""),
        )).single()

        assertTrue(group.merged)
        assertEquals(2, group.current.snapshot.sessions.size)
        assertEquals(listOf(40.0, 25.0), group.current.snapshot.sessions.map { it.durationMinutes })
        assertEquals(12, group.current.snapshot.days.size)
    }

    @Test fun `일별 차트는 일주일을 넘으면 밀어 보고, 최근에 기록이 없으면 값이 있는 마지막 날에서 시작한다`() {
        fun bars(values: List<Double?>) = values.mapIndexed { i, v -> V.Bar("d$i", "", false, "", v, false) }
        assertTrue(V.scrollsByWeek(V.Bars(false, bars(List(8) { 1.0 }))))
        assertFalse(V.scrollsByWeek(V.Bars(false, bars(List(7) { 1.0 }))))
        // 주 단위로 묶은 막대는 밀지 않는다
        assertFalse(V.scrollsByWeek(V.Bars(true, bars(List(9) { 1.0 }))))
        assertEquals(23, V.firstVisible(bars(List(30) { 1.0 })))
        // 최근 8일이 비어 있으면 값이 있는 마지막 날(22번째)이 오른쪽 끝에 온다
        assertEquals(15, V.firstVisible(bars(List(22) { 1.0 } + List(8) { null })))
        assertEquals(23, V.firstVisible(bars(List(30) { null })))
        assertEquals("한 달", V.wholeViewLabel(30))
        assertEquals("14일", V.wholeViewLabel(14))
    }

    @Test fun `칸이 좁아 꼬리표가 양옆 칸을 덮으면 그 가운데 가장 높은 막대 위에 둔다`() {
        val bars = listOf(5.0, 9.0, 3.0, null, 7.0).mapIndexed { i, v -> V.Bar("d$i", "", false, "", v, false) }
        assertEquals(3.0, V.extremeLift(bars, 2, 0))
        assertEquals(9.0, V.extremeLift(bars, 2, 1))
        // 기록이 없는 칸과 차트 밖은 건너뛴다
        assertEquals(7.0, V.extremeLift(bars, 4, 1))
        assertEquals(9.0, V.extremeLift(bars, 0, 1))
    }

    @Test fun `가장 높은 날과 낮은 날은 지표에 맞는 말로 부른다`() {
        val s = MetricSnapshot(
            avg = null, dailyMin = DatedValue("2026-10-03", 2.0), dailyMax = DatedValue("2026-10-07", 9.0),
            daysWithData = 0, completedDays = 0, unsyncedDays = 0, days = emptyList(), inProgress = null,
            measuredMin = null, measuredMax = null, nights = emptyList(), sessions = emptyList(),
        )
        assertEquals(listOf("가장 길게 잔 날", "가장 짧게 잔 날"), V.dayHighlights("sleeps", s).map { it.label })
        assertEquals(listOf("가장 많이 걸은 날", "가장 적게 걸은 날"), V.dayHighlights("steps", s).map { it.label })
        assertEquals(listOf("가장 오래 운동한 날", "가장 짧게 운동한 날"), V.dayHighlights("exercises", s).map { it.label })
        // 수치의 높낮이가 그대로 뜻인 지표는 바꾸지 않는다
        assertEquals(listOf("가장 높은 날", "가장 낮은 날"), V.dayHighlights("heart_rates", s).map { it.label })
    }

    // ── 웹 430~439 를 앱으로 옮긴 규칙(S15P31A403-440) ─────────────────────────

    private fun days(start: String, values: List<Double?>, reasons: Map<Int, String> = emptyMap(), progressLast: Boolean = false): List<D> {
        val first = java.time.LocalDate.parse(start)
        return values.mapIndexed { i, v -> D(first.plusDays(i.toLong()).toString(), v, if (v == null) reasons[i] ?: "NO_RECORD" else null, progressLast && i == values.lastIndex) }
    }

    private fun snapshot(metric: String, list: List<D>, detail: String? = null): MetricSnapshot =
        (parse(if (detail == null) metric(metric, list) else metric(metric, list, detail = detail)).single() as MetricEvidence).snapshot

    @Test fun `다른 기간에 통째로 겹치는 기간은 기간 구분에서 빼고 따로 남긴다`() {
        // 이전 7일 · 최근 7일 · 14일 전체: 14일 전체가 빠지고 7일 둘이 비교로 남는다
        val whole = metric("steps", days("2026-10-01", List(14) { 8000.0 }), position = 0)
        val previous = metric("steps", days("2026-10-01", List(7) { 7000.0 }), position = 1)
        val recent = metric("steps", days("2026-10-08", List(7) { 9000.0 }), position = 2)
        val group = V.groups(parse(whole, previous, recent)).single()
        assertEquals("2026-10-08", group.current.periodStart)
        assertEquals("2026-10-01", group.comparison?.periodStart)
        assertEquals(listOf("2026-10-01" to "2026-10-14"), group.overlaps.map { it.periodStart to it.periodEnd })
        assertFalse(group.merged)
    }

    @Test fun `일부만 겹친 두 기간은 긴 쪽을 보여 주고 길이가 같으면 늦게 끝나는 쪽이다`() {
        // 심박 28일(9.30~10.27)과 하루 더 늦게 끝나는 최근 7일(10.22~10.28). 걸음은 28일 한 번
        val steps = metric("steps", days("2026-09-30", List(28) { 8000.0 }), position = 0)
        val long = metric("heart_rates", days("2026-09-30", List(28) { 72.0 }), position = 1)
        val short = metric("heart_rates", days("2026-10-22", List(7) { 71.0 }), position = 2)
        val groups = V.groups(parse(steps, long, short))
        val heart = groups.first { it.metric == "heart_rates" }
        // 전에는 늦게 끝나는 7일이 차트로 나와 심박 탭만 7일, 걸음 탭은 28일이었다
        assertEquals(groups.first { it.metric == "steps" }.current.periodStart to "2026-10-27", heart.current.periodStart to heart.current.periodEnd)
        assertNull(heart.comparison)
        assertEquals(listOf("2026-10-22" to "2026-10-28"), heart.overlaps.map { it.periodStart to it.periodEnd })

        // 하루 어긋난 같은 길이의 두 기간(운동을 14일씩 두 번 조회)은 오늘이 든 쪽이 남는다
        val first = metric("exercises", days("2026-10-14", List(14) { 60.0 }), position = 0)
        val second = metric("exercises", days("2026-10-15", List(14) { 60.0 }), position = 1)
        val exercise = V.groups(parse(first, second)).single()
        assertEquals("2026-10-15", exercise.current.periodStart)
        assertEquals(listOf("2026-10-14"), exercise.overlaps.map { it.periodStart })
    }

    @Test fun `달력의 한 바퀴 기준은 기간 평균이고 운동은 운동한 날 평균이다`() {
        val steps = snapshot("steps", days("2026-10-01", listOf(6000.0, 8000.0, 10000.0)))
        assertEquals(V.CalendarBase(8000.0, V.CalendarBasis.SUMMARY), V.calendarAverage("steps", steps))
        // 운동은 쉰 날(0분)을 빼고 평균한다. AI 가 받은 평균(쉰 날 포함 40분)과 다르다
        val exercise = snapshot("exercises", days("2026-10-01", listOf(60.0, 0.0, 90.0, 0.0, 30.0)))
        assertEquals(V.CalendarBase(60.0, V.CalendarBasis.ACTIVE_DAYS), V.calendarAverage("exercises", exercise))
        // 산소포화도·신체 지표는 달력을 쓰지 않는다. 운동이 모두 0분이어도 쓰지 않는다
        assertNull(V.calendarAverage("oxygen_saturations", snapshot("oxygen_saturations", days("2026-10-01", listOf(96.0, 97.0)))))
        assertNull(V.calendarAverage("exercises", snapshot("exercises", days("2026-10-01", listOf(0.0, 0.0)))))
    }

    @Test fun `달력은 기간이 이틀 이상이고 견줄 기록이 있으면 길이와 관계없이 쓴다`() {
        fun shows(metric: String, s: MetricSnapshot) = V.showsCalendar(metric, s, V.coverage(s))
        // 걸음도 짧은 기간부터 달력이다(웹 439). 전에는 15일 이상일 때만이었다
        assertTrue(shows("steps", snapshot("steps", days("2026-10-01", listOf(7000.0, 9000.0)))))
        assertTrue(shows("heart_rates", snapshot("heart_rates", days("2026-10-01", List(7) { 70.0 + it }))))
        // 하루 기간, 기록 없음, 달력을 쓰지 않는 지표
        assertFalse(shows("steps", snapshot("steps", days("2026-10-01", listOf(7000.0)))))
        assertFalse(shows("steps", snapshot("steps", days("2026-10-01", listOf(null, null)))))
        assertFalse(shows("oxygen_saturations", snapshot("oxygen_saturations", days("2026-10-01", listOf(96.0, 97.0)))))
    }

    @Test fun `달력의 최고·최저 날은 AI 가 받은 날이고 운동은 운동한 날 가운데서 고른다`() {
        val sleep = snapshot("sleeps", days("2026-10-01", listOf(400.0, 480.0, 360.0)))
        assertEquals(DatedValue("2026-10-02", 480.0) to DatedValue("2026-10-03", 360.0), V.calendarExtremes("sleeps", sleep))
        // 운동은 0분인 날을 최저로 치지 않는다
        val exercise = snapshot("exercises", days("2026-10-01", listOf(60.0, 0.0, 90.0, 30.0)))
        assertEquals(DatedValue("2026-10-03", 90.0) to DatedValue("2026-10-04", 30.0), V.calendarExtremes("exercises", exercise))
        // 운동한 날이 하루뿐이면 최고 하나만 둔다
        val single = snapshot("exercises", days("2026-10-01", listOf(0.0, 45.0, 0.0)))
        assertEquals(DatedValue("2026-10-02", 45.0) to null, V.calendarExtremes("exercises", single))
    }

    @Test fun `일별 차트 꼬리표는 네 탭 모두 달고 달력의 체크와 같은 날을 가리킨다`() {
        fun marks(metric: String, s: MetricSnapshot) = V.extremes(metric, V.chartBars(metric, s), s, merged = false).map { it.index to it.highest }
        // 심박에도 단다(전에는 달지 않았다)
        assertEquals(listOf(1 to true, 2 to false), marks("heart_rates", snapshot("heart_rates", days("2026-10-01", listOf(72.0, 78.0, 69.0)))))
        // 운동은 0분인 날이 아니라 가장 짧게 운동한 날에 최저를 단다(전에는 최저가 0이라 최고만 있었다)
        assertEquals(listOf(2 to true, 3 to false), marks("exercises", snapshot("exercises", days("2026-10-01", listOf(60.0, 0.0, 90.0, 30.0)))))
        // 모든 날이 같은 값이면 달지 않고, 꺾은선(산소포화도)에는 달지 않는다
        assertTrue(marks("steps", snapshot("steps", days("2026-10-01", listOf(8000.0, 8000.0, 8000.0)))).isEmpty())
        assertTrue(marks("oxygen_saturations", snapshot("oxygen_saturations", days("2026-10-01", listOf(95.0, 97.0, 96.0)))).isEmpty())
    }

    @Test fun `달력에는 앱 연동 전 날짜를 올리지 않고 연동한 달부터 시작한다`() {
        // 7.31~10.28 가운데 앞 40일이 연동 전(9월 9일에 연동)
        val values = List(90) { if (it < 40) null else 7000.0 + it }
        val s = snapshot("steps", days("2026-07-31", values, reasons = (0 until 40).associateWith { "BEFORE_LINK" }))
        val calendar = V.calendarDays(s)
        assertEquals("2026-09-09", calendar.first().date)
        assertEquals(listOf("2026-09", "2026-10"), V.calendarMonths(calendar))
        // 9월 달력에서 8일까지는 기간 밖 날과 같다(기록 칸이 없다)
        val september = V.calendarWeeks("2026-09", calendar).flatten()
        assertNull(september.first { it.date == "2026-09-08" }.entry)
        assertEquals("2026-09-09", september.first { it.entry != null }.date)
        assertFalse(V.allBeforeLink(s))
        // 물은 기간이 통째로 연동 전이면 달력도 차트도 없고 연동 전이라고 알린다
        assertTrue(V.allBeforeLink(snapshot("steps", days("2026-08-10", listOf(null), reasons = mapOf(0 to "BEFORE_LINK")))))
    }

    @Test fun `달력은 기본 5주이고 6주가 필요한 달만 한 줄 늘린다`() {
        // 2026년 2월은 일요일에 시작해 28일로 딱 4주지만 5주로 그린다. 8월은 토요일에 시작해 6주가 필요하다
        assertEquals(4, V.weeksInMonth("2026-02"))
        assertEquals(5, V.calendarWeeks("2026-02", emptyList()).size)
        assertEquals(6, V.weeksInMonth("2026-08"))
        assertEquals(6, V.calendarWeeks("2026-08", emptyList()).size)
        val october = V.calendarWeeks("2026-10", emptyList())
        assertEquals(5, october.size)
        // 일요일부터 시작하고, 앞뒤 달의 날은 그 달의 날이 아니다
        assertEquals("2026-09-27", october.first().first().date)
        assertFalse(october.first().first().inMonth)
        assertEquals(listOf(0, 1, 2, 3, 4, 5, 6), october.first().map { it.weekday })
        assertEquals("10월", V.monthTitle("2026-10", listOf("2026-09", "2026-10")))
        assertEquals("2027년 1월", V.monthTitle("2027-01", listOf("2026-12", "2027-01")))
    }

    @Test fun `원은 첫 바퀴가 평균까지 두 바퀴째가 기간 최고까지다`() {
        assertEquals(0.5f to 0f, V.donutFill(4000.0, 8000.0, 12000.0))
        assertEquals(1f to 0f, V.donutFill(8000.0, 8000.0, 12000.0))
        assertEquals(1f to 0.5f, V.donutFill(10000.0, 8000.0, 12000.0))
        assertEquals(1f to 1f, V.donutFill(12000.0, 8000.0, 12000.0))
        // 기간 최고가 평균과 같거나 없으면 두 바퀴째를 그리지 않는다
        assertEquals(1f to 0f, V.donutFill(9000.0, 8000.0, null))
        // 운동 0분인 날은 기록 없는 날과 같이 값이 없는 것으로 둔다. 수집 중인 오늘은 0분이어도 값이다
        assertNull(V.shownValue("exercises", EvidenceDay("2026-10-01", 0.0, false, null, false)))
        assertEquals(0.0, V.shownValue("exercises", EvidenceDay("2026-10-01", 0.0, false, null, true)))
        assertEquals(0.0, V.shownValue("steps", EvidenceDay("2026-10-01", 0.0, false, null, false)))
    }

    @Test fun `그날 값은 화면에 보이는 자리로 반올림해 평균과 견준다`() {
        assertEquals("평균보다 450걸음 많아요", V.compareToAverage("steps", 7750.0, 7300.0))
        assertEquals("평균보다 21분 짧아요", V.compareToAverage("sleeps", 441.0, 462.0))
        assertEquals("평균보다 4.1bpm 높아요", V.compareToAverage("heart_rates", 76.0, 71.9))
        // 반올림하면 같은 값으로 보이는 날은 같다고 쓴다
        assertEquals("평균과 같아요", V.compareToAverage("sleeps", 422.4, 421.6))
        assertEquals("10월 27일 화요일, 7시간 2분, 가장 길게 잔 날", V.cellLabel("sleeps", EvidenceDay("2026-10-27", 422.0, false, null, false), "가장 길게 잔 날"))
        assertEquals("10월 28일 수요일, 동기화 전", V.cellLabel("steps", EvidenceDay("2026-10-28", null, true, "UNSYNCED", false), null))
    }

    @Test fun `그날 측정 최저 최고 심박과 깬 시각을 근거에서 읽는다`() {
        val heart = snapshot(
            "heart_rates", days("2026-10-08", listOf(72.0, 75.0)),
            detail = """"measured_min":null,"measured_max":null,"nights":[],"sessions":[],"daily_ranges":[
              {"date":"2026-10-08","min":46,"max":139,"sample_count":730},{"date":"2026-10-09","min":null,"max":null},"모양이 다른 줄"]""",
        )
        assertEquals(DailyRange("2026-10-08", 46.0, 139.0), V.dailyRange("heart_rates", heart, "2026-10-08"))
        // 둘 다 없는 날과 다른 지표는 두지 않는다
        assertNull(V.dailyRange("heart_rates", heart, "2026-10-09"))
        assertNull(V.dailyRange("steps", heart, "2026-10-08"))

        val sleep = snapshot(
            "sleeps", days("2026-10-08", listOf(400.0)),
            detail = """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[
              {"date":"2026-10-08","start_at":"2026-10-07T23:30:00+09:00","end_at":"2026-10-08T07:00:00+09:00","sleep_minutes":400,"deep_minutes":80,"rem_minutes":90,"light_minutes":200,"awake_minutes":30}]""",
        )
        assertEquals("23:30" to "07:00", V.clock(sleep.nights.single().startAt) to V.clock(sleep.nights.single().endAt!!))
    }

    @Test fun `운동 기록이 한 건뿐이면 가장 길게를 붙이지 않고 횟수·칼로리·거리가 모두 비면 묶음을 두지 않는다`() {
        val one = snapshot(
            "exercises", days("2026-10-27", listOf(78.0)),
            detail = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[
              {"date":"2026-10-27","start_at":"2026-10-27T21:27:00+09:00","duration_minutes":78,"type_name":"근력 운동","title":null,"counts_as_exercise":true}]""",
        )
        assertEquals(listOf(false), V.sessionItems(one).map { it.isLongest })

        fun activity(count: String, calories: String, failed: Boolean) = """"measured_min":null,"measured_max":null,"nights":[],"daily_ranges":[],"sessions":[],
          "exercise_activity":{"days":[{"date":"2026-10-28","exercise_count":$count,"exercise_calories_kcal":$calories,"exercise_distance_m":null,
            "is_in_progress":true,"reason":${if (count == "null") "\"UNSYNCED\"" else "null"},"calories_reason":${if (calories == "null") "\"UNSYNCED\"" else "null"},"distance_reason":"UNSYNCED"}],
          "summary":{"exercise_count":{"total":null,"avg":null,"days_with_data":0},"exercise_calories_kcal":{"total":null,"avg":null,"days_with_data":0},
            "exercise_distance_m":{"total":null,"avg":null,"days_with_data":0}},
          "source_statuses":{"exercise_sessions":{"last_sync_status":"success"},"calories_burned":{"last_sync_status":"${if (failed) "fail" else "success"}"},"distances":{"last_sync_status":"success"}}}"""
        val today = days("2026-10-28", listOf(null), reasons = mapOf(0 to "UNSYNCED"), progressLast = true)
        // 셋 다 비면 "-"만 세 칸인 표가 되어 두지 않는다
        assertNull(V.activity(snapshot("exercises", today, activity("null", "null", failed = false))))
        // 수집에 실패한 원천이 있으면 알리려고 둔다. 오늘 값이 하나라도 있으면 둔다
        assertTrue(V.activity(snapshot("exercises", today, activity("null", "null", failed = true)))!!.notes.any { "칼로리" in it })
        assertEquals(listOf("1회", "320kcal", "-"), V.activity(snapshot("exercises", today, activity("1", "320", failed = false)))!!.cards.map { it.value })
    }

    @Test fun `시간대별 걸음은 그날이 든 달을 근거 기간 안에서 한 번에 부른다`() {
        assertEquals("2026-10-01" to "2026-10-28", V.hourlyRange("2026-10-15", "2026-09-09", "2026-10-28"))
        assertEquals("2026-09-09" to "2026-09-30", V.hourlyRange("2026-09-20", "2026-09-09", "2026-10-28"))
        assertEquals(listOf("2026-09-29", "2026-09-30", "2026-10-01"), V.datesBetween("2026-09-29", "2026-10-01"))
        assertEquals(18 to 1500.0, V.hourlyPeak(List(24) { if (it == 18) 1500.0 else 200.0 }))
        // 하루가 모두 0걸음이거나 기록이 없으면 가장 많이 걸은 시간이 없다
        assertNull(V.hourlyPeak(List(24) { 0.0 }))
        assertNull(V.hourlyPeak(null))

        val body = """{"data":{"days":[{"date":"2026-10-01","hourlySteps":[${List(24) { it * 10 }.joinToString(",")}]},{"date":"2026-10-02","hourlySteps":null}]}}"""
        val parsed = HourlyStepsJson.days(body)
        assertEquals(230.0, parsed["2026-10-01"]!![23], 0.001)
        assertNull(parsed["2026-10-02"])
        assertTrue(runCatching { HourlyStepsJson.days("""{"data":{"days":[{"date":"2026-10-01","hourlySteps":[1,2,3]}]}}""") }.isFailure)
    }

    @Test fun `시간대별 걸음은 불러온 달을 쌓아 두어 다시 부르지 않고 실패한 달은 다시 부른다`() = kotlinx.coroutines.runBlocking {
        val calls = mutableListOf<Pair<String, String>>()
        var fail = false
        val loader = HourlyStepsLoader { start, end ->
            calls += start to end
            if (fail) null else mapOf("2026-10-01" to List(24) { 100.0 })
        }
        assertFalse(loader.has("2026-10-01"))
        assertTrue(loader.load("2026-10-01", "2026-10-03"))
        // 응답에 없는 날짜는 기록 없는 날로 채운다
        assertTrue(loader.has("2026-10-02"))
        assertNull(loader.peek("2026-10-02"))
        assertEquals(100.0, loader.peek("2026-10-01")!![0], 0.001)
        // 같은 달 안의 날은 다시 부르지 않는다
        assertTrue(loader.load("2026-10-01", "2026-10-03"))
        assertTrue(loader.load("2026-10-02", "2026-10-02"))
        assertEquals(1, calls.size)
        // 실패한 범위는 쌓아 두지 않아 다음에 다시 부른다
        fail = true
        assertFalse(loader.load("2026-09-09", "2026-09-30"))
        assertFalse(loader.has("2026-09-09"))
        fail = false
        assertTrue(loader.load("2026-09-09", "2026-09-30"))
        assertEquals(3, calls.size)
    }

    @Test fun `날짜별 수면 단계는 밤마다 얕은 잠 렘수면 깊은 잠을 쌓고 단계가 없는 밤은 비운다`() {
        val sleep = snapshot(
            "sleeps", days("2026-10-08", listOf(400.0, 410.0, null)),
            detail = """"measured_min":null,"measured_max":null,"daily_ranges":[],"sessions":[],"nights":[
              {"date":"2026-10-08","start_at":"2026-10-07T23:00:00+09:00","end_at":"2026-10-08T06:00:00+09:00","sleep_minutes":400,"deep_minutes":80,"rem_minutes":90,"light_minutes":200,"awake_minutes":30},
              {"date":"2026-10-09","start_at":"2026-10-08T23:30:00+09:00","end_at":"2026-10-09T06:30:00+09:00","sleep_minutes":410,"deep_minutes":null,"rem_minutes":null,"light_minutes":null,"awake_minutes":null}]""",
        )
        val columns = V.stageColumns(V.dayBars(sleep), sleep.nights)
        assertEquals(3, columns.size)
        // 위에서 아래로 얕은 잠 → 렘수면 → 깊은 잠. 합에는 깬 시간을 넣지 않는다
        assertEquals(listOf(V.Stage.LIGHT to 200.0, V.Stage.REM to 90.0, V.Stage.DEEP to 80.0), columns[0].parts.map { it.stage to it.minutes })
        assertEquals(370.0, columns[0].total, 0.001)
        assertEquals("깊은 잠 1시간 20분, 렘수면 1시간 30분, 얕은 잠 3시간 20분", V.stageText(columns[0]))
        assertEquals("단계 기록 없음", V.stageText(columns[1]))
        assertEquals(0.0, columns[2].total, 0.001)
    }

    @Test fun `달력 상세의 산소포화도 90% 미만 칸은 상태만 적는다`() {
        assertEquals("없음", V.spo2Bare(V.Spo2Low.None))
        assertEquals("측정 없음", V.spo2Bare(V.Spo2Low.Unmeasured))
        assertEquals("2회 (시각 정보 없음)", V.spo2Bare(V.Spo2Low.CountOnly(2)))
        assertEquals("2회 · 02:10, 04:30", V.spo2Bare(V.spo2Low(2, listOf("2026-10-08T04:30:00+09:00", "2026-10-07T17:10:00Z"))))
        // 시각이 네 개 이상이면 접힌 동안 앞의 세 개만 쓴다(화면이 뒤에 "외 N회 보기"를 붙인다)
        val five = V.spo2Low(5, listOf("01:20", "02:10", "03:05", "04:30", "05:15").map { "2026-10-08T$it:00+09:00" })
        assertEquals("5회 · 01:20, 02:10, 03:05", V.spo2Bare(five, expanded = false))
        assertEquals("5회 · 01:20, 02:10, 03:05, 04:30, 05:15", V.spo2Bare(five))
        assertTrue(V.hasLowSpo2(V.Spo2Low.CountOnly(1)))
        assertFalse(V.hasLowSpo2(V.Spo2Low.CountOnly(0)))
        assertFalse(V.hasLowSpo2(V.Spo2Low.None))
    }

    @Test fun `나눠 조회해 합친 심박 근거에서도 그날 측정 최저 최고를 읽는다`() {
        fun ranges(list: List<D>) = list.joinToString(",") { """{"date":"${it.date}","min":50,"max":${130 + it.date.takeLast(2).toInt()},"sample_count":700}""" }
        fun part(start: String, count: Int, position: Int): String {
            val list = days(start, List(count) { 70.0 })
            return metric("heart_rates", list, position = position, detail = """"measured_min":null,"measured_max":null,"nights":[],"sessions":[],"daily_ranges":[${ranges(list)}]""")
        }
        // 42일을 11일과 31일로 나눠 조회한 모양
        val group = V.groups(parse(part("2026-10-01", 11, 0), part("2026-10-12", 31, 1))).single()
        assertTrue(group.merged)
        assertEquals(DailyRange("2026-10-05", 50.0, 135.0), V.dailyRange("heart_rates", group.current.snapshot, "2026-10-05"))
        assertEquals(DailyRange("2026-11-11", 50.0, 141.0), V.dailyRange("heart_rates", group.current.snapshot, "2026-11-11"))
    }
}
