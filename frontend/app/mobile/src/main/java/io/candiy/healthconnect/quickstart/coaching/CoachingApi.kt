package io.candiy.healthconnect.quickstart.coaching

import io.candiy.healthconnect.quickstart.auth.AuthSession
import io.candiy.healthconnect.quickstart.auth.RefreshOutcome
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** 코칭 REST 한 번의 결과 */
sealed interface CoachingResult<out T> {
    data class Ok<T>(val value: T) : CoachingResult<T>
    /** 로그인 상태가 끝났다(갱신까지 거절). 로그인 화면으로 보낸다 */
    data object SignedOut : CoachingResult<Nothing>
    /** 연결 실패·서버 오류. code 는 서버 오류 code 또는 NETWORK */
    data class Failed(val status: Int, val code: String) : CoachingResult<Nothing>
}

/**
 * 명세 6.2~6.4 GET. 앱의 다른 호출(auth/AuthApi.kt)과 같이 HttpURLConnection 으로 직접 부른다.
 * 401 이면 갱신([AuthSession.refresh]) 뒤 한 번만 다시 보낸다(명세 6.2 AUTH_EXPIRED). 갱신도 거절되면 [CoachingResult.SignedOut].
 */
class CoachingApi(baseUrl: String, private val auth: AuthSession) {
    private val base = baseUrl.trimEnd('/')

    suspend fun conversations(cursor: String? = null): CoachingResult<ConversationPage> =
        get("/api/v1/coaching/conversations?limit=20" + (cursor?.let { "&cursor=${enc(it)}" } ?: ""), CoachingJson::conversations)

    suspend fun messages(conversationId: String, beforeSequenceNo: Int? = null): CoachingResult<MessagePage> =
        get(
            "/api/v1/coaching/conversations/${enc(conversationId)}/messages" + (beforeSequenceNo?.let { "?beforeSequenceNo=$it" } ?: ""),
            CoachingJson::messages,
        )

    suspend fun request(requestId: String): CoachingResult<RequestState> =
        get("/api/v1/coaching/requests/${enc(requestId)}", CoachingJson::requestStateBody)

    /**
     * 날짜별 시간대 걸음(/health/timeline, 1~31일). 활동 달력에서 고른 날의 0~23시 걸음에 쓴다(S15P31A403-440).
     * 건강 기록 조회지만 앱에서는 근거 화면만 써서 여기 둔다. 웹 getActivityHours 와 같은 호출이다
     */
    suspend fun hourlySteps(start: String, end: String): CoachingResult<Map<String, List<Double>?>> =
        get("/api/v1/health/timeline?start=${enc(start)}&end=${enc(end)}", HourlyStepsJson::days)

    private fun enc(value: String) = URLEncoder.encode(value, "UTF-8")

    private suspend fun <T> get(path: String, parse: (String) -> T): CoachingResult<T> {
        val token = auth.accessToken() ?: return signedOutOrRetry()
        val first = withContext(Dispatchers.IO) { send(path, token) }
        val reply = if (first.status == 401) {
            when (val outcome = auth.refresh()) {
                is RefreshOutcome.Refreshed -> withContext(Dispatchers.IO) { send(path, outcome.session.accessToken) }
                is RefreshOutcome.TryLater -> return CoachingResult.Failed(0, outcome.code)
                else -> return CoachingResult.SignedOut
            }
        } else first
        if (reply.status == 401) return CoachingResult.SignedOut
        if (reply.status !in 200..299) return CoachingResult.Failed(reply.status, CoachingJson.errorCode(reply.body) ?: NETWORK)
        return runCatching { parse(reply.body.orEmpty()) }.fold(
            onSuccess = { CoachingResult.Ok(it) },
            onFailure = { CoachingResult.Failed(reply.status, "INVALID_RESPONSE") },
        )
    }

    /** 액세스가 없으면 갱신해 본다. 갱신 결과로 로그인 상태를 판단한다 */
    private suspend fun signedOutOrRetry(): CoachingResult<Nothing> = when (val outcome = auth.refresh()) {
        is RefreshOutcome.TryLater -> CoachingResult.Failed(0, outcome.code)
        else -> CoachingResult.SignedOut
    }

    private data class Reply(val status: Int, val body: String?)

    private fun send(path: String, token: String): Reply {
        val conn = URL("$base$path").openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 10_000
        conn.readTimeout = 15_000
        conn.setRequestProperty("Accept", "application/json")
        conn.setRequestProperty("Authorization", "Bearer $token")
        return try {
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            Reply(status, stream?.bufferedReader()?.use { it.readText() })
        } catch (_: IOException) {
            Reply(0, null)
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        const val NETWORK = "NETWORK"
    }
}
