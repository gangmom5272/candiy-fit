package io.candiy.healthconnect.quickstart

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * 홈 화면의 지표 한 줄. 건강 수치는 담지 않고 기록 건수·시각·전송 결과만 담는다.
 *
 * @property key SDK 지표 이름(예: STEPS). 전송 로그(TransferLog)와 맞추는 키
 * @property count24h 최근 24시간 안에 시작된 Health Connect 기록 수. 읽지 못했으면 null
 * @property lastMeasured 최근 30일 중 가장 늦은 기록의 측정 시각. 없으면 null
 * @property permitted 이 지표의 읽기 권한이 있는지
 */
data class MetricStatus(
    val key: String,
    val label: String,
    val count24h: Int?,
    val lastMeasured: Instant?,
    val permitted: Boolean,
    val transfer: TypeTransfer?,
)

/** 홈 상단 카드 종류 (와이어프레임 A-04 · A-04-1 ~ 4 · A-03-4) */
enum class HomeState { OK, PREPARING, LATE, PERMISSION, WATCH, FAILED }

object HomeStatus {
    /** 와이어프레임 홈 "오늘 기록"의 네 지표. 나머지 9종은 접는 목록으로 */
    val CORE = listOf("STEPS", "HEART_RATE", "OXYGEN_SATURATION", "SLEEP")

    /** 예정 시각이 이만큼 지나도 다음 알람이 안 잡히면 늦어지는 것으로 본다(절전·Doze 로 몇 분 밀리는 건 흔하다) */
    private const val LATE_MS = 10 * 60 * 1000L

    /**
     * 상단 카드를 고른다. 사용자가 손쓸 수 있는 문제부터 보여 준다.
     * - 동기화 시작 실패(콜백 false) → 권한 꺼짐 → 아직 등록 전 → 예정보다 늦음 → 워치 끊김 → 정상
     * - 시작에 성공했으면 예정 시각을 아직 못 읽었어도(앱을 연 직후 몇 초) 등록 전으로 보지 않는다
     *
     * @param syncOk SDK 시작 콜백 결과. 아직 모르면 null
     * @param nextSyncAt 다음 동기화 알람 시각(ms). 등록돼 있지 않으면 null
     * @param missingPermissions 빠진 권한 수
     * @param watchNearby 가까이 붙은 워치 수. 확인하지 못했으면 null
     */
    fun state(syncOk: Boolean?, nextSyncAt: Long?, missingPermissions: Int, watchNearby: Int?, nowMs: Long): HomeState = when {
        syncOk == false -> HomeState.FAILED
        missingPermissions > 0 -> HomeState.PERMISSION
        nextSyncAt == null && syncOk != true -> HomeState.PREPARING
        nextSyncAt == null -> if (watchNearby == 0) HomeState.WATCH else HomeState.OK
        nowMs - nextSyncAt > LATE_MS -> HomeState.LATE
        watchNearby == 0 -> HomeState.WATCH
        else -> HomeState.OK
    }

    /** 지표 카드 아래 전송 줄. (문구, 종류) — 종류는 ok · idle · bad */
    fun transferLine(m: MetricStatus, today: LocalDate): Pair<String, String> {
        val t = m.transfer
        return when {
            !m.permitted -> "권한 꺼짐 · 보내지 않아요" to "bad"
            t == null -> "아직 보낸 적 없음" to "idle"
            !t.ok -> "전송 실패 · ${logTime(t.lastAt, today)}" to "bad"
            t.lastSentAt != null -> "보냄 ${logTime(t.lastSentAt, today)} · ${t.lastSentCount}건" to "ok"
            else -> "새 기록 없음 · 확인 ${logTime(t.lastAt, today)}" to "idle"
        }
    }

    /** 워치에 보내는 네 지표의 이름. 홈 지표 카드와 같다 */
    val CORE_LABELS = mapOf("STEPS" to "걸음", "HEART_RATE" to "심박", "OXYGEN_SATURATION" to "산소포화도", "SLEEP" to "수면")

    /**
     * 워치 화면용 짧은 전송 줄. [transferLine]과 같은 판단이고 문구만 줄였다(둥근 작은 화면에 한 줄로).
     * 건수는 빼고 상태와 시각만 보낸다. (문구, 종류) — 종류는 ok · idle · bad
     */
    fun watchLine(m: MetricStatus, today: LocalDate): Pair<String, String> {
        val t = m.transfer
        return when {
            !m.permitted -> "권한 꺼짐" to "bad"
            t == null -> "보낸 적 없음" to "idle"
            !t.ok -> "실패 ${logTime(t.lastAt, today)}" to "bad"
            t.lastSentAt != null -> "보냄 ${logTime(t.lastSentAt, today)}" to "ok"
            else -> "새 기록 없음" to "idle"
        }
    }

    private val HM = DateTimeFormatter.ofPattern("HH:mm")
    private val MD_HM = DateTimeFormatter.ofPattern("M/d HH:mm")

    /** 측정·예정 시각. 오늘이면 "HH:mm", 아니면 "M/d HH:mm" */
    fun clock(at: Instant, today: LocalDate, zone: ZoneId = ZoneId.systemDefault()): String {
        val t = LocalDateTime.ofInstant(at, zone)
        return t.format(if (t.toLocalDate() == today) HM else MD_HM)
    }

    /**
     * 설정 화면 워치 줄. 연결 여부와 워치 자동 수집분을 마지막으로 받은 시각을 함께 쓴다.
     * 연결은 돼 있는데 오래 받지 못했는지(워치 앱이 멈췄는지)를 폰에서 알 수 있게 한다. 두 번째 값은 주의 표시 여부
     */
    fun watchSetting(nearby: Int?, lastReceivedMs: Long?, today: LocalDate, zone: ZoneId = ZoneId.systemDefault()): Pair<String, Boolean> {
        val received = lastReceivedMs?.let { "마지막으로 받은 기록 ${clock(Instant.ofEpochMilli(it), today, zone)}" }
        return when {
            nearby == null -> "확인하는 중" to false
            nearby == 0 -> (received?.let { "연결 안 됨 · $it" } ?: "연결 안 됨 · 다시 연결되면 모아 둔 기록을 보내요") to true
            else -> "연결됨 · ${received ?: "아직 받은 기록 없음"}" to false
        }
    }

    /** logcat 시각 "MM-dd HH:mm" → 오늘이면 "HH:mm", 아니면 "M/d HH:mm". 형식이 다르면 그대로 */
    fun logTime(mmddHhmm: String, today: LocalDate): String {
        val m = Regex("""(\d{2})-(\d{2}) (\d{2}:\d{2})""").find(mmddHhmm) ?: return mmddHhmm
        val (mm, dd, hm) = m.destructured
        return if (mm.toInt() == today.monthValue && dd.toInt() == today.dayOfMonth) hm else "${mm.toInt()}/${dd.toInt()} $hm"
    }

    /** 홈 날짜 줄 "11월 2일 일요일" */
    fun dateLine(today: LocalDate): String =
        today.format(DateTimeFormatter.ofPattern("M월 d일 EEEE", Locale.KOREAN))
}
