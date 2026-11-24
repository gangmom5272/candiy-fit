package io.candiy.healthconnect.quickstart.auth

import java.time.Instant

/** Google Credential Manager·Kakao SDK 로그인 결과 */
sealed interface SocialSignInResult {
    data class Success(val idToken: String) : SocialSignInResult {
        override fun toString() = "Success"
    }

    /** 사용자가 닫았거나 동의하지 않았다. 로그인 API 를 부르지 않는다(명세 3.3) */
    data object Cancelled : SocialSignInResult

    /** 이 빌드에 제공자 설정값(클라이언트 ID·앱 키)이 없다 */
    data object NotConfigured : SocialSignInResult

    /** kind: 로그에 남길 실패 종류(예외 이름 등). 사용자에게는 보이지 않는다 */
    data class Failed(val kind: String) : SocialSignInResult
}

/** 제공자 SDK 로그인. 서버가 준 nonce 원문을 그대로 SDK 요청에 넣는다 */
interface SocialSignIn {
    fun isConfigured(social: SocialType): Boolean
    suspend fun signIn(social: SocialType, nonce: String): SocialSignInResult
}

/** 제공자 SDK 를 붙이기 전 자리. 버튼을 누르면 설정이 없다고 안내한다 */
object NotConfiguredSignIn : SocialSignIn {
    override fun isConfigured(social: SocialType) = false
    override suspend fun signIn(social: SocialType, nonce: String) = SocialSignInResult.NotConfigured
}

sealed interface LoginStep {
    data class LoggedIn(val session: Session) : LoginStep
    data class NeedsSignup(val pending: LoginResult.SignupRequired) : LoginStep
    data object Cancelled : LoginStep

    /** reason: 로그에 남길 실패 이유(어느 단계·서버 오류 코드). 화면에는 message 만 보인다 */
    data class Failed(val message: String, val reason: String = "-") : LoginStep
}

sealed interface SignupStep {
    data class LoggedIn(val session: Session) : SignupStep
    data class Invalid(val errors: Map<String, String>) : SignupStep
    /** 가입 자격을 더 쓸 수 없다. 로그인 화면으로 돌아가 처음부터 */
    data class Restart(val message: String) : SignupStep
    data class Failed(val message: String) : SignupStep
}

/**
 * 로그인 한 번의 순서(명세 3.1 ANDROID): challenge 발급 → SDK 로그인(nonce) → 로그인 API → 기존 회원·신규 회원.
 * 신규 회원은 가입 화면을 거쳐 [signup] 으로 끝난다. 화면은 결과(LoginStep·SignupStep)만 보고 그린다.
 */
class LoginFlow(
    private val auth: AuthSession,
    private val signIn: SocialSignIn,
    private val clock: () -> Instant = Instant::now,
) {
    suspend fun login(social: SocialType): LoginStep {
        // 설정이 없으면 challenge 를 받지 않는다(받아 봐야 쓰지 못하고 버린다)
        if (!signIn.isConfigured(social)) return LoginStep.Failed(notConfigured(social))
        val challenge = when (val result = auth.challenge(social)) {
            is ApiResult.Ok -> result.value
            else -> return LoginStep.Failed(errorMessage(result), "challenge:${reasonOf(result)}")
        }
        val idToken = when (val result = signIn.signIn(social, challenge.nonce)) {
            is SocialSignInResult.Success -> result.idToken
            SocialSignInResult.Cancelled -> return LoginStep.Cancelled
            SocialSignInResult.NotConfigured -> return LoginStep.Failed(notConfigured(social))
            is SocialSignInResult.Failed ->
                return LoginStep.Failed("${label(social)} 로그인에 실패했어요. 다시 시도해 주세요.", "provider:${result.kind}")
        }
        return when (val result = auth.login(social, idToken, challenge.challengeToken)) {
            is ApiResult.Ok -> when (val value = result.value) {
                is LoginResult.LoggedIn -> LoginStep.LoggedIn(value.session)
                is LoginResult.SignupRequired -> LoginStep.NeedsSignup(value)
            }
            else -> LoginStep.Failed(errorMessage(result), "login:${reasonOf(result)}")
        }
    }

    /** 로그용 실패 이유. 서버 오류는 `상태_코드`(예: 500_INTERNAL_ERROR), 그 밖은 결과 종류 */
    private fun reasonOf(result: ApiResult<*>): String = when (result) {
        is ApiResult.Rejected -> "${result.status}_${result.code}"
        else -> result::class.simpleName ?: "-"
    }

    suspend fun signup(pending: LoginResult.SignupRequired, form: ProfileForm): SignupStep {
        if (!clock().isBefore(pending.expiresAt)) return SignupStep.Restart(SIGNUP_EXPIRED)
        val profile = when (val check = ProfileValidation.check(form)) {
            is ProfileCheck.Ok -> check.profile
            is ProfileCheck.Invalid -> return SignupStep.Invalid(check.errors)
        }
        return when (val result = auth.signup(pending.signupToken, profile)) {
            is ApiResult.Ok -> SignupStep.LoggedIn(result.value)
            is ApiResult.Rejected -> when {
                result.code == "SIGNUP_TOKEN_INVALID" -> SignupStep.Restart(SIGNUP_EXPIRED)
                result.status == 422 -> {
                    val errors = result.details
                        .filter { it.field in ProfileValidation.FIELDS }
                        .associate { it.field to ProfileValidation.reasonMessage(it.field, it.reason) }
                    if (errors.isEmpty()) SignupStep.Failed(errorMessage(result)) else SignupStep.Invalid(errors)
                }
                else -> SignupStep.Failed(errorMessage(result))
            }
            // 가입은 됐는데 응답만 잃었을 수 있다. 다시 로그인하면 기존 회원으로 들어간다(명세 3.4)
            ApiResult.NoResponse -> SignupStep.Restart("가입 결과를 확인하지 못했어요. 로그인부터 다시 해 주세요.")
            ApiResult.NotSent -> SignupStep.Failed(errorMessage(result))
        }
    }

    companion object {
        const val SIGNUP_EXPIRED = "가입 시간(10분)이 지났어요. 로그인부터 다시 해 주세요."

        fun label(social: SocialType) = when (social) {
            SocialType.GOOGLE -> "Google"
            SocialType.KAKAO -> "카카오"
        }

        fun notConfigured(social: SocialType) = "이 앱 빌드에는 ${label(social)} 로그인 설정이 아직 없어요."

        fun errorMessage(result: ApiResult<*>): String = when (result) {
            ApiResult.NotSent -> "서버에 연결하지 못했어요. 인터넷 연결을 확인해 주세요."
            ApiResult.NoResponse -> "서버 응답을 받지 못했어요. 처음부터 다시 시도해 주세요."
            is ApiResult.Rejected -> when (result.code) {
                "SOCIAL_CREDENTIAL_INVALID" -> "로그인 확인에 실패했어요. 다시 시도해 주세요."
                "SOCIAL_PROVIDER_UNAVAILABLE" -> "Google·카카오 서버와 연결이 원활하지 않아요. 잠시 후 다시 시도해 주세요."
                else -> "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."
            }
            is ApiResult.Ok -> ""
        }
    }
}
