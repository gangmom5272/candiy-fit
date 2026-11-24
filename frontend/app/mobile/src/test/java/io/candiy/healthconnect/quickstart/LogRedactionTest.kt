package io.candiy.healthconnect.quickstart

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/** 값은 모두 지어낸 것이다. 실제 기록을 넣지 않는다. */
class LogRedactionTest {
    private val head = "10-28 14:00:00.000 E/CandiySync( 1234): "
    private val mask = LogRedaction.MASK

    private fun sdk(body: String) = LogRedaction.sdkLine(head + body)

    @Test
    fun `건수·상태만 있는 이벤트 줄은 그대로 둔다`() {
        val body = "job_finished result=success status=success read=3 saved=3 sent=3 failed=0 totalElapsedMs=12"
        assertEquals(head + body, sdk(body))
        assertEquals(head + "http method=POST path=/api/steps reqMb=0.01 code=200 elapsedMs=40",
            sdk("http method=POST path=/api/steps reqMb=0.01 code=200 elapsedMs=40"))
        assertEquals(head + "passive_inserted records=2 types=[ExerciseSessionRecord, HeartRateRecord]",
            sdk("passive_inserted records=2 types=[ExerciseSessionRecord, HeartRateRecord]"))
    }

    @Test
    fun `오류 본문은 안에 키=값이 섞여도 줄 끝까지 가린다`() {
        val line = sdk("""remote_transfer_batch type=steps ok=false httpCode=422 error={"message":"bad value=98765 at row"}""")
        assertEquals(head + "remote_transfer_batch type=steps ok=false httpCode=422 error=$mask", line)
    }

    @Test
    fun `여러 줄 오류의 둘째 줄부터는 통째로 가린다`() {
        assertEquals(head + mask, sdk("""  "heart_rate": 98765,"""))
        assertEquals(head + mask, sdk("""}"""))
        assertEquals(head + mask, sdk("steps heart_rate=98765"))
        assertEquals(head + mask, sdk("bad value 98765"))
    }

    @Test
    fun `예외는 종류와 위치만 남긴다`() {
        assertEquals(head + "java.io.IOException: $mask", sdk("java.io.IOException: unexpected value 98765"))
        assertEquals(head + "Caused by: java.lang.IllegalStateException: $mask",
            sdk("Caused by: java.lang.IllegalStateException: value=98765"))
        assertEquals(head + "java.net.SocketTimeoutException", sdk("java.net.SocketTimeoutException"))
        val frame = "\tat com.candiy.candiyhc.HealthDataSyncEngine.run(HealthDataSyncEngine.kt:312)"
        assertEquals(head + frame, sdk(frame))
        assertEquals(head + "\tat kotlin.coroutines.Foo.resume(Unknown Source:8)", sdk("\tat kotlin.coroutines.Foo.resume(Unknown Source:8)"))
    }

    @Test
    fun `형식을 모르는 줄은 값이 남지 않는다`() {
        for (body in listOf("Starting real-time data streaming for type: 98765", "{\"value\": 98765}")) {
            assertFalse(sdk(body).contains("98765"))
        }
        assertEquals(mask, LogRedaction.sdkLine("logcat 머리가 없는 줄 98765"))
    }
}
