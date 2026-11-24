package io.candiy.healthconnect.quickstart.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/*
 * (디버그 빌드 전용) 워치 로그를 블루투스로 받아 폰 파일에 저장한다. 워치 ADB 가 없어도 된다.
 *
 *   노트북 ─USB─ 폰 ─블루투스(Data Layer 메시지)─ 워치
 *
 *   1) adb shell am broadcast -n io.candiy.healthconnect.quickstart/.debug.WatchLogPullReceiver
 *   2) adb exec-out run-as io.candiy.healthconnect.quickstart cat files/watch_log.txt
 *
 * 요청은 블루투스로 직접 붙어 있는(isNearby) 워치에만 보낸다 — Google 클라우드 경유 전달을 쓰지 않기 위해서다.
 * 로그에는 건강 수치가 없다.
 */

private const val TAG = "CandiyWatchLog"
private const val PATH_REQUEST = "/candiy/log/request"
private const val PATH_DATA = "/candiy/log/data"

/** 노트북(adb shell)에서 부르는 진입점. DUMP 권한을 요구해 다른 앱은 부를 수 없다. */
class WatchLogPullReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val nodes = Wearable.getNodeClient(context).connectedNodes.await().filter { it.isNearby }
                nodes.forEach { Wearable.getMessageClient(context).sendMessage(it.id, PATH_REQUEST, ByteArray(0)).await() }
                Log.i(TAG, "log_requested nearbyNodes=${nodes.size}")
            } catch (e: Exception) {
                Log.e(TAG, "log_request_failed", e)
            } finally {
                pending.finish()
            }
        }
    }
}

/** 워치가 돌려준 로그를 files/watch_log.txt 에 저장한다(받을 때마다 최신 내용으로 덮어씀). */
class WatchLogSink : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != PATH_DATA) return
        val stamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("MM-dd HH:mm:ss"))
        File(filesDir, "watch_log.txt").writeBytes("# received $stamp from ${event.sourceNodeId}\n".toByteArray() + event.data)
        Log.i(TAG, "log_received bytes=${event.data.size}")
    }
}
