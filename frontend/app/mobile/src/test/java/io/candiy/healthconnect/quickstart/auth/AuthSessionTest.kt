package io.candiy.healthconnect.quickstart.auth

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.util.Collections

/** 서버 대신 정해 둔 답을 돌려주는 가짜 HTTP. 값은 모두 지어낸 것이다 */
class AuthSessionTest {

    private class FakeHttp(vararg replies: HttpReply) : HttpTransport {
        private val queue = ArrayDeque(replies.toList())
        val calls: MutableList<Pair<String, JSONObject>> = Collections.synchronizedList(mutableListOf())

        override fun postJson(path: String, body: JSONObject): HttpReply {
            calls += path to body
            Thread.sleep(50) // 두 갱신이 겹치는 상황을 만든다
            return synchronized(queue) { queue.removeFirst() }
        }
    }

    private class MemoryStorage(var value: Session? = null) : SessionStorage {
        override fun load() = value
        override fun save(session: Session) { value = session }
        override fun clear() { value = null }
    }

    private val now = Instant.parse("2026-10-29T01:00:00Z")
    private val stored = AuthJson.decodeSession(
        JSONObject(Fixtures.session("access-1", "refresh-1", "2026-10-29T01:00:30Z")).getJSONObject("data").toString()
    )

    private fun session(http: FakeHttp, storage: MemoryStorage) =
        AuthSession(AuthApi(http), storage, clock = { now })

    private fun ok(body: String) = HttpReply.Received(200, body)

    @Test
    fun `기존 회원 로그인은 저장하고, 신규 회원은 저장하지 않는다`() = runBlocking {
        val storage = MemoryStorage()
        session(FakeHttp(ok(Fixtures.loggedIn())), storage).login(SocialType.KAKAO, "id", "challenge")
        assertEquals("refresh-1", storage.value?.refreshToken)

        val empty = MemoryStorage()
        val signup = ok("""{"data":{"status":"SIGNUP_REQUIRED","signupToken":"s","signupTokenExpiresAt":"2026-10-29T01:10:00Z","socialType":"KAKAO"}}""")
        session(FakeHttp(signup), empty).login(SocialType.KAKAO, "id", "challenge")
        assertNull(empty.value)
    }

    @Test
    fun `가입에 성공하면 저장한다`() = runBlocking {
        val storage = MemoryStorage()
        val result = session(FakeHttp(HttpReply.Received(201, Fixtures.session("access-2", "refresh-2"))), storage)
            .signup("signup", SignupProfile("핏디", 2000, "FEMALE", 165.25, 55.0))
        assertTrue(result is ApiResult.Ok)
        assertEquals("refresh-2", storage.value?.refreshToken)
    }

    @Test
    fun `갱신이 겹쳐도 서버에는 한 번만 보내고 둘 다 새 토큰을 받는다`() = runBlocking {
        val storage = MemoryStorage(stored)
        val http = FakeHttp(ok(Fixtures.session("access-2", "refresh-2")))
        val auth = session(http, storage)

        val outcomes = listOf(async { auth.refresh() }, async { auth.refresh() }).awaitAll()

        assertEquals(1, http.calls.size)
        assertEquals("refresh-1", http.calls.single().second.getString("refreshToken"))
        outcomes.forEach { assertEquals("refresh-2", (it as RefreshOutcome.Refreshed).session.refreshToken) }
        assertEquals("refresh-2", storage.value?.refreshToken)
    }

    @Test
    fun `갱신 중에 호출한 쪽이 취소돼도 서버가 준 새 토큰은 저장한다`() = runBlocking {
        val storage = MemoryStorage(stored)
        val http = FakeHttp(ok(Fixtures.session("access-2", "refresh-2")))
        val auth = session(http, storage)
        // 화면 회전 등으로 갱신을 부른 쪽이, 요청은 이미 서버에 갔는데 응답을 받기 전에 취소되는 상황
        val job = launch { auth.refresh() }
        while (http.calls.isEmpty()) delay(1)
        job.cancelAndJoin()
        assertEquals("refresh-2", storage.value?.refreshToken)
    }

    @Test
    fun `갱신 거절·응답 유실·커밋 결과 모름은 로그인 상태를 지운다`() = runBlocking {
        val cases = listOf(
            HttpReply.Received(401, """{"error":{"code":"REFRESH_REJECTED","message":"m"}}""") to "REFRESH_REJECTED",
            HttpReply.Received(503, """{"error":{"code":"AUTH_UNAVAILABLE","message":"m","retryable":false}}""") to "AUTH_UNAVAILABLE",
            HttpReply.NoResponse to AuthSession.NO_RESPONSE,
            // 200 인데 읽을 수 없으면 서버는 이미 토큰을 바꿨을 수 있다
            ok("{}") to AuthJson.INVALID_RESPONSE,
        )
        for ((reply, code) in cases) {
            val storage = MemoryStorage(stored)
            assertEquals(RefreshOutcome.SignedOut(code), session(FakeHttp(reply), storage).refresh())
            assertNull(storage.value)
        }
    }

    @Test
    fun `미커밋이 확인된 503과 연결 실패는 토큰을 두고 나중에 다시 한다`() = runBlocking {
        val cases = listOf(
            HttpReply.Received(503, """{"error":{"code":"AUTH_UNAVAILABLE","message":"m","retryable":true}}""") to "AUTH_UNAVAILABLE",
            HttpReply.NotSent to AuthSession.NETWORK,
        )
        for ((reply, code) in cases) {
            val storage = MemoryStorage(stored)
            assertEquals(RefreshOutcome.TryLater(code), session(FakeHttp(reply), storage).refresh())
            assertEquals("refresh-1", storage.value?.refreshToken)
        }
    }

    @Test
    fun `액세스가 1분 안에 끝나면 갱신해서 준다`() = runBlocking {
        val storage = MemoryStorage(stored) // 30초 뒤 만료
        val http = FakeHttp(ok(Fixtures.session("access-2", "refresh-2")))
        assertEquals("access-2", session(http, storage).accessToken())

        val fresh = MemoryStorage(stored.copy(accessTokenExpiresAt = now.plusSeconds(600)))
        val idle = FakeHttp()
        assertEquals("access-1", session(idle, fresh).accessToken())
        assertTrue(idle.calls.isEmpty())
    }

    @Test
    fun `로그아웃은 서버 응답과 관계없이 기기에서 지운다`() = runBlocking {
        val storage = MemoryStorage(stored)
        val http = FakeHttp(HttpReply.NotSent)
        assertEquals(ApiResult.NotSent, session(http, storage).logout())
        assertEquals("/api/v1/auth/logout", http.calls.single().first)
        assertNull(storage.value)

        val ok = MemoryStorage(stored)
        assertEquals(ApiResult.Ok(Unit), session(FakeHttp(HttpReply.Received(204, null)), ok).logout())
        assertNull(ok.value)
    }
}
