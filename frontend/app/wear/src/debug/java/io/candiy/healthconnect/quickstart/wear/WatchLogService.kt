package io.candiy.healthconnect.quickstart.wear

import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.tasks.await

/**
 * (디버그 빌드 전용) 폰이 로그를 요청하면 워치 로그 파일을 폰으로 돌려보낸다.
 *
 * 교육장처럼 워치와 노트북이 같은 와이파이에 있을 수 없는 곳에서 쓰는 경로다.
 *   노트북 ─USB─ 폰 ─블루투스(Data Layer 메시지)─ 워치
 * 요청을 보낸 노드(블루투스로 붙어 있는 폰)에게만 답한다. 로그에는 건강 수치가 없다.
 */
class WatchLogService : WearableListenerService() {

    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != PATH_REQUEST) return
        FileLog.init(this)
        runBlocking {
            try {
                val body = FileLog.tailBytes(MAX_BYTES)
                Wearable.getMessageClient(this@WatchLogService).sendMessage(event.sourceNodeId, PATH_DATA, body).await()
                FileLog.i(TAG, "log_sent bytes=${body.size}")
            } catch (e: Exception) {
                FileLog.e(TAG, "log_send_failed", e)
            }
        }
    }

    companion object {
        private const val TAG = "CandiyWearLog"
        const val PATH_REQUEST = "/candiy/log/request"
        const val PATH_DATA = "/candiy/log/data"
        // Data Layer 메시지는 100KB 남짓이 한계라 여유를 둔다
        private const val MAX_BYTES = 90 * 1024
    }
}
