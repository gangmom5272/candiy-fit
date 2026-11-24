package io.candiy.healthconnect.quickstart.auth

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/** 값은 모두 지어낸 것이다. 실제 토큰·회원 정보를 넣지 않는다 */
class AuthJsonTest {

    @Test
    fun `요청 본문에는 명세의 필드만 넣는다`() {
        assertEquals(setOf("socialType", "platformType"), AuthJson.challengeBody(SocialType.KAKAO).keySet())
        val login = AuthJson.loginBody(SocialType.GOOGLE, "id-token", "challenge")
        assertEquals(setOf("socialType", "platformType", "idToken", "challengeToken"), login.keySet())
        assertEquals("ANDROID", login.getString("platformType"))
        // 가입은 platformType·nonce 를 보내지 않는다
        val signup = AuthJson.signupBody("signup", SignupProfile("핏디", 2000, "FEMALE", 165.25, 55.0))
        assertEquals(setOf("signupToken", "displayName", "birthYear", "gender", "heightCm", "weightKg"), signup.keySet())
    }

    @Test
    fun `로그인 응답의 두 상태를 구분한다`() {
        val loggedIn = AuthJson.login(JSONObject(Fixtures.loggedIn()))
        assertTrue(loggedIn is LoginResult.LoggedIn)
        val session = (loggedIn as LoginResult.LoggedIn).session
        assertEquals("refresh-1", session.refreshToken)
        assertEquals(Fixtures.USER_ID, session.user.endUserId)
        assertEquals(SocialType.KAKAO, session.user.socialType)

        val signup = AuthJson.login(JSONObject(
            """{"data":{"status":"SIGNUP_REQUIRED","signupToken":"signup-1","signupTokenExpiresAt":"2026-10-29T01:10:00Z","socialType":"GOOGLE"}}"""
        ))
        assertEquals(
            LoginResult.SignupRequired("signup-1", Instant.parse("2026-10-29T01:10:00Z"), SocialType.GOOGLE),
            signup,
        )
    }

    @Test
    fun `오류 본문에서 코드·재시도 여부·칸별 사유를 읽는다`() {
        val refresh503 = AuthJson.error(503, """{"error":{"code":"AUTH_UNAVAILABLE","message":"m","retryable":false}}""")
        assertEquals("AUTH_UNAVAILABLE", refresh503.code)
        assertEquals(false, refresh503.retryable)

        val invalid = AuthJson.error(422, """{"error":{"code":"INVALID_REQUEST","message":"m","details":[{"field":"heightCm","reason":"decimal_places"}]}}""")
        assertEquals(listOf(FieldError("heightCm", "decimal_places")), invalid.details)
        assertNull(invalid.retryable)

        // 앞단 프록시가 HTML 을 돌려줘도 죽지 않는다
        assertEquals("HTTP_502", AuthJson.error(502, "<html>bad gateway</html>").code)
        assertEquals("HTTP_500", AuthJson.error(500, null).code)
    }

    @Test
    fun `저장한 로그인 상태를 그대로 다시 읽는다`() {
        val session = (AuthJson.login(JSONObject(Fixtures.loggedIn())) as LoginResult.LoggedIn).session
        assertEquals(session, AuthJson.decodeSession(AuthJson.encodeSession(session)))
    }

    @Test
    fun `토큰이 든 값은 문자열로 찍어도 토큰이 보이지 않는다`() {
        val session = (AuthJson.login(JSONObject(Fixtures.loggedIn())) as LoginResult.LoggedIn).session
        assertFalse(session.toString().contains("access-1"))
        assertFalse(session.toString().contains("refresh-1"))
        assertFalse(Challenge("challenge-1", "nonce-1", Instant.EPOCH).toString().contains("nonce-1"))
    }
}

internal object Fixtures {
    const val USER_ID = "6ef7d160-428a-4bd3-86ca-08f084390dea"

    fun loggedIn(access: String = "access-1", refresh: String = "refresh-1", expiresAt: String = "2026-10-29T01:15:00Z") =
        """{"data":{"status":"LOGGED_IN",${sessionFields(access, refresh, expiresAt)}}}"""

    /** 가입·갱신 응답(status 없음) */
    fun session(access: String, refresh: String, expiresAt: String = "2026-10-29T01:30:00Z") =
        """{"data":{${sessionFields(access, refresh, expiresAt)}}}"""

    private fun sessionFields(access: String, refresh: String, expiresAt: String) =
        """"accessToken":"$access","accessTokenExpiresAt":"$expiresAt","refreshToken":"$refresh",""" +
            """"user":{"id":"$USER_ID","endUserId":"$USER_ID","displayName":"핏디","birthYear":2000,""" +
            """"gender":"FEMALE","heightCm":165.25,"weightKg":55,"socialType":"KAKAO","createdAt":"2026-10-19T00:00:00Z"}"""
}
