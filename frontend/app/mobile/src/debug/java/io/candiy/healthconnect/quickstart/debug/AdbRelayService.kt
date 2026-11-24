package io.candiy.healthconnect.quickstart.debug

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import kotlin.concurrent.thread

/**
 * (디버그 빌드 전용) 노트북의 adb 를 폰을 거쳐 워치 무선 디버깅에 이어 주는 TCP 중계.
 *
 * 교육장에서는 노트북이 보안 와이파이에만 붙을 수 있어 워치와 같은 네트워크에 둘 수 없다.
 * 대신 폰은 USB 로 노트북에, 와이파이로 워치에 붙어 있으니 폰이 둘을 이어 준다.
 *
 *   노트북 adb ─USB(adb forward)─▶ 폰 127.0.0.1:포트 ─와이파이─▶ 워치 IP:포트
 *
 * 폰 안의 127.0.0.1 에서만 연결을 받는다(같은 네트워크의 다른 기기는 접속 불가).
 * 시작·종료는 adb shell 에서만 가능하다(DUMP 권한 요구). tools/watch_adb.sh 가 이 서비스를 부른다.
 *
 *   map 형식: "로컬포트=워치IP:포트,로컬포트=워치IP:포트"  (연결용, 페어링용)
 */
class AdbRelayService : Service() {

    private val servers = mutableListOf<ServerSocket>()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopRelays()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        startForeground(1001, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        stopRelays()
        intent?.getStringExtra("map").orEmpty().split(",").filter { it.contains("=") }.forEach { entry ->
            val (local, target) = entry.trim().split("=", limit = 2)
            val (host, port) = target.split(":", limit = 2)
            startRelay(local.toInt(), host, port.toInt())
        }
        return START_NOT_STICKY
    }

    private fun startRelay(localPort: Int, host: String, port: Int) {
        val server = ServerSocket(localPort, 8, InetAddress.getLoopbackAddress())
        servers += server
        Log.i(TAG, "relay_started local=$localPort target=$host:$port")
        thread(name = "relay-$localPort", isDaemon = true) {
            while (!server.isClosed) {
                val inbound = try { server.accept() } catch (_: Exception) { break }
                thread(isDaemon = true) {
                    try {
                        val outbound = Socket().apply { connect(InetSocketAddress(host, port), 5_000) }
                        Log.i(TAG, "relay_connected local=$localPort")
                        pipe(inbound, outbound)
                        pipe(outbound, inbound)
                    } catch (e: Exception) {
                        Log.e(TAG, "relay_connect_failed local=$localPort reason=${e.javaClass.simpleName}")
                        inbound.close()
                    }
                }
            }
        }
    }

    private fun pipe(from: Socket, to: Socket) = thread(isDaemon = true) {
        try {
            from.getInputStream().copyTo(to.getOutputStream())
        } catch (_: Exception) {
        } finally {
            runCatching { from.close() }
            runCatching { to.close() }
        }
    }

    private fun stopRelays() {
        servers.forEach { runCatching { it.close() } }
        servers.clear()
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "워치 디버깅 중계", NotificationManager.IMPORTANCE_LOW))
        }
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("워치 디버깅 중계 중 (개발용)")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        stopRelays()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "CandiyAdbRelay"
        private const val CHANNEL = "adb_relay"
        const val ACTION_STOP = "io.candiy.relay.STOP"
    }
}
