package io.candiy.healthconnect.quickstart.wear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.health.services.client.PassiveListenerService
import androidx.health.services.client.data.DataPointContainer
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull

/** Health Services 가 패시브 데이터를 넘길 때 시스템이 깨우는 서비스. 앱이 꺼져 있어도 불린다. */
class PassiveDataService : PassiveListenerService() {
    override fun onNewDataPointsReceived(dataPoints: DataPointContainer) {
        FileLog.init(this)
        PassiveCollector.onData(this, dataPoints)
        // 15분이 지났으면 지금 보낸다 (예약 작업이 밀려도 주기가 유지되게)
        runBlocking { withTimeoutOrNull(30_000) { PassiveCollector.flushIfDue(this@PassiveDataService) } }
    }
}

/** 15분마다 대기열을 폰으로 보낸다. 폰이 없으면 다음 주기로 미룬다. */
class PassiveFlushWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        PassiveCollector.flush(applicationContext)
        return Result.success()
    }
}

/** 재부팅하면 패시브 등록이 풀리므로 다시 건다. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                PassiveCollector.start(context)
            } catch (e: Exception) {
                FileLog.init(context)
                FileLog.e("CandiyPassive", "boot_register_failed", e)
            } finally {
                pending.finish()
            }
        }
    }
}
