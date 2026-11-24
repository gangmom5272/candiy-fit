package io.candiy.healthconnect.quickstart.auth

import android.content.Context
import io.candiy.healthconnect.quickstart.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.Instant

/** 갱신 시도의 결과. 화면은 이것만 보고 다음 행동(계속·다시 시도·로그인 화면)을 정한다 */
sealed interface RefreshOutcome {
    data class Refreshed(val session: Session) : RefreshOutcome

    /** 세션은 그대로 두고 나중에 다시 시도한다(연결 실패, 미커밋이 확인된 503) */
    data class TryLater(val code: String) : RefreshOutcome

    /** 저장된 로그인 상태를 지웠다. 다시 로그인해야 한다 */
    data class SignedOut(val code: String) : RefreshOutcome

    data object NoSession : RefreshOutcome
}

/**
 * 로그인 상태의 보관과 갱신 순서를 맡는다.
 *
 *  - 갱신은 한 번에 하나만 보낸다. 같은 갱신 토큰을 두 번 보내면 서버가 재사용으로 보고 세션을 폐기한다(명세 1.4).
 *    기다리던 호출은 앞선 호출이 받아 둔 새 토큰을 그대로 쓴다.
 *  - 응답을 잃은 갱신은 다시 보내지 않고 로그아웃으로 정리한다. 미커밋이 확인된 503(retryable=true)과
 *    연결 자체가 안 된 경우만 토큰을 두고 나중에 다시 시도한다(명세 3.5).
 */
class AuthSession(
    private val api: AuthApi,
    private val storage: SessionStorage,
    private val clock: () -> Instant = Instant::now,
) {
    private val lock = Mutex()

    suspend fun current(): Session? = io { storage.load() }

    /** 로그인 시도마다 새로 받는다. 실패 후에도 예전 challenge 를 다시 쓰지 않는다 */
    suspend fun challenge(social: SocialType): ApiResult<Challenge> = api.challenge(social)

    /** LOGGED_IN 이면 저장한다. SIGNUP_REQUIRED 는 저장할 것이 없다(가입 자격은 화면이 들고 있다 버린다) */
    suspend fun login(social: SocialType, idToken: String, challengeToken: String): ApiResult<LoginResult> =
        lock.withLock {
            uninterrupted {
                api.login(social, idToken, challengeToken).also {
                    if (it is ApiResult.Ok && it.value is LoginResult.LoggedIn) io { storage.save(it.value.session) }
                }
            }
        }

    suspend fun signup(signupToken: String, profile: SignupProfile): ApiResult<Session> =
        lock.withLock {
            uninterrupted {
                api.signup(signupToken, profile).also {
                    if (it is ApiResult.Ok) io { storage.save(it.value) }
                }
            }
        }

    suspend fun refresh(): RefreshOutcome {
        val before = io { storage.load() }?.refreshToken ?: return RefreshOutcome.NoSession
        return lock.withLock { uninterrupted { refreshLocked(before) } }
    }

    private suspend fun refreshLocked(before: String): RefreshOutcome {
        val session = io { storage.load() } ?: return RefreshOutcome.NoSession
        // 기다리는 사이 앞선 호출이 이미 바꿔 두었다
        if (session.refreshToken != before) return RefreshOutcome.Refreshed(session)
        return when (val result = api.refresh(session.refreshToken)) {
            is ApiResult.Ok -> {
                io { storage.save(result.value) }
                RefreshOutcome.Refreshed(result.value)
            }
            ApiResult.NotSent -> RefreshOutcome.TryLater(NETWORK)
            is ApiResult.Rejected ->
                if (result.status == 503 && result.code == "AUTH_UNAVAILABLE" && result.retryable == true) {
                    RefreshOutcome.TryLater(result.code)
                } else {
                    io { storage.clear() }
                    RefreshOutcome.SignedOut(result.code)
                }
            ApiResult.NoResponse -> {
                io { storage.clear() }
                RefreshOutcome.SignedOut(NO_RESPONSE)
            }
        }
    }

    /** 1분 안에 끝나는 액세스는 미리 갱신해서 준다. 갱신이 안 되면 null */
    suspend fun accessToken(): String? = validSession()?.accessToken

    /**
     * 쓸 수 있는 액세스 토큰과 그 회원을 한 쌍으로 준다(1분 안에 끝나면 미리 갱신). 갱신이 안 되면 null.
     * 토큰만 받아 쓰면 그 사이 계정이 바뀌었는지 알 수 없어, 회원을 확인하고 보내야 하는 요청은 이것을 쓴다(S15P31A403-366)
     */
    suspend fun validSession(): Session? {
        val session = current() ?: return null
        if (session.accessTokenExpiresAt.isAfter(clock().plus(EARLY_REFRESH))) return session
        return (refresh() as? RefreshOutcome.Refreshed)?.session
    }

    /**
     * 서버 세션을 폐기하고 기기의 로그인 상태를 지운다. 서버 응답과 관계없이 기기에서는 지운다 —
     * 폐기를 못 했어도 그 세션은 마지막 갱신 7일 뒤 스스로 끝난다(명세 1.4).
     */
    suspend fun logout(): ApiResult<Unit> = lock.withLock {
        uninterrupted {
            val session = io { storage.load() } ?: return@uninterrupted ApiResult.Ok(Unit)
            api.logout(session.refreshToken).also { io { storage.clear() } }
        }
    }

    private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }

    /**
     * 서버 호출과 그 결과 저장을 끊기지 않게 묶는다. 서버가 갱신 토큰을 바꿔 준 뒤 화면 회전 등으로 호출한 쪽이 취소돼도
     * 새 토큰을 끝까지 저장한다. 저장을 놓치면 다음 갱신이 예전 토큰을 보내 서버가 재사용으로 보고 세션을 폐기한다
     */
    private suspend fun <T> uninterrupted(block: suspend () -> T): T = withContext(NonCancellable) { block() }

    companion object {
        const val NETWORK = "NETWORK"
        const val NO_RESPONSE = "NO_RESPONSE"
        private val EARLY_REFRESH: Duration = Duration.ofMinutes(1)
    }
}

/** 앱 전체에서 하나만 쓴다. 갱신 잠금이 하나여야 한 번에 하나만 보낼 수 있다 */
object Auth {
    @Volatile private var instance: AuthSession? = null

    fun session(context: Context): AuthSession = instance ?: synchronized(this) {
        instance ?: AuthSession(
            api = AuthApi(UrlConnectionTransport(BuildConfig.BACKEND_BASE_URL)),
            storage = KeystoreSessionStorage(context),
        ).also { instance = it }
    }
}
