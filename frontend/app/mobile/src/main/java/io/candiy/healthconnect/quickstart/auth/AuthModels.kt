package io.candiy.healthconnect.quickstart.auth

import java.time.Instant

/**
 * 우리 백엔드 로그인 API(명세 3.1~3.6)가 주고받는 값.
 * 토큰·nonce 가 든 클래스는 toString 에서 값을 가린다 — 로그·예외 문구에 실수로 찍혀도 새지 않게.
 */
enum class SocialType { GOOGLE, KAKAO }

/** 명세 2.1 User. endUserId 는 id 와 같은 값이며 CANDiY SDK 에 넘기는 사용자 ID 다 */
data class AuthUser(
    val id: String,
    val endUserId: String,
    val displayName: String,
    val birthYear: Int,
    val gender: String,
    val heightCm: Double,
    val weightKg: Double,
    val socialType: SocialType,
    val createdAt: Instant,
)

/** 로그인된 상태. 액세스는 15분, 갱신 토큰은 갱신할 때마다 새 값으로 바뀐다 */
data class Session(
    val accessToken: String,
    val accessTokenExpiresAt: Instant,
    val refreshToken: String,
    val user: AuthUser,
) {
    override fun toString() = "Session(user=${user.socialType}, accessTokenExpiresAt=$accessTokenExpiresAt)"
}

/** 명세 3.2. nonce 원문을 SDK 로그인 요청에 넣고, challengeToken 은 로그인 API 에 보낸다. 5분·한 번만 쓴다 */
data class Challenge(val challengeToken: String, val nonce: String, val expiresAt: Instant) {
    override fun toString() = "Challenge(expiresAt=$expiresAt)"
}

sealed interface LoginResult {
    data class LoggedIn(val session: Session) : LoginResult

    /** 신규 회원. signupToken 으로 10분 안에 가입해야 한다 */
    data class SignupRequired(val signupToken: String, val expiresAt: Instant, val socialType: SocialType) : LoginResult {
        override fun toString() = "SignupRequired(socialType=$socialType, expiresAt=$expiresAt)"
    }
}

/** 가입 프로필(명세 3.4·4.3). 다섯 칸 모두 필수, gender 는 MALE·FEMALE */
data class SignupProfile(
    val displayName: String,
    val birthYear: Int,
    val gender: String,
    val heightCm: Double,
    val weightKg: Double,
)

/** 명세 1.2 의 422 details 한 칸 */
data class FieldError(val field: String, val reason: String)

/** API 한 번 호출의 결과. 서버에 닿았는지 아닌지를 나눠야 갱신 토큰을 안전하게 다룰 수 있다 */
sealed interface ApiResult<out T> {
    data class Ok<T>(val value: T) : ApiResult<T>

    /** 서버가 오류로 답했다. retryable 은 refresh 503 에만 온다 */
    data class Rejected(
        val status: Int,
        val code: String,
        val retryable: Boolean? = null,
        val details: List<FieldError> = emptyList(),
    ) : ApiResult<Nothing>

    /** 연결 자체가 안 됐다. 서버 상태가 바뀌지 않았으니 같은 요청을 다시 해도 된다 */
    data object NotSent : ApiResult<Nothing>

    /** 보냈지만 답을 못 받았다. 서버에서 처리됐는지 알 수 없어 같은 요청을 다시 보내면 안 된다 */
    data object NoResponse : ApiResult<Nothing>
}
