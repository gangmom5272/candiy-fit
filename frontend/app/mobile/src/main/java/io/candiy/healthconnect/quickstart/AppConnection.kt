package io.candiy.healthconnect.quickstart

import io.candiy.healthconnect.quickstart.auth.AuthJson
import io.candiy.healthconnect.quickstart.auth.AuthSession
import io.candiy.healthconnect.quickstart.auth.HttpReply
import io.candiy.healthconnect.quickstart.auth.RefreshOutcome
import io.candiy.healthconnect.quickstart.auth.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/*
 * 앱 최초 연동 완료 보고(S15P31A403-366, 백엔드 명세 5.7 · backend/docs/app-connection-363.md).
 * 권한과 동기화 등록을 마친 뒤 서버에 "이 회원은 앱 연동을 마쳤다"고 알린다. 웹은 이 상태로 앱 미연동 안내를 가른다.
 *
 * 지키는 것
 *  - 서비스 로그인 액세스 토큰만 보낸다. 본문·쿼리·사용자 ID 는 보내지 않는다(서버가 토큰으로 회원을 안다).
 *  - 보고하려던 회원과 지금 로그인한 회원이 같을 때만 보낸다. 계정을 바꾼 뒤 늦게 온 결과로 다른 계정에 보고하지 않는다.
 *  - 보고만 실패하면 동기화 등록을 다시 하지 않고 보고만 제한적으로 다시 보낸다. 끝없이 되풀이하지 않는다.
 *  - 로그에는 결과 종류와 코드만 남긴다. 토큰·회원 ID 는 남기지 않는다.
 */

/** 화면에 보여 줄 연동 완료 보고 상태 */
enum class ConnectionReport {
    /** 아직 보고할 때가 아니다(권한·동기화 등록 전) */
    IDLE,
    /** 서버에 알리는 중 */
    SENDING,
    /** 서버가 받았다(HTTP 200). 이때만 연결 완료로 보여 준다 */
    REPORTED,
    /** 앱 설정은 끝났지만 서버에 알리지 못했다. 다시 시도할 수 있다 */
    PENDING,
}

/** 보고 한 번의 결과 */
sealed interface ReportResult {
    data object Reported : ReportResult
    /** 알리지 못했다. code 는 서버 오류 코드 또는 NETWORK·NO_RESPONSE */
    data class Pending(val code: String) : ReportResult
    /** 보고하려던 회원이 더 이상 로그인해 있지 않거나 이 시도가 취소됐다. 아무것도 하지 않는다 */
    data object Stale : ReportResult
    /** 로그인이 끝났다(갱신까지 거절). 로그인 화면으로 보낸다 */
    data object SignedOut : ReportResult
}

fun interface AppConnectionTransport {
    /** IO 스레드에서 부른다. 본문 없이 PUT 한다 */
    fun put(accessToken: String): HttpReply
}

/** 앱의 다른 호출(auth/AuthApi.kt)과 같은 HttpURLConnection 방식 */
class UrlAppConnectionTransport(baseUrl: String) : AppConnectionTransport {
    private val base = baseUrl.trimEnd('/')

    override fun put(accessToken: String): HttpReply {
        val conn = URL("$base$PATH").openConnection() as HttpURLConnection
        conn.requestMethod = "PUT"
        // 본문을 보내지 않는다. 서버는 {} 나 null 도 422 로 거절한다
        conn.doOutput = false
        conn.connectTimeout = 10_000
        conn.readTimeout = 15_000
        conn.setRequestProperty("Accept", "application/json")
        conn.setRequestProperty("Authorization", "Bearer $accessToken")
        try {
            conn.connect()
        } catch (_: IOException) {
            conn.disconnect()
            return HttpReply.NotSent
        }
        return try {
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            HttpReply.Received(status, stream?.bufferedReader()?.use { it.readText() })
        } catch (_: IOException) {
            HttpReply.NoResponse
        } finally {
            conn.disconnect()
        }
    }

    private companion object {
        const val PATH = "/api/v1/health/app-connection"
    }
}

object AppConnection {
    /**
     * 보고해도 되는 권한 상태인지. 동기화 대상 지표의 읽기 권한이 하나라도 허용돼 있어야 한다.
     * 쓰기·백그라운드·과거 기록 권한만 있는 경우는 건강 기록을 읽을 수 없어 연동으로 치지 않는다
     */
    fun hasReadPermission(granted: Set<String>, readPermissions: Set<String>): Boolean =
        granted.any { it in readPermissions }

    /** 동기화 등록에 쓴 사용자 ID 가 로그인한 회원의 것인지 */
    fun sameMember(session: Session?, endUserId: String?): Boolean =
        session != null && endUserId != null && session.user.endUserId == endUserId
}

/**
 * 완료 보고를 보낸다. 다시 보내도 서버의 최초 완료 시각은 그대로라(멱등) 응답을 잃었을 때도 같은 보고를 다시 보낼 수 있다.
 *
 * @param stillValid 이 시도가 아직 유효한지(계정 전환·로그아웃으로 취소되지 않았는지). 보내기 직전마다 확인한다
 */
class AppConnectionReporter(
    private val auth: AuthSession,
    private val transport: AppConnectionTransport,
    private val retryDelaysMs: List<Long> = listOf(2_000L, 5_000L),
    private val wait: suspend (Long) -> Unit = { delay(it) },
) {
    /** @param memberId 보고하려는 회원(서비스 회원 ID). 보낼 때 로그인한 회원과 다르면 보내지 않는다 */
    suspend fun report(memberId: String, stillValid: () -> Boolean): ReportResult {
        var refreshed = false
        var retries = 0
        while (true) {
            // 토큰과 회원을 한 쌍으로 얻는다. 토큰만 받으면 그 사이 계정이 바뀐 것을 알 수 없다
            val session = auth.validSession() ?: return if (auth.current() == null) ReportResult.SignedOut else ReportResult.Pending(AuthSession.NETWORK)
            if (session.user.id != memberId || !stillValid()) return ReportResult.Stale

            when (val reply = withContext(Dispatchers.IO) { transport.put(session.accessToken) }) {
                is HttpReply.Received -> {
                    if (reply.status in 200..299) return ReportResult.Reported
                    val error = AuthJson.error(reply.status, reply.body)
                    when {
                        // 액세스 만료는 한 번만 갱신해 같은 회원일 때 다시 보낸다
                        reply.status == 401 && error.code == "AUTH_EXPIRED" && !refreshed -> {
                            refreshed = true
                            when (val outcome = auth.refresh()) {
                                is RefreshOutcome.Refreshed -> continue
                                is RefreshOutcome.TryLater -> return ReportResult.Pending(outcome.code)
                                else -> return ReportResult.SignedOut
                            }
                        }
                        // 서버가 저장하지 못했다고 알려 준 경우만 잠시 뒤 다시 보낸다
                        reply.status == 503 && error.retryable == true && retries < retryDelaysMs.size -> wait(retryDelaysMs[retries++])
                        // 422(요청 모양 오류)·500·AUTH_REQUIRED 등은 되풀이해도 같으므로 멈추고 알린다
                        else -> return ReportResult.Pending(error.code)
                    }
                }
                // 연결 실패·응답 유실. 저장됐을 수도 있지만 다시 보내도 결과가 같다
                HttpReply.NotSent, HttpReply.NoResponse -> {
                    if (retries >= retryDelaysMs.size) {
                        return ReportResult.Pending(if (reply == HttpReply.NotSent) AuthSession.NETWORK else AuthSession.NO_RESPONSE)
                    }
                    wait(retryDelaysMs[retries++])
                }
            }
        }
    }
}

/** 로그에 남길 결과 코드. 토큰·회원 ID 는 들어가지 않는다 */
fun ReportResult.code(): String = when (this) {
    ReportResult.Reported -> "REPORTED"
    is ReportResult.Pending -> code
    ReportResult.Stale -> "STALE"
    ReportResult.SignedOut -> "SIGNED_OUT"
}
