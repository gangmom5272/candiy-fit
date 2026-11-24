package io.candiy.healthconnect.quickstart

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SyncHistoryTest {
    private fun transfer(time: String, type: String, sent: Int, ok: Boolean = true) =
        "$time.100 I/CandiySync(1): remote_transfer_result type=$type sent=$sent dropped=0 ok=$ok cursor=x"

    private fun finished(time: String, result: String, status: String, read: Int, sent: Int, failed: Int) =
        "$time.200 I/CandiySync(1): job_finished result=$result status=$status read=$read saved=$read sent=$sent failed=$failed localSaveFailed=false"

    @Test
    fun `회차마다 지표별 보낸 건수를 묶고 최근 회차부터 돌려준다`() {
        val runs = SyncHistory.parse(
            listOf(
                transfer("11-03 10:50:42", "STEPS", 4),
                transfer("11-03 10:50:42", "HEART_RATE", 2),
                transfer("11-03 10:50:42", "SLEEP", 0),
                finished("11-03 10:50:43", "success", "success", 6, 6, 0),
                transfer("11-03 11:05:10", "STEPS", 0),
                finished("11-03 11:05:11", "success", "no_data", 0, 0, 0),
            ),
        )
        assertEquals(listOf("11-03 11:05", "11-03 10:50"), runs.map { it.at })
        assertEquals(SyncRun.Kind.EMPTY, runs[0].kind)
        assertTrue(runs[0].sentByType.isEmpty())
        assertEquals(mapOf("STEPS" to 4, "HEART_RATE" to 2), runs[1].sentByType)
        assertEquals(SyncRun.Kind.SENT, runs[1].kind)
    }

    @Test
    fun `실패와 일부 실패를 구분한다`() {
        val runs = SyncHistory.parse(
            listOf(
                finished("11-03 09:00:00", "fail", "network", 3, 0, 3),
                finished("11-03 09:15:00", "success", "partial", 5, 3, 2),
            ),
        )
        assertEquals(listOf(SyncRun.Kind.PARTIAL, SyncRun.Kind.FAILED), runs.map { it.kind })
    }

    @Test
    fun `끝나지 않은 회차의 전송 줄은 버리고 개수를 제한한다`() {
        val lines = (0 until 5).map { finished("11-03 0$it:00:00", "success", "success", 1, 1, 0) } +
            transfer("11-03 06:00:00", "STEPS", 9)
        val runs = SyncHistory.parse(lines, limit = 3)
        assertEquals(3, runs.size)
        assertEquals("11-03 04:00", runs.first().at)
    }
}
