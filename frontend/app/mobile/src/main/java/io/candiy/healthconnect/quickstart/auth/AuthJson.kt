package io.candiy.healthconnect.quickstart.auth

import org.json.JSONObject
import java.time.Instant

/**
 * 로그인 API 요청 본문과 응답 해석. 기기 없이 단위 테스트로 확인한다.
 * 정의하지 않은 필드를 보내면 서버가 422 로 거절하므로(명세 1.1) 본문에는 명세의 필드만 넣는다.
 */
internal object AuthJson {
    /** 응답이 명세와 다를 때(200 인데 필드 없음 등)의 코드. 서버가 보내는 코드가 아니다 */
    const val INVALID_RESPONSE = "INVALID_RESPONSE"

    fun challengeBody(social: SocialType) = JSONObject()
        .put("socialType", social.name)
        .put("platformType", "ANDROID")

    fun loginBody(social: SocialType, idToken: String, challengeToken: String) = JSONObject()
        .put("socialType", social.name)
        .put("platformType", "ANDROID")
        .put("idToken", idToken)
        .put("challengeToken", challengeToken)

    /** 가입은 platformType 을 받지 않는다(가입 자격에 저장된 플랫폼을 쓴다) */
    fun signupBody(signupToken: String, profile: SignupProfile) = JSONObject()
        .put("signupToken", signupToken)
        .put("displayName", profile.displayName)
        .put("birthYear", profile.birthYear)
        .put("gender", profile.gender)
        .put("heightCm", profile.heightCm)
        .put("weightKg", profile.weightKg)

    fun refreshBody(refreshToken: String) = JSONObject().put("refreshToken", refreshToken)

    fun challenge(root: JSONObject): Challenge {
        val data = root.getJSONObject("data")
        return Challenge(
            challengeToken = data.getString("challengeToken"),
            nonce = data.getString("nonce"),
            expiresAt = Instant.parse(data.getString("expiresAt")),
        )
    }

    fun login(root: JSONObject): LoginResult {
        val data = root.getJSONObject("data")
        return when (val status = data.getString("status")) {
            "LOGGED_IN" -> LoginResult.LoggedIn(sessionData(data))
            "SIGNUP_REQUIRED" -> LoginResult.SignupRequired(
                signupToken = data.getString("signupToken"),
                expiresAt = Instant.parse(data.getString("signupTokenExpiresAt")),
                socialType = SocialType.valueOf(data.getString("socialType")),
            )
            else -> throw IllegalArgumentException("unknown login status $status")
        }
    }

    /** 가입(201·200)·갱신 응답. 둘 다 LOGGED_IN 과 같은 모양이고 ANDROID 는 본문에 refreshToken 이 있다 */
    fun session(root: JSONObject): Session = sessionData(root.getJSONObject("data"))

    private fun sessionData(data: JSONObject) = Session(
        accessToken = data.getString("accessToken"),
        accessTokenExpiresAt = Instant.parse(data.getString("accessTokenExpiresAt")),
        refreshToken = data.getString("refreshToken"),
        user = user(data.getJSONObject("user")),
    )

    fun user(json: JSONObject) = AuthUser(
        id = json.getString("id"),
        endUserId = json.getString("endUserId"),
        displayName = json.getString("displayName"),
        birthYear = json.getInt("birthYear"),
        gender = json.getString("gender"),
        heightCm = json.getDouble("heightCm"),
        weightKg = json.getDouble("weightKg"),
        socialType = SocialType.valueOf(json.getString("socialType")),
        createdAt = Instant.parse(json.getString("createdAt")),
    )

    /** 오류 본문(명세 1.2). JSON 이 아니면(앞단 프록시의 502 HTML 등) HTTP_상태 코드로 둔다 */
    fun error(status: Int, body: String?): ApiResult.Rejected {
        val error = runCatching { JSONObject(body ?: "").getJSONObject("error") }.getOrNull()
            ?: return ApiResult.Rejected(status, "HTTP_$status")
        val details = error.optJSONArray("details")?.let { array ->
            (0 until array.length()).mapNotNull { index ->
                array.optJSONObject(index)?.let { FieldError(it.optString("field"), it.optString("reason")) }
            }
        }.orEmpty()
        return ApiResult.Rejected(
            status = status,
            code = error.optString("code").ifEmpty { "HTTP_$status" },
            retryable = if (error.has("retryable")) error.optBoolean("retryable") else null,
            details = details,
        )
    }

    /* ── 기기 저장용. 서버 응답과 같은 모양으로 적어 두고 같은 해석기로 읽는다 ── */

    fun encodeSession(session: Session): String = JSONObject()
        .put("accessToken", session.accessToken)
        .put("accessTokenExpiresAt", session.accessTokenExpiresAt.toString())
        .put("refreshToken", session.refreshToken)
        .put("user", JSONObject()
            .put("id", session.user.id)
            .put("endUserId", session.user.endUserId)
            .put("displayName", session.user.displayName)
            .put("birthYear", session.user.birthYear)
            .put("gender", session.user.gender)
            .put("heightCm", session.user.heightCm)
            .put("weightKg", session.user.weightKg)
            .put("socialType", session.user.socialType.name)
            .put("createdAt", session.user.createdAt.toString()))
        .toString()

    fun decodeSession(text: String): Session = sessionData(JSONObject(text))
}
