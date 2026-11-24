package io.candiy.healthconnect.quickstart.wear

import android.content.Context
import android.os.SystemClock
import androidx.health.services.client.HealthServices
import androidx.health.services.client.data.DataPointContainer
import androidx.health.services.client.data.DataType
import androidx.health.services.client.data.PassiveListenerConfig
import androidx.health.services.client.getCapabilities
import androidx.health.services.client.setPassiveListenerService
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.concurrent.TimeUnit

/**
 * 워치 자동 수집. 사용자가 앱을 열거나 버튼을 누르지 않아도 동작한다.
 *
 *  1) 수집: Health Services 패시브 수집이 걸음·거리·칼로리(하루 누적값)를 시스템이 정한 간격으로 넘겨준다.
 *     심박도 직접 받는다 — 걸음·거리·칼로리·심박은 워치 앱이 맡고, 삼성 헬스는 수면·산소포화도만 맡는다(10-19 결정).
 *     삼성 헬스도 같은 지표를 Health Connect 에 넣으므로, 백엔드는 이 지표들을 우리 앱 출처만 쓴다.
 *     앱이 꺼져 있어도 시스템이 [PassiveDataService] 를 깨워 전달한다.
 *  2) 묶음: 하루 누적값은 "지난번 보낸 값과의 차이"로 바꿔 15분 안팎의 구간 기록(시작·끝·값)으로 만든다.
 *     이 구간은 "시간대별 흐름" 용이다. 하루 합계는 삼성 헬스의 하루 총합 기록을 쓴다(백엔드 규칙).
 *  3) 전송: 15분마다(WorkManager) 블루투스로 직접 붙어 있는 폰에만 보낸다(Google 클라우드 경유 없음).
 *     폰이 Health Connect 에 저장했다고 답한 묶음만 대기열에서 지운다.
 *     폰이 없거나 멀리 있으면 워치 안의 대기열에 그대로 두었다가 다음 주기에 다시 보낸다.
 *
 * 폰 앱은 받은 구간을 원래 측정 시각 그대로 Health Connect 에 쓰고, CANDiY SDK 가 15분 주기로 서버에 보낸다.
 * 로그에는 건강 수치를 남기지 않는다(건수·상태만).
 */
object PassiveCollector {

    private const val TAG = "CandiyPassive"
    // 폰 앱 WatchWalkReceiver 와 같은 경로. 묶음을 보내면 폰이 Health Connect 에 저장한 뒤 SAVED 로 답한다
    private const val BATCH_PATH = "/candiy/passive/batch"
    private const val SAVED = "saved"
    private const val REQUEST_TIMEOUT_MS = 30_000L
    private const val WORK = "candiy_passive_flush"
    private const val MAX_MESSAGE = 90 * 1024
    // 폰에 오래 못 보낼 때 심박 임시 파일 상한(약 5만 줄). 줄을 세려면 파일을 다 읽어야 해서 크기로 본다
    private const val MAX_HR_BYTES = 1_000_000L
    private val zone: ZoneId get() = ZoneId.systemDefault()

    // 하루 누적값으로 오는 종류 → 대기열에 쓰는 이름
    private val DAILY = linkedMapOf(
        DataType.STEPS_DAILY to "steps",
        DataType.DISTANCE_DAILY to "distance",
        DataType.CALORIES_DAILY to "calories",
    )

    /** 패시브 수집 등록 + 15분 전송 작업 예약. 앱 실행·재부팅 때 부른다(여러 번 불러도 안전). */
    suspend fun start(context: Context) {
        FileLog.init(context)
        val client = HealthServices.getClient(context).passiveMonitoringClient
        val supported = client.getCapabilities().supportedDataTypesPassiveMonitoring
        val types = (DAILY.keys + DataType.HEART_RATE_BPM).filter { it in supported }.toSet()
        client.setPassiveListenerService(
            PassiveDataService::class.java,
            PassiveListenerConfig.builder().setDataTypes(types).build(),
        )
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            WORK, ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<PassiveFlushWorker>(15, TimeUnit.MINUTES).build(),
        )
        prefs(context).edit().putBoolean("registered", true).apply()
        FileLog.i(TAG, "passive_registered types=${types.map { it.name }}")
    }

    /** 시스템이 넘겨준 데이터를 워치 안에 쌓는다. */
    @Synchronized
    fun onData(context: Context, data: DataPointContainer) {
        val boot = Instant.ofEpochMilli(System.currentTimeMillis() - SystemClock.elapsedRealtime())
        DAILY.forEach { (type, key) ->
            data.getData(type).forEach { p -> onDaily(context, key, (p.value as Number).toDouble(), p.getEndInstant(boot).toEpochMilli()) }
        }
        val hr = data.getData(DataType.HEART_RATE_BPM)
        if (hr.isNotEmpty()) {
            val f = hrFile(context)
            val lines = hr.joinToString("") { "${it.getTimeInstant(boot).toEpochMilli()},${it.value.toLong()}\n" }
            if (f.length() < MAX_HR_BYTES) f.appendText(lines)
        }
        prefs(context).edit().putLong("lastDataMs", System.currentTimeMillis()).apply()
        val grew = DAILY.values.joinToString(" ") { k -> "$k+=${grewFlags.remove(k) ?: "-"}" }
        FileLog.i(TAG, "passive_data daily=${DAILY.keys.sumOf { data.getData(it).size }} hr=${hr.size} $grew")
    }

    // 이번 묶음에서 누적값이 늘었는지(로그용, 수치는 남기지 않는다)
    private val grewFlags = mutableMapOf<String, Boolean>()

    private fun onDaily(context: Context, key: String, value: Double, endMs: Long) {
        val p = prefs(context)
        grewFlags[key] = (grewFlags[key] ?: false) || value > p.getFloat("${key}_cur", Float.MAX_VALUE)
        // LocalDate.ofInstant 는 API 34부터라 Wear OS 3·4(API 30~33) 워치에서 멈춘다. atZone 은 모든 버전에 있다
        val day = Instant.ofEpochMilli(endMs).atZone(zone).toLocalDate().toString()
        val storedDay = p.getString("${key}_day", null)
        if (storedDay == null) {
            // 처음 받은 값은 기준점으로만 쓴다(언제부터 센 값인지 알 수 없어서)
            p.edit().putString("${key}_day", day).putFloat("${key}_sent", value.toFloat()).putLong("${key}_sentMs", endMs)
                .putFloat("${key}_cur", value.toFloat()).putLong("${key}_curMs", endMs).apply()
            return
        }
        if (day != storedDay) {
            // 날짜가 바뀌었다: 어제 남은 몫을 닫고, 오늘은 0시부터 센다
            closeInterval(context, key)
            val midnight = LocalDate.parse(day).atStartOfDay(zone).toInstant().toEpochMilli()
            p.edit().putString("${key}_day", day).putFloat("${key}_sent", 0f).putLong("${key}_sentMs", midnight).apply()
        } else if (value < p.getFloat("${key}_sent", 0f)) {
            // 같은 날인데 누적값이 줄었다(워치 재시작 등): 0부터 다시 센 것으로 본다
            p.edit().putFloat("${key}_sent", 0f).apply()
        }
        p.edit().putFloat("${key}_cur", value.toFloat()).putLong("${key}_curMs", endMs).apply()
    }

    /** 지난번 보낸 값 이후 늘어난 만큼을 구간 하나로 대기열에 넣는다. */
    private fun closeInterval(context: Context, key: String) {
        val p = prefs(context)
        val sent = p.getFloat("${key}_sent", 0f).toDouble()
        val cur = p.getFloat("${key}_cur", 0f).toDouble()
        val sentMs = p.getLong("${key}_sentMs", 0)
        val curMs = p.getLong("${key}_curMs", 0)
        if (cur - sent > 0 && curMs > sentMs) {
            enqueue(context, JSONObject().put("type", key).put("start", sentMs).put("end", curMs).put("value", cur - sent))
        }
        p.edit().putFloat("${key}_sent", cur.toFloat()).putLong("${key}_sentMs", curMs).apply()
    }

    /**
     * 15분마다 부른다. 구간을 닫고, 블루투스로 붙어 있는 폰이 있으면 대기열을 보낸다.
     * 15분 작업과 데이터 수신(flushIfDue)·화면의 "지금 보내기"가 겹쳐도 한 번에 하나만 돈다.
     * 둘이 같은 대기열을 함께 보내고 각자 앞에서 지우면 아직 안 보낸 기록이 지워지기 때문이다.
     */
    suspend fun flush(context: Context): String = flushLock.withLock { flushLocked(context) }

    private val flushLock = Mutex()

    private suspend fun flushLocked(context: Context): String {
        FileLog.init(context)
        synchronized(this) {
            DAILY.values.forEach { closeInterval(context, it) }
            moveHeartRate(context)
        }
        val queue = synchronized(this) { readQueue(context) }
        val p = prefs(context)
        if (queue.length() == 0) {
            p.edit().putLong("lastFlushMs", System.currentTimeMillis()).putString("lastResult", "보낼 것 없음").apply()
            return "empty"
        }
        val nodes = runCatching { Wearable.getNodeClient(context).connectedNodes.await().filter { it.isNearby } }.getOrDefault(emptyList())
        if (nodes.isEmpty()) {
            p.edit().putLong("lastFlushMs", System.currentTimeMillis()).putString("lastResult", "폰 없음 · ${queue.length()}건 대기").apply()
            FileLog.i(TAG, "flush_deferred reason=no_nearby_phone queued=${queue.length()}")
            return "deferred"
        }
        // 묶음마다 폰이 Health Connect 에 저장했다고 답해야(요청·응답) 그만큼 대기열에서 지운다.
        // 메시지가 닿기만 하고 폰 저장이 실패하면 예전에는 워치 쪽 기록까지 사라졌다
        var savedItems = 0
        return try {
            val phone = nodes.first().id
            for (chunk in chunks(queue)) {
                val body = JSONObject().put("items", chunk).toString().toByteArray()
                val reply = withTimeout(REQUEST_TIMEOUT_MS) { Wearable.getMessageClient(context).sendRequest(phone, BATCH_PATH, body).await() }
                if (String(reply) != SAVED) throw IllegalStateException("phone_not_saved")
                synchronized(this) { dropFromQueue(context, chunk.length()) }
                savedItems += chunk.length()
            }
            p.edit().putLong("lastFlushMs", System.currentTimeMillis()).putLong("lastSentMs", System.currentTimeMillis())
                .putString("lastResult", "전송 ${savedItems}건").apply()
            val byType = (0 until queue.length()).groupingBy { queue.getJSONObject(it).optString("type") }.eachCount()
            FileLog.i(TAG, "flush_saved items=$savedItems byType=$byType")
            "sent"
        } catch (e: Exception) {
            // 저장 확인을 받은 묶음만 지웠으므로, 남은 것은 다음 주기에 다시 보낸다
            val left = queue.length() - savedItems
            p.edit().putLong("lastFlushMs", System.currentTimeMillis())
                .putString("lastResult", "전송 실패 · ${left}건 다음에 다시").apply()
            FileLog.e(TAG, "flush_failed saved=$savedItems left=$left", e)
            "failed"
        }
    }

    /**
     * 데이터를 받을 때 불린다. 마지막 전송 후 15분이 지났으면 바로 보낸다.
     * Wear OS 는 자주 안 여는 앱의 예약 작업(WorkManager)을 오래 미루기 때문에,
     * Health Services 가 깨워 줄 때(약 5~10분 간격) 함께 보내는 쪽이 15분 주기에 더 가깝다.
     */
    suspend fun flushIfDue(context: Context) {
        val last = prefs(context).getLong("lastFlushMs", 0)
        if (System.currentTimeMillis() - last >= 15 * 60_000L) flush(context)
    }

    /** 화면 표시용 한 줄: 워치가 폰으로 마지막 보낸 시각과 마지막 결과(건수·대기). 건강 수치는 없다. */
    fun summary(context: Context): String {
        val p = prefs(context)
        if (!p.getBoolean("registered", false)) return "자동 수집 준비 중"
        val sent = p.getLong("lastSentMs", 0)
        val at = if (sent == 0L) "아직 없음" else Instant.ofEpochMilli(sent).atZone(zone).toLocalTime().toString().take(5)
        val result = p.getString("lastResult", "").orEmpty()
        return if (result.isEmpty()) "워치 → 폰 $at" else "워치 → 폰 $at · $result"
    }


    private fun chunks(queue: JSONArray): List<JSONArray> {
        val out = mutableListOf<JSONArray>()
        var cur = JSONArray()
        var size = 0
        for (i in 0 until queue.length()) {
            val item = queue.getJSONObject(i)
            val len = item.toString().length
            if (size + len > MAX_MESSAGE && cur.length() > 0) { out += cur; cur = JSONArray(); size = 0 }
            cur.put(item); size += len
        }
        if (cur.length() > 0) out += cur
        return out
    }

    private fun enqueue(context: Context, item: JSONObject) {
        val q = readQueue(context).put(item)
        queueFile(context).writeText(q.toString())
    }

    private fun dropFromQueue(context: Context, n: Int) {
        val q = readQueue(context)
        val rest = JSONArray()
        for (i in n until q.length()) rest.put(q.get(i))
        queueFile(context).writeText(rest.toString())
    }

    private fun readQueue(context: Context): JSONArray =
        queueFile(context).takeIf { it.exists() }?.readText()?.let { runCatching { JSONArray(it) }.getOrNull() } ?: JSONArray()

    private fun queueFile(c: Context) = File(c.filesDir, "passive_queue.json")
    /** 모아 둔 심박 샘플을 대기열로 옮긴다. 메시지 크기 제한 때문에 2,000개씩 나눈다. */
    private fun moveHeartRate(context: Context) {
        val f = hrFile(context)
        if (!f.exists()) return
        val samples = f.readLines().mapNotNull { line ->
            line.split(",").takeIf { it.size == 2 }?.let { JSONArray().put(it[0].toLong()).put(it[1].toLong()) }
        }
        samples.chunked(2000).forEach { part -> enqueue(context, JSONObject().put("type", "hr").put("samples", JSONArray(part))) }
        f.delete()
    }

    private fun hrFile(c: Context) = File(c.filesDir, "passive_hr.csv")
    private fun prefs(c: Context) = c.getSharedPreferences("candiy_passive", Context.MODE_PRIVATE)
}
