package io.candiy.healthconnect.quickstart

import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.OxygenSaturationRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.health.connect.client.units.Percentage
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZoneOffset
import kotlin.reflect.KClass

/**
 * 연동 테스트용 목업 데이터. 최근 7일치 걸음 수·심박수·산소포화도·수면을 Health Connect 에 쓴다.
 *
 * 기록마다 clientRecordId 를 붙여서 다시 실행해도 같은 기록이 덮어써질 뿐 늘어나지 않는다.
 * 수면은 4일 전 밤을 일부러 비워 결측 처리를 같이 확인한다.
 * local.properties 의 candiy.mockData=true 일 때만 MainActivity 가 부른다.
 */
object MockHealthData {

    private val zone: ZoneId = ZoneId.of("Asia/Seoul")
    private val offset: ZoneOffset = zone.rules.getOffset(Instant.now())

    // index 0 = 7일 전, 6 = 어제
    private val STEPS = intArrayOf(7200, 5400, 8100, 6900, 4800, 7600, 7880)
    private val REST_HR = intArrayOf(63, 65, 64, 62, 66, 62, 64)
    private val SPO2_MIN = doubleArrayOf(97.0, 96.0, 98.0, 97.0, 93.0, 96.0, 97.0)
    private val SLEEP_HOURS = doubleArrayOf(6.5, 5.8, 7.1, Double.NaN, 6.0, 5.5, 6.3) // NaN = 기록 없음

    /** 기대 개수. 서버 조회 결과와 비교할 때 쓴다. */
    const val EXPECTED_STEPS = 7 * 3
    const val EXPECTED_HEART_RATE = 7 * 14
    const val EXPECTED_SPO2 = 7 * 3
    const val EXPECTED_SLEEP = 6

    private fun day(i: Int): LocalDate = LocalDate.now(zone).minusDays((7 - i).toLong())
    private fun at(date: LocalDate, h: Int, m: Int = 0): Instant = date.atTime(LocalTime.of(h, m)).atZone(zone).toInstant()
    private fun meta(id: String) = Metadata.manualEntry(clientRecordId = "candiy-mock-$id")

    fun build(): List<Record> {
        val records = mutableListOf<Record>()
        for (i in 0 until 7) {
            val d = day(i)

            // 걸음 수: 오전·점심·저녁 3건, 합계가 STEPS[i]
            val total = STEPS[i]
            val parts = listOf(total * 3 / 10, total * 3 / 10, total - 2 * (total * 3 / 10))
            listOf(9 to 0, 12 to 30, 18 to 0).forEachIndexed { k, (h, m) ->
                records += StepsRecord(
                    startTime = at(d, h, m), startZoneOffset = offset,
                    endTime = at(d, h, m).plus(Duration.ofHours(1)), endZoneOffset = offset,
                    count = parts[k].toLong(),
                    metadata = meta("steps-$d-$k"),
                )
            }

            // 심박수: 08~21시 시간당 1건, 10분 간격 샘플 6개. 18시는 운동 구간이라 높게
            for (h in 8..21) {
                val start = at(d, h)
                val samples = (0 until 6).map { k ->
                    val bpm = REST_HR[i] + (if (h == 18) 45 else (h % 3) * 3) + k
                    HeartRateRecord.Sample(time = start.plus(Duration.ofMinutes(10L * k)), beatsPerMinute = bpm.toLong())
                }
                records += HeartRateRecord(
                    startTime = start, startZoneOffset = offset,
                    endTime = start.plus(Duration.ofMinutes(59)), endZoneOffset = offset,
                    samples = samples,
                    metadata = meta("hr-$d-$h"),
                )
            }

            // 산소포화도: 그날 새벽 2·4·6시 3건, 가장 낮은 값이 SPO2_MIN[i]
            listOf(2 to 1.0, 4 to 0.0, 6 to 1.0).forEach { (h, plus) ->
                records += OxygenSaturationRecord(
                    time = at(d, h), zoneOffset = offset,
                    percentage = Percentage(SPO2_MIN[i] + plus),
                    metadata = meta("spo2-$d-$h"),
                )
            }

            // 수면: 전날 밤 ~ 그날 06:30, 단계 6개. 기록 없는 밤은 건너뛴다
            val hours = SLEEP_HOURS[i]
            if (!hours.isNaN()) {
                val end = at(d, 6, 30)
                val asleep = Duration.ofMinutes((hours * 60).toLong())
                val start = end.minus(asleep).minus(Duration.ofMinutes(10))
                val plan = listOf(
                    SleepSessionRecord.STAGE_TYPE_AWAKE to 0.0,
                    SleepSessionRecord.STAGE_TYPE_LIGHT to 0.35,
                    SleepSessionRecord.STAGE_TYPE_DEEP to 0.20,
                    SleepSessionRecord.STAGE_TYPE_REM to 0.15,
                    SleepSessionRecord.STAGE_TYPE_LIGHT to 0.15,
                    SleepSessionRecord.STAGE_TYPE_REM to 0.15,
                )
                var t = start
                val stages = plan.mapIndexed { k, (type, ratio) ->
                    val len = if (k == 0) Duration.ofMinutes(10) else Duration.ofMinutes((asleep.toMinutes() * ratio).toLong())
                    val stageEnd = if (k == plan.lastIndex) end else t.plus(len)
                    SleepSessionRecord.Stage(startTime = t, endTime = stageEnd, stage = type).also { t = stageEnd }
                }
                records += SleepSessionRecord(
                    startTime = start, startZoneOffset = offset,
                    endTime = end, endZoneOffset = offset,
                    title = "목업 수면", notes = null,
                    stages = stages,
                    metadata = meta("sleep-$d"),
                )
            }
        }
        return records
    }

    /**
     * 늦게 도착한 운동 기록. 워치를 휴대폰 없이 차고 운동한 뒤 몇 시간 지나 다시 연결한 상황을 흉내 낸다.
     * 측정 시각은 [hoursAgo]시간 전 30분이지만 Health Connect 에는 지금 들어간다.
     * 실행할 때마다 새 ID 를 붙여 매번 새 기록이 된다. 돌려주는 값은 화면·로그에 쓸 설명.
     */
    suspend fun writeLateWorkout(client: HealthConnectClient, hoursAgo: Long = 3): String {
        val start = Instant.now().minus(Duration.ofHours(hoursAgo)).let { it.minusSeconds(it.epochSecond % 60) }
        val end = start.plus(Duration.ofMinutes(30))
        val id = "late-${start.epochSecond}"
        val steps = 3200L
        val samples = (0 until 6).map { k ->
            HeartRateRecord.Sample(time = start.plus(Duration.ofMinutes(5L * k)), beatsPerMinute = 118L + k * 4)
        }
        client.insertRecords(listOf(
            StepsRecord(
                startTime = start, startZoneOffset = offset, endTime = end, endZoneOffset = offset,
                count = steps, metadata = meta("$id-steps"),
            ),
            HeartRateRecord(
                startTime = start, startZoneOffset = offset, endTime = end, endZoneOffset = offset,
                samples = samples, metadata = meta("$id-hr"),
            ),
        ))
        val t = start.atZone(zone).toLocalTime().toString().take(5)
        return "늦게 도착한 운동 · 측정 $t~${end.atZone(zone).toLocalTime().toString().take(5)} (${hoursAgo}시간 전) · 걸음 ${steps}보 + 심박 6개 · 지금 Health Connect 에 기록"
    }

    /** 목업 기록을 쓰고 쓴 개수를 돌려준다. */
    suspend fun write(client: HealthConnectClient): Int {
        val records = build()
        records.chunked(500).forEach { client.insertRecords(it) }
        return records.size
    }

    /** 우리 앱이 쓴 기록만 다시 읽어 종류별 개수를 문장으로 돌려준다. */
    suspend fun readBack(client: HealthConnectClient, packageName: String): String {
        val range = TimeRangeFilter.between(at(day(0), 0).minus(Duration.ofDays(1)), Instant.now())
        suspend fun <T : Record> count(type: KClass<T>): List<T> {
            val out = mutableListOf<T>()
            var token: String? = null
            do {
                val res = client.readRecords(ReadRecordsRequest(type, range, pageToken = token))
                out += res.records.filter { it.metadata.dataOrigin.packageName == packageName }
                token = res.pageToken
            } while (token != null)
            return out
        }
        val steps = count(StepsRecord::class)
        val hr = count(HeartRateRecord::class)
        val spo2 = count(OxygenSaturationRecord::class)
        val sleep = count(SleepSessionRecord::class)
        return buildString {
            appendLine("걸음 ${steps.size}/${EXPECTED_STEPS}건 · 합계 ${steps.sumOf { it.count }}보")
            appendLine("심박 ${hr.size}/${EXPECTED_HEART_RATE}건 · 샘플 ${hr.sumOf { it.samples.size }}개")
            appendLine("산소포화도 ${spo2.size}/${EXPECTED_SPO2}건 · 최저 ${spo2.minOfOrNull { it.percentage.value } ?: "-"}%")
            append("수면 ${sleep.size}/${EXPECTED_SLEEP}건 · 단계 ${sleep.sumOf { it.stages.size }}개")
        }
    }
}
