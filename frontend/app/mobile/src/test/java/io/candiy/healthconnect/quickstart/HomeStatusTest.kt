package io.candiy.healthconnect.quickstart

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDate

/** 값은 모두 지어낸 것이다. 실제 기록을 넣지 않는다. */
class HomeStatusTest {
    private val now = 1_800_000_000_000L
    private val today = LocalDate.of(2026, 11, 2)

    private fun line(at: String, type: String, sent: Int, ok: Boolean) =
        "$at:13.000 I/CandiySync( 1234): remote_transfer_result type=$type sent=$sent dropped=0 ok=$ok cursor=42"

    @Test
    fun `지표별 마지막 전송과 마지막으로 새 기록을 보낸 시각을 따로 남긴다`() {
        val map = TransferLog.parse(listOf(
            line("11-02 10:12", "STEPS", 3, true),
            line("11-02 10:27", "STEPS", 0, true),
            line("11-02 10:27", "SLEEP", 0, true),
            "11-02 10:28:00.000 I/CandiySync( 1234): job_finished result=success sent=3 failed=0",
        ))
        assertEquals(TypeTransfer("11-02 10:27", true, "11-02 10:12", 3), map["STEPS"])
        assertEquals(TypeTransfer("11-02 10:27", true, null, 0), map["SLEEP"])
        assertNull(map["HEART_RATE"])
    }

    @Test
    fun `소문자 지표 이름도 같은 키로 모은다`() {
        val map = TransferLog.parse(listOf(line("11-02 10:12", "steps", 2, false)))
        assertEquals(false, map["STEPS"]?.ok)
    }

    @Test
    fun `상단 카드는 사용자가 손쓸 수 있는 문제부터 고른다`() {
        assertEquals(HomeState.FAILED, HomeStatus.state(false, null, 3, 0, now))
        assertEquals(HomeState.PERMISSION, HomeStatus.state(true, now, 1, 0, now))
        assertEquals(HomeState.PREPARING, HomeStatus.state(null, null, 0, 1, now))
        // 시작은 성공했는데 예정 시각을 아직 못 읽은 순간은 정상으로 본다
        assertEquals(HomeState.OK, HomeStatus.state(true, null, 0, 1, now))
        assertEquals(HomeState.LATE, HomeStatus.state(true, now - 11 * 60_000, 0, 1, now))
        assertEquals(HomeState.WATCH, HomeStatus.state(true, now - 5 * 60_000, 0, 0, now))
        assertEquals(HomeState.OK, HomeStatus.state(true, now + 60_000, 0, 1, now))
        // 워치 연결을 확인하지 못했으면(null) 끊김으로 보지 않는다
        assertEquals(HomeState.OK, HomeStatus.state(null, now + 60_000, 0, null, now))
    }

    @Test
    fun `전송 줄은 권한 · 실패 · 보냄 · 새 기록 없음을 구분한다`() {
        fun m(permitted: Boolean = true, t: TypeTransfer? = null) = MetricStatus("STEPS", "걸음 수", 1, null, permitted, t)
        assertEquals("권한 꺼짐 · 보내지 않아요" to "bad", HomeStatus.transferLine(m(permitted = false), today))
        assertEquals("아직 보낸 적 없음" to "idle", HomeStatus.transferLine(m(), today))
        assertEquals("전송 실패 · 10:42" to "bad",
            HomeStatus.transferLine(m(t = TypeTransfer("11-02 10:42", false, null, 0)), today))
        assertEquals("보냄 10:12 · 3건" to "ok",
            HomeStatus.transferLine(m(t = TypeTransfer("11-02 10:27", true, "11-02 10:12", 3)), today))
        assertEquals("새 기록 없음 · 확인 11/1 23:57" to "idle",
            HomeStatus.transferLine(m(t = TypeTransfer("11-01 23:57", true, null, 0)), today))
    }

    @Test
    fun `워치 줄은 같은 판단을 건수 없이 짧게 쓴다`() {
        fun m(permitted: Boolean = true, t: TypeTransfer? = null) = MetricStatus("STEPS", "걸음", 1, null, permitted, t)
        assertEquals("권한 꺼짐" to "bad", HomeStatus.watchLine(m(permitted = false), today))
        assertEquals("보낸 적 없음" to "idle", HomeStatus.watchLine(m(), today))
        assertEquals("실패 10:42" to "bad", HomeStatus.watchLine(m(t = TypeTransfer("11-02 10:42", false, null, 0)), today))
        assertEquals("보냄 11/1 23:50" to "ok",
            HomeStatus.watchLine(m(t = TypeTransfer("11-02 10:27", true, "11-01 23:50", 3)), today))
        assertEquals("새 기록 없음" to "idle", HomeStatus.watchLine(m(t = TypeTransfer("11-02 10:27", true, null, 0)), today))
        assertEquals(HomeStatus.CORE, HomeStatus.CORE_LABELS.keys.toList())
    }

    @Test
    fun `설정 워치 줄은 연결 여부와 마지막으로 받은 시각을 함께 쓴다`() {
        val zone = java.time.ZoneId.of("Asia/Seoul")
        val at = today.atTime(14, 5).atZone(zone).toInstant().toEpochMilli()
        assertEquals("확인하는 중" to false, HomeStatus.watchSetting(null, at, today, zone))
        assertEquals("연결됨 · 마지막으로 받은 기록 14:05" to false, HomeStatus.watchSetting(1, at, today, zone))
        assertEquals("연결됨 · 아직 받은 기록 없음" to false, HomeStatus.watchSetting(1, null, today, zone))
        assertEquals("연결 안 됨 · 마지막으로 받은 기록 14:05" to true, HomeStatus.watchSetting(0, at, today, zone))
        assertEquals("연결 안 됨 · 다시 연결되면 모아 둔 기록을 보내요" to true, HomeStatus.watchSetting(0, null, today, zone))
    }

    @Test
    fun `날짜 줄은 한국어 요일까지 쓴다`() {
        assertEquals("11월 2일 월요일", HomeStatus.dateLine(today))
    }
}
