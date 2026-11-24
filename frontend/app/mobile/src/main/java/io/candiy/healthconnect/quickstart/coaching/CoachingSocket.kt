package io.candiy.healthconnect.quickstart.coaching

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.channels.trySendBlocking
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * 코칭 WebSocket(명세 7절). 질문 하나마다 연결을 열어 auth → ready → question 을 보내고, completed·error 를 받으면 닫는다.
 * 연결이 끊겨도 서버는 접수한 요청을 계속 만들므로(7.5) 화면은 요청 상태 조회(6.4)로 이어서 확인한다.
 *
 * OkHttp 를 쓰는 이유: TLS 위 WebSocket(핸드셰이크·마스킹·프레임·핑)을 직접 짜면 보안 실수 위험이 크고,
 * OkHttp 는 CANDiY SDK 가 이미 앱에 넣고 있어 용량이 늘지 않는다. WebSocket 에만 쓰고 REST 는 직접 부른다([CoachingApi]).
 */
class CoachingSocket(baseUrl: String) {
    private val url = baseUrl.trimEnd('/').replaceFirst("https://", "wss://").replaceFirst("http://", "ws://") + "/api/v1/coaching/ws"

    // 답변 생성은 접수 후 최대 120초(명세 7.5). 읽기 시간 제한은 두지 않고 핑으로 끊김을 알아챈다
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    /**
     * 질문 한 건을 보내고 서버 이벤트를 흘려보낸다. ready 전에 연결이 끊기면 Closed(afterReady=false),
     * ready 뒤 끊기면 Closed(afterReady=true) 로 끝난다. completed·error·connection_error 뒤에는 연결을 닫고 끝낸다.
     */
    fun ask(accessToken: String, clientRequestId: String, conversationId: String?, content: String): Flow<SocketEvent> = callbackFlow {
        var ready = false
        var finished = false
        val socket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                webSocket.send(CoachingJson.auth(accessToken))
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val event = CoachingJson.event(text) ?: return
                if (event is SocketEvent.Ready) {
                    ready = true
                    webSocket.send(CoachingJson.question(clientRequestId, conversationId, content))
                }
                trySendBlocking(event)
                // request_snapshot 은 진행 중이어도 이 연결로 이벤트가 더 오지 않는다(7.6) — 닫고 상태 조회로 이어 간다
                val ends = event is SocketEvent.Completed || event is SocketEvent.ConnectionError ||
                    event is SocketEvent.Failed || event is SocketEvent.Snapshot
                if (ends) {
                    finished = true
                    webSocket.close(1000, null)
                    channel.close()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = end()

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = end()

            private fun end() {
                if (!finished) {
                    finished = true
                    trySendBlocking(SocketEvent.Closed(afterReady = ready))
                }
                channel.close()
            }
        })
        awaitClose { socket.cancel() }
    }
}
