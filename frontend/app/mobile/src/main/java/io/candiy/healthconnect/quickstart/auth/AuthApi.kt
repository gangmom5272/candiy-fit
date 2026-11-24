package io.candiy.healthconnect.quickstart.auth

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** HTTP 한 번의 결과. 연결 실패와 응답 유실을 나눈다 — 갱신 토큰은 응답을 잃으면 다시 보내면 안 된다(명세 3.5) */
sealed interface HttpReply {
    data class Received(val status: Int, val body: String?) : HttpReply
    data object NotSent : HttpReply
    data object NoResponse : HttpReply
}

fun interface HttpTransport {
    /** IO 스레드에서 부른다 */
    fun postJson(path: String, body: JSONObject): HttpReply
}

/** 앱의 다른 호출과 같은 HttpURLConnection 방식. 자동 재시도는 하지 않는다 */
class UrlConnectionTransport(baseUrl: String) : HttpTransport {
    private val base = baseUrl.trimEnd('/')

    override fun postJson(path: String, body: JSONObject): HttpReply {
        val bytes = body.toString().toByteArray()
        val conn = URL("$base$path").openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.doOutput = true
        // 길이를 정해 바로 흘려보낸다. 길이를 모르면 내장 HTTP 구현이 본문을 모아 두었다가 연결이 끊기면
        // 같은 POST 를 몰래 다시 보낼 수 있다 — 갱신 토큰이 두 번 가면 서버가 재사용으로 보고 세션을 폐기한다
        conn.setFixedLengthStreamingMode(bytes.size)
        conn.connectTimeout = 10_000
        conn.readTimeout = 15_000
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setRequestProperty("Accept", "application/json")
        try {
            conn.connect()
        } catch (_: IOException) {
            conn.disconnect()
            return HttpReply.NotSent
        }
        return try {
            conn.outputStream.use { it.write(bytes) }
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            HttpReply.Received(status, stream?.bufferedReader()?.use { it.readText() })
        } catch (_: IOException) {
            // 본문을 쓰기 시작한 뒤의 실패는 서버가 받았는지 알 수 없다
            HttpReply.NoResponse
        } finally {
            conn.disconnect()
        }
    }
}

/** 명세 3.2~3.6 의 ANDROID 요청. 저장은 하지 않는다 — 토큰 보관·갱신 순서는 [AuthSession] 이 맡는다 */
class AuthApi(private val http: HttpTransport) {

    suspend fun challenge(social: SocialType): ApiResult<Challenge> =
        post("/api/v1/auth/social/challenge", AuthJson.challengeBody(social), AuthJson::challenge)

    suspend fun login(social: SocialType, idToken: String, challengeToken: String): ApiResult<LoginResult> =
        post("/api/v1/auth/social/login", AuthJson.loginBody(social, idToken, challengeToken), AuthJson::login)

    /** 201(새 회원)·200(같은 계정이 먼저 가입됨) 모두 로그인 성공이다 */
    suspend fun signup(signupToken: String, profile: SignupProfile): ApiResult<Session> =
        post("/api/v1/auth/signup", AuthJson.signupBody(signupToken, profile), AuthJson::session)

    suspend fun refresh(refreshToken: String): ApiResult<Session> =
        post("/api/v1/auth/refresh", AuthJson.refreshBody(refreshToken), AuthJson::session)

    /** 204 본문 없음 */
    suspend fun logout(refreshToken: String): ApiResult<Unit> =
        post("/api/v1/auth/logout", AuthJson.refreshBody(refreshToken)) { }

    private suspend fun <T> post(path: String, body: JSONObject, parse: (JSONObject) -> T): ApiResult<T> =
        withContext(Dispatchers.IO) {
            when (val reply = http.postJson(path, body)) {
                HttpReply.NotSent -> ApiResult.NotSent
                HttpReply.NoResponse -> ApiResult.NoResponse
                is HttpReply.Received ->
                    if (reply.status in 200..299) {
                        runCatching { parse(JSONObject(reply.body?.ifBlank { null } ?: "{}")) }.fold(
                            onSuccess = { ApiResult.Ok(it) },
                            // 성공 상태인데 읽을 수 없으면 서버 쪽은 처리를 끝냈을 수 있다
                            onFailure = { ApiResult.Rejected(reply.status, AuthJson.INVALID_RESPONSE) },
                        )
                    } else {
                        AuthJson.error(reply.status, reply.body)
                    }
            }
        }
}
