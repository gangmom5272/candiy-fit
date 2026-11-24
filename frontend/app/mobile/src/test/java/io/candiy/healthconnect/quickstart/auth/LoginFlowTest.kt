package io.candiy.healthconnect.quickstart.auth

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/** 로그인 한 번의 순서. 서버·SDK 대신 정해 둔 답을 쓴다. 값은 모두 지어낸 것이다 */
class LoginFlowTest {

    private class ScriptedHttp(vararg replies: HttpReply) : HttpTransport {
        private val queue = ArrayDeque(replies.toList())
        val calls = mutableListOf<Pair<String, JSONObject>>()
        override fun postJson(path: String, body: JSONObject): HttpReply {
            calls += path to body
            return queue.removeFirst()
        }
    }

    private class MemoryStorage(var value: Session? = null) : SessionStorage {
        override fun load() = value
        override fun save(session: Session) { value = session }
        override fun clear() { value = null }
    }

    private class FakeSignIn(val result: SocialSignInResult, val configured: Boolean = true) : SocialSignIn {
        var nonce: String? = null
        override fun isConfigured(social: SocialType) = configured
        override suspend fun signIn(social: SocialType, nonce: String): SocialSignInResult {
            this.nonce = nonce
            return result
        }
    }

    private val now = Instant.parse("2026-10-29T01:00:00Z")
    private val challenge = HttpReply.Received(
        200, """{"data":{"challengeToken":"challenge-1","nonce":"nonce-1","expiresAt":"2026-10-29T01:05:00Z"}}"""
    )
    private val signupRequired = HttpReply.Received(
        200, """{"data":{"status":"SIGNUP_REQUIRED","signupToken":"signup-1","signupTokenExpiresAt":"2026-10-29T01:10:00Z","socialType":"KAKAO"}}"""
    )
    private val pending = LoginResult.SignupRequired("signup-1", Instant.parse("2026-10-29T01:10:00Z"), SocialType.KAKAO)
    private val form = ProfileForm("핏디", "2000", "FEMALE", "165.25", "55")

    private fun flow(http: ScriptedHttp, signIn: SocialSignIn = FakeSignIn(SocialSignInResult.Success("id-token")), storage: MemoryStorage = MemoryStorage()) =
        LoginFlow(AuthSession(AuthApi(http), storage, clock = { now }), signIn, clock = { now })

    @Test
    fun `서버 nonce 를 SDK 에 넘기고, ID 토큰과 challengeToken 으로 로그인한다`() = runBlocking {
        val http = ScriptedHttp(challenge, HttpReply.Received(200, Fixtures.loggedIn()))
        val signIn = FakeSignIn(SocialSignInResult.Success("id-token"))
        val step = flow(http, signIn).login(SocialType.KAKAO)

        assertTrue(step is LoginStep.LoggedIn)
        assertEquals("nonce-1", signIn.nonce)
        val body = http.calls[1].second
        assertEquals("id-token", body.getString("idToken"))
        assertEquals("challenge-1", body.getString("challengeToken"))
        assertTrue(!body.has("nonce"))
    }

    @Test
    fun `신규 회원은 가입 화면으로 간다`() = runBlocking {
        assertEquals(LoginStep.NeedsSignup(pending), flow(ScriptedHttp(challenge, signupRequired)).login(SocialType.KAKAO))
    }

    @Test
    fun `취소하면 로그인 API 를 부르지 않는다`() = runBlocking {
        val http = ScriptedHttp(challenge)
        assertEquals(LoginStep.Cancelled, flow(http, FakeSignIn(SocialSignInResult.Cancelled)).login(SocialType.GOOGLE))
        assertEquals(listOf("/api/v1/auth/social/challenge"), http.calls.map { it.first })
    }

    @Test
    fun `설정이 없으면 challenge 도 받지 않고 안내한다`() = runBlocking {
        val http = ScriptedHttp()
        val step = flow(http, NotConfiguredSignIn).login(SocialType.GOOGLE)
        assertEquals(LoginStep.Failed(LoginFlow.notConfigured(SocialType.GOOGLE)), step)
        assertTrue(http.calls.isEmpty())
    }

    @Test
    fun `서버 오류는 안내 문구와 함께 로그용 이유(단계·상태·코드)를 남긴다`() = runBlocking {
        val serverError = HttpReply.Received(500, """{"error":{"code":"INTERNAL_ERROR","message":"m"}}""")
        val step = flow(ScriptedHttp(challenge, serverError)).login(SocialType.KAKAO) as LoginStep.Failed
        assertEquals("login:500_INTERNAL_ERROR", step.reason)
        assertEquals("요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.", step.message)

        val offline = flow(ScriptedHttp(HttpReply.NotSent)).login(SocialType.KAKAO) as LoginStep.Failed
        assertEquals("challenge:NotSent", offline.reason)
    }

    @Test
    fun `가입에 성공하면 로그인 상태가 저장된다`() = runBlocking {
        val storage = MemoryStorage()
        val http = ScriptedHttp(HttpReply.Received(201, Fixtures.session("access-2", "refresh-2")))
        val step = flow(http, storage = storage).signup(pending, form)
        assertTrue(step is SignupStep.LoggedIn)
        assertEquals("refresh-2", storage.value?.refreshToken)
    }

    @Test
    fun `입력이 틀리면 서버에 보내지 않는다`() = runBlocking {
        val http = ScriptedHttp()
        val step = flow(http).signup(pending, form.copy(heightCm = "0"))
        assertTrue((step as SignupStep.Invalid).errors.containsKey("heightCm"))
        assertTrue(http.calls.isEmpty())
    }

    @Test
    fun `서버 422 는 칸별 문구로, 만료된 가입 자격·응답 유실은 로그인부터 다시`() = runBlocking {
        val invalid = HttpReply.Received(422, """{"error":{"code":"INVALID_REQUEST","message":"m","details":[{"field":"displayName","reason":"too_long"}]}}""")
        assertEquals(
            SignupStep.Invalid(mapOf("displayName" to "닉네임은 12자까지 쓸 수 있어요.")),
            flow(ScriptedHttp(invalid)).signup(pending, form),
        )
        val expired = HttpReply.Received(401, """{"error":{"code":"SIGNUP_TOKEN_INVALID","message":"m"}}""")
        assertEquals(SignupStep.Restart(LoginFlow.SIGNUP_EXPIRED), flow(ScriptedHttp(expired)).signup(pending, form))
        assertTrue(flow(ScriptedHttp(HttpReply.NoResponse)).signup(pending, form) is SignupStep.Restart)
    }

    @Test
    fun `10분이 지난 가입 자격은 보내지 않는다`() = runBlocking {
        val http = ScriptedHttp()
        val late = pending.copy(expiresAt = now.minusSeconds(1))
        assertEquals(SignupStep.Restart(LoginFlow.SIGNUP_EXPIRED), flow(http).signup(late, form))
        assertTrue(http.calls.isEmpty())
    }
}
