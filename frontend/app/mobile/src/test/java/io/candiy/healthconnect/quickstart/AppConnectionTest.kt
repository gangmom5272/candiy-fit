package io.candiy.healthconnect.quickstart

import io.candiy.healthconnect.quickstart.auth.AuthApi
import io.candiy.healthconnect.quickstart.auth.AuthJson
import io.candiy.healthconnect.quickstart.auth.AuthSession
import io.candiy.healthconnect.quickstart.auth.Fixtures
import io.candiy.healthconnect.quickstart.auth.HttpReply
import io.candiy.healthconnect.quickstart.auth.HttpTransport
import io.candiy.healthconnect.quickstart.auth.Session
import io.candiy.healthconnect.quickstart.auth.SessionStorage
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * 앱 연동 완료 보고(S15P31A403-366). 서버 대신 정해 둔 답을 돌려주는 가짜 전송을 쓴다. 값은 모두 지어낸 것이다.
 * 백엔드 가이드(backend/docs/app-connection-363.md)의 결과 표와 같은 상황을 하나씩 확인한다.
 */
class AppConnectionTest {
    /** 완료 보고 PUT 의 가짜. 받은 토큰을 순서대로 남긴다 */
    private class FakePut(vararg replies: HttpReply) : AppConnectionTransport {
        private val queue = ArrayDeque(replies.toList())
        val tokens = mutableListOf<String>()
        override fun put(accessToken: String): HttpReply {
            tokens += accessToken
            return queue.removeFirst()
        }
    }

    /** 갱신(POST /auth/refresh)의 가짜 */
    private class FakeAuthHttp(vararg replies: HttpReply) : HttpTransport {
        private val queue = ArrayDeque(replies.toList())
        var calls = 0
        override fun postJson(path: String, body: JSONObject): HttpReply {
            calls++
            return queue.removeFirst()
        }
    }

    private class MemoryStorage(var value: Session?) : SessionStorage {
        override fun load() = value
        override fun save(session: Session) { value = session }
        override fun clear() { value = null }
    }

    private val now = Instant.parse("2026-10-29T01:00:00Z")
    private fun stored(access: String = "access-1", expiresAt: String = "2026-10-29T01:15:00Z") =
        AuthJson.decodeSession(JSONObject(Fixtures.session(access, "refresh-1", expiresAt)).getJSONObject("data").toString())

    private val ok = HttpReply.Received(200, """{"data":{"status":"CONNECTED","connectedAt":"2026-11-11T07:00:00Z"}}""")
    private fun error(status: Int, code: String, retryable: Boolean? = null) = HttpReply.Received(
        status,
        """{"error":{"code":"$code","message":"합성 오류"${retryable?.let { ""","retryable":$it""" } ?: ""}}}""",
    )

    private class Setup(val reporter: AppConnectionReporter, val put: FakePut, val authHttp: FakeAuthHttp, val storage: MemoryStorage, val waits: MutableList<Long>)

    private fun setup(put: FakePut, authHttp: FakeAuthHttp = FakeAuthHttp(), session: Session? = stored()): Setup {
        val storage = MemoryStorage(session)
        val waits = mutableListOf<Long>()
        val auth = AuthSession(AuthApi(authHttp), storage, clock = { now })
        return Setup(AppConnectionReporter(auth, put, retryDelaysMs = listOf(10L, 20L), wait = { waits += it }), put, authHttp, storage, waits)
    }

    @Test fun `읽기 권한이 하나라도 있어야 연동으로 친다`() {
        val read = setOf("read.STEPS", "read.SLEEP")
        assertTrue(AppConnection.hasReadPermission(setOf("read.SLEEP", "write.STEPS"), read))
        // 쓰기·백그라운드·과거 기록 권한만으로는 건강 기록을 읽을 수 없다
        assertFalse(AppConnection.hasReadPermission(setOf("write.STEPS", "background", "history"), read))
        assertFalse(AppConnection.hasReadPermission(emptySet(), read))
    }

    @Test fun `동기화에 등록한 사용자 ID 가 로그인한 회원의 것일 때만 같다`() {
        assertTrue(AppConnection.sameMember(stored(), Fixtures.USER_ID))
        assertFalse(AppConnection.sameMember(stored(), "다른-회원"))
        assertFalse(AppConnection.sameMember(null, Fixtures.USER_ID))
        assertFalse(AppConnection.sameMember(stored(), null))
    }

    @Test fun `200 이면 완료이고 서비스 액세스 토큰만 보낸다`() = runBlocking {
        val s = setup(FakePut(ok))
        assertEquals(ReportResult.Reported, s.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(listOf("access-1"), s.put.tokens)
        assertEquals(0, s.authHttp.calls)
    }

    @Test fun `로그인한 회원이 다르거나 시도가 취소됐으면 보내지 않는다`() = runBlocking {
        val other = setup(FakePut())
        assertEquals(ReportResult.Stale, other.reporter.report("다른-회원") { true })
        assertTrue(other.put.tokens.isEmpty())

        val cancelled = setup(FakePut())
        assertEquals(ReportResult.Stale, cancelled.reporter.report(Fixtures.USER_ID) { false })
        assertTrue(cancelled.put.tokens.isEmpty())
    }

    @Test fun `로그인 상태가 없으면 로그인으로 보낸다`() = runBlocking {
        val s = setup(FakePut(), session = null)
        assertEquals(ReportResult.SignedOut, s.reporter.report(Fixtures.USER_ID) { true })
    }

    @Test fun `액세스 만료는 한 번만 갱신해 새 토큰으로 다시 보낸다`() = runBlocking {
        val refreshed = HttpReply.Received(200, Fixtures.session("access-2", "refresh-2"))
        val s = setup(FakePut(error(401, "AUTH_EXPIRED"), ok), FakeAuthHttp(refreshed))
        assertEquals(ReportResult.Reported, s.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(listOf("access-1", "access-2"), s.put.tokens)
        assertEquals(1, s.authHttp.calls)

        // 갱신한 뒤에도 만료라고 하면 다시 갱신하지 않고 멈춘다
        val again = setup(FakePut(error(401, "AUTH_EXPIRED"), error(401, "AUTH_EXPIRED")), FakeAuthHttp(refreshed))
        assertEquals(ReportResult.Pending("AUTH_EXPIRED"), again.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(1, again.authHttp.calls)
    }

    @Test fun `갱신이 거절되면 로그인으로 보내고, 갱신하는 사이 취소되면 버린다`() = runBlocking {
        val rejected = setup(FakePut(error(401, "AUTH_EXPIRED")), FakeAuthHttp(error(401, "AUTH_REQUIRED")))
        assertEquals(ReportResult.SignedOut, rejected.reporter.report(Fixtures.USER_ID) { true })
        assertNull(rejected.storage.value)

        var valid = true
        val refreshed = HttpReply.Received(200, Fixtures.session("access-2", "refresh-2"))
        val s = setup(FakePut(error(401, "AUTH_EXPIRED")), FakeAuthHttp(refreshed))
        // 첫 전송 뒤 계정을 바꾼 상황: 두 번째 확인에서 걸러져 새 토큰으로는 보내지 않는다
        val result = s.reporter.report(Fixtures.USER_ID) { valid.also { valid = false } }
        assertEquals(ReportResult.Stale, result)
        assertEquals(listOf("access-1"), s.put.tokens)
    }

    @Test fun `곧 끝나는 액세스는 미리 갱신한 토큰으로 보낸다`() = runBlocking {
        val refreshed = HttpReply.Received(200, Fixtures.session("access-2", "refresh-2"))
        val s = setup(FakePut(ok), FakeAuthHttp(refreshed), session = stored(expiresAt = "2026-10-29T01:00:30Z"))
        assertEquals(ReportResult.Reported, s.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(listOf("access-2"), s.put.tokens)
    }

    @Test fun `503 retryable 과 연결 실패는 정해 둔 횟수만 다시 보낸다`() = runBlocking {
        val recovered = setup(FakePut(error(503, "APP_CONNECTION_UNAVAILABLE", true), HttpReply.NoResponse, ok))
        assertEquals(ReportResult.Reported, recovered.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(listOf(10L, 20L), recovered.waits)

        val unavailable = setup(FakePut(*Array(3) { error(503, "APP_CONNECTION_UNAVAILABLE", true) }))
        assertEquals(ReportResult.Pending("APP_CONNECTION_UNAVAILABLE"), unavailable.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(3, unavailable.put.tokens.size)

        val offline = setup(FakePut(HttpReply.NotSent, HttpReply.NotSent, HttpReply.NotSent))
        assertEquals(ReportResult.Pending(AuthSession.NETWORK), offline.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(3, offline.put.tokens.size)
    }

    @Test fun `422·500·AUTH_REQUIRED 는 되풀이하지 않고 알린다`() = runBlocking {
        for ((status, code) in listOf(422 to "INVALID_REQUEST", 500 to "INTERNAL_ERROR", 401 to "AUTH_REQUIRED")) {
            val s = setup(FakePut(error(status, code)))
            assertEquals(ReportResult.Pending(code), s.reporter.report(Fixtures.USER_ID) { true })
            assertEquals(1, s.put.tokens.size)
            assertTrue(s.waits.isEmpty())
        }
        // retryable 이 아닌 503 도 자동으로 다시 보내지 않는다
        val s = setup(FakePut(error(503, "APP_CONNECTION_UNAVAILABLE", false)))
        assertEquals(ReportResult.Pending("APP_CONNECTION_UNAVAILABLE"), s.reporter.report(Fixtures.USER_ID) { true })
        assertEquals(1, s.put.tokens.size)
    }

    @Test fun `연결 완료 화면은 서버가 받은 뒤에만 완료로 보인다`() {
        assertEquals(ConnectedPhase.PREPARING, ConnectedPhase.of(null, ConnectionReport.IDLE))
        // 동기화 등록은 성공했지만 아직 서버에 알리는 중
        assertEquals(ConnectedPhase.PREPARING, ConnectedPhase.of(true, ConnectionReport.IDLE))
        assertEquals(ConnectedPhase.PREPARING, ConnectedPhase.of(true, ConnectionReport.SENDING))
        assertEquals(ConnectedPhase.DONE, ConnectedPhase.of(true, ConnectionReport.REPORTED))
        assertEquals(ConnectedPhase.REPORT_PENDING, ConnectedPhase.of(true, ConnectionReport.PENDING))
        // 등록 실패가 먼저다. 보고 상태와 관계없이 등록부터 다시 한다
        assertEquals(ConnectedPhase.SYNC_FAILED, ConnectedPhase.of(false, ConnectionReport.REPORTED))
    }
}
