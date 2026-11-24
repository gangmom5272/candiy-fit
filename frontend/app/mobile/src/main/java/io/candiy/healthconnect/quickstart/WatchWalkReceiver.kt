package io.candiy.healthconnect.quickstart

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.metadata.Device
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.units.Energy
import androidx.health.connect.client.units.Length
import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import com.google.android.gms.wearable.DataEvent
import com.google.android.gms.wearable.DataEventBuffer
import com.google.android.gms.wearable.DataMap
import com.google.android.gms.wearable.DataMapItem
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * 워치 앱(wear 모듈)이 블루투스 메시지 `/candiy/passive` 로 보내는 것을 받아 Health Connect 에 쓴다.
 * 워치 화면이 `/candiy/status/request` 로 물으면 네 지표의 전송 상태를 `/candiy/status` 로 답한다.
 *  - 15분 자동 수집분: 걸음·거리·칼로리 구간, 심박 샘플
 *  - 직접 시작한 걷기 기록(`walk`): 운동 시간대 + 운동 중 심박. 걸음·거리·칼로리는 자동 수집 구간에 이미 있어 쓰지 않는다(이중 집계 방지)
 *
 * DataItem `/candiy/walk/...` 은 예전 워치 빌드가 남긴 기록을 받기 위해서만 남겨 둔다
 * (DataItem 은 Google 클라우드를 거칠 수 있어 새 워치 빌드는 쓰지 않는다). 여기서도 걸음·거리·칼로리는 버린다.
 *
 * 걷기 기록을 쓰고 나면 CANDiY HC SDK 동기화를 한 번 다시 등록해 바로 서버로 보내게 한다.
 * SDK 입장에서는 출처(app)가 이 앱 패키지인 Health Connect 기록일 뿐이라 SDK·API 는 그대로다.
 *
 * clientRecordId 를 운동 시작 시각으로 고정해, 같은 DataItem 이 다시 와도 덮어써질 뿐 늘어나지 않는다.
 * 로그에는 건강 수치를 남기지 않는다.
 */
class WatchWalkReceiver : WearableListenerService() {

    override fun onCreate() {
        super.onCreate()
        FileLog.init(this)
    }

    override fun onDataChanged(events: DataEventBuffer) {
        events.filter { it.type == DataEvent.TYPE_CHANGED && it.dataItem.uri.path?.startsWith(PATH_PREFIX) == true }
            .map { DataMapItem.fromDataItem(it.dataItem).dataMap }
            .forEach { map ->
                runBlocking {
                    try {
                        val records = toRecords(map)
                        HealthConnectClient.getOrCreate(this@WatchWalkReceiver).insertRecords(records)
                        FileLog.i(TAG, "watch_walk_inserted records=${records.size} types=${records.map { it::class.simpleName }}")
                        restartSync()
                    } catch (e: Exception) {
                        FileLog.e(TAG, "watch_walk_insert_failed", e)
                    }
                }
            }
    }

    /**
     * 15분 자동 수집분(요청·응답). 워치는 블루투스로 직접 붙어 있을 때만 보낸다.
     * Health Connect 저장까지 끝나야 [SAVED] 로 답하고, 실패하면 오류로 답해 워치가 대기열을 지우지 않게 한다.
     */
    override fun onRequest(nodeId: String, path: String, request: ByteArray): Task<ByteArray>? {
        if (path != PASSIVE_BATCH_PATH) return null
        return if (savePassive(request)) Tasks.forResult(SAVED.toByteArray())
        else Tasks.forException(IllegalStateException("passive_not_saved"))
    }

    /** 예전 워치 빌드의 15분 자동 수집분(답 없는 메시지)과 워치 화면의 상태 요청 */
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path == STATUS_REQUEST_PATH) return replyStatus(event.sourceNodeId)
        if (event.path == PASSIVE_PATH) savePassive(event.data)
    }

    /** 워치 묶음을 Health Connect 에 쓴다. 저장했으면 true. 워치에서 마지막으로 받은 시각도 남긴다 */
    private fun savePassive(data: ByteArray): Boolean = runBlocking {
        try {
            val records = passiveRecords(JSONObject(String(data)))
            if (records.isNotEmpty()) HealthConnectClient.getOrCreate(this@WatchWalkReceiver).insertRecords(records)
            markReceived(this@WatchWalkReceiver)
            FileLog.i(TAG, "passive_inserted records=${records.size} types=${records.groupingBy { it::class.simpleName }.eachCount()}")
            // 걷기 기록이 들어 있으면 15분을 기다리지 않고 바로 보낸다
            if (records.any { it is ExerciseSessionRecord }) restartSync()
            true
        } catch (e: Exception) {
            FileLog.e(TAG, "passive_insert_failed", e)
            false
        }
    }

    /**
     * 워치 대기열 항목 → Health Connect 기록. clientRecordId 를 종류·시작 시각으로 고정해
     * 같은 항목이 다시 와도 덮어써질 뿐 늘어나지 않는다. 시각은 워치가 측정한 원래 시각이다.
     */
    private fun passiveRecords(body: JSONObject): List<Record> {
        val items = body.optJSONArray("items") ?: return emptyList()
        val watch = Device(type = Device.TYPE_WATCH)
        fun meta(id: String) = Metadata.autoRecorded(device = watch, clientRecordId = "candiy-passive-$id", clientRecordVersion = 1)
        val out = mutableListOf<Record>()
        for (i in 0 until items.length()) {
            val it = items.getJSONObject(i)
            val type = it.optString("type")
            if (type == "walk") {
                val start = Instant.ofEpochMilli(it.getLong("start"))
                val end = Instant.ofEpochMilli(it.getLong("end"))
                if (end.isAfter(start)) out += walkSession(start, end, it.optString("source"))
                continue
            }
            if (type == "hr") {
                val arr = it.getJSONArray("samples")
                // 범위 밖 값(착용이 느슨할 때 오는 0 등)은 Sample 을 만들기 전에 거른다. Sample 은 1~300 이 아니면
                // 만들 때 예외를 던져, 먼저 만들면 같은 묶음의 걸음·거리·칼로리까지 통째로 저장되지 않는다
                val samples = (0 until arr.length()).map { k -> arr.getJSONArray(k) }
                    .filter { s -> s.getLong(1) in 1..300 }
                    .map { s -> HeartRateRecord.Sample(Instant.ofEpochMilli(s.getLong(0)), s.getLong(1)) }
                    .sortedBy { s -> s.time }
                    .distinctBy { s -> s.time }
                if (samples.isEmpty()) continue
                val start = samples.first().time
                val end = samples.last().time.plusSeconds(1)
                val off = ZoneId.systemDefault().rules.getOffset(start)
                // 걷기 중 심박(tag=walk)은 자동 수집 심박과 id 가 겹치지 않게 구분한다
                val tag = it.optString("tag").takeIf { t -> t.isNotEmpty() }?.let { t -> "$t-" }.orEmpty()
                out += HeartRateRecord(startTime = start, startZoneOffset = off, endTime = end, endZoneOffset = off,
                    samples = samples, metadata = meta("hr-$tag${start.toEpochMilli()}"))
                continue
            }
            val start = Instant.ofEpochMilli(it.getLong("start"))
            val end = Instant.ofEpochMilli(it.getLong("end"))
            val value = it.getDouble("value")
            if (!end.isAfter(start) || value <= 0) continue
            val off = ZoneId.systemDefault().rules.getOffset(start)
            val id = "$type-${start.toEpochMilli()}"
            when (type) {
                "steps" -> out += StepsRecord(startTime = start, startZoneOffset = off, endTime = end, endZoneOffset = off,
                    count = value.toLong().coerceIn(1, 1_000_000), metadata = meta(id))
                "distance" -> out += DistanceRecord(startTime = start, startZoneOffset = off, endTime = end, endZoneOffset = off,
                    distance = Length.meters(value), metadata = meta(id))
                "calories" -> out += TotalCaloriesBurnedRecord(startTime = start, startZoneOffset = off, endTime = end, endZoneOffset = off,
                    energy = Energy.kilocalories(value), metadata = meta(id))
            }
        }
        return out
    }

    /** 걷기 운동 시간대. id 는 시작 시각으로 고정해 예전 DataItem 경로로 온 같은 기록과도 겹치지 않는다(덮어쓰기). */
    private fun walkSession(start: Instant, end: Instant, source: String?): ExerciseSessionRecord {
        val offset = ZoneId.systemDefault().rules.getOffset(start)
        return ExerciseSessionRecord(
            startTime = start, startZoneOffset = offset, endTime = end, endZoneOffset = offset,
            exerciseType = ExerciseSessionRecord.EXERCISE_TYPE_WALKING,
            title = "워치 직접 기록 걷기", notes = source,
            metadata = Metadata.autoRecorded(device = Device(type = Device.TYPE_WATCH),
                clientRecordId = "candiy-watch-${start.toEpochMilli()}-session", clientRecordVersion = 1),
        )
    }

    /** 걷기 기록은 15분 주기를 기다리지 않고 바로 서버로 보낸다 (등록 해제 후 재등록 — [CandiySync.startNow]). */
    private fun restartSync() {
        CandiySync.startNow(this) { ok, msg ->
            FileLog.i(TAG, "sync_restarted ok=$ok msg=${LogRedaction.freeText(msg)}")
            FileLog.snapshotSdkLog()
        }
    }

    /**
     * 워치 화면이 열릴 때 보내는 상태 요청에 답한다. 홈의 네 지표(걸음·심박·산소포화도·수면)마다 서버 전송 상태를
     * 홈 지표 카드와 같은 판단([HomeStatus.watchLine])으로 짧게 보낸다. 권한 여부와 SDK 전송 로그만 읽고
     * 건강 기록·수치는 읽지 않는다. 워치는 화면을 열 때 한 번만 묻는다(배터리).
     */
    private fun replyStatus(nodeId: String) {
        runBlocking {
            try {
                val granted = runCatching {
                    HealthConnectClient.getOrCreate(this@WatchWalkReceiver).permissionController.getGrantedPermissions()
                }.getOrDefault(emptySet())
                val transfers = withContext(Dispatchers.IO) {
                    FileLog.snapshotSdkLog()
                    FileLog.transfers()
                }
                val today = LocalDate.now()
                val metrics = JSONArray()
                CandiySync.realTypes.filterKeys { it.name in HomeStatus.CORE }.forEach { (type, cls) ->
                    val status = MetricStatus(
                        key = type.name,
                        label = HomeStatus.CORE_LABELS[type.name].orEmpty(),
                        count24h = null,
                        lastMeasured = null,
                        permitted = HealthPermission.getReadPermission(cls) in granted,
                        transfer = transfers[type.name],
                    )
                    val (line, kind) = HomeStatus.watchLine(status, today)
                    metrics.put(JSONObject().put("key", status.key).put("label", status.label).put("line", line).put("kind", kind))
                }
                val body = JSONObject().put("at", System.currentTimeMillis()).put("metrics", metrics).toString().toByteArray()
                Wearable.getMessageClient(this@WatchWalkReceiver).sendMessage(nodeId, STATUS_PATH, body).await()
                FileLog.i(TAG, "watch_status_sent metrics=${metrics.length()}")
            } catch (e: Exception) {
                FileLog.e(TAG, "watch_status_failed", e)
            }
        }
    }

    /** 예전 워치 빌드의 DataItem. 걸음·거리·칼로리는 자동 수집 구간과 겹치므로 버리고 운동 시간대·심박만 쓴다. */
    private fun toRecords(map: DataMap): List<Record> {
        val start = Instant.ofEpochMilli(map.getLong("start"))
        val end = Instant.ofEpochMilli(map.getLong("end"))
        val offset = ZoneId.systemDefault().rules.getOffset(start)
        val watch = Device(type = Device.TYPE_WATCH)
        // 시작 시각이 비어 있던 예전 버그(1970년 기록)는 버린다
        if (start.toEpochMilli() <= 0 || !end.isAfter(start)) return emptyList()

        val out = mutableListOf<Record>(walkSession(start, end, map.getString("source")))
        val times = map.getLongArray("hrTimes") ?: LongArray(0)
        val bpm = map.getLongArray("hrBpm") ?: LongArray(0)
        // 범위 검사를 Sample 을 만들기 전에 한다(위 passiveRecords 와 같은 이유)
        val samples = times.indices
            .filter { bpm.getOrNull(it) in 1L..300L }
            .map { HeartRateRecord.Sample(Instant.ofEpochMilli(times[it]), bpm[it]) }
            .filter { !it.time.isBefore(start) && !it.time.isAfter(end) }
        if (samples.isNotEmpty()) {
            out += HeartRateRecord(startTime = start, startZoneOffset = offset, endTime = end, endZoneOffset = offset,
                samples = samples, metadata = Metadata.autoRecorded(device = watch,
                    clientRecordId = "candiy-watch-${start.toEpochMilli()}-hr", clientRecordVersion = 1))
        }
        return out
    }

    companion object {
        private const val TAG = "CandiyWatchRx"
        const val PATH_PREFIX = "/candiy/walk/"
        const val PASSIVE_PATH = "/candiy/passive"
        // 워치 자동 수집분 요청·응답 (wear 모듈 PassiveCollector 와 같은 경로·응답 값)
        const val PASSIVE_BATCH_PATH = "/candiy/passive/batch"
        const val SAVED = "saved"
        // 워치 화면 상태 요청·응답 (wear 모듈 StatusActivity 와 같은 경로)
        const val STATUS_REQUEST_PATH = "/candiy/status/request"
        const val STATUS_PATH = "/candiy/status"

        private fun prefs(c: Context) = c.getSharedPreferences("candiy_watch_link", Context.MODE_PRIVATE)
        private fun markReceived(c: Context) = prefs(c).edit().putLong("lastReceivedMs", System.currentTimeMillis()).apply()

        /** 워치 자동 수집분을 마지막으로 받아 저장한 시각(ms). 받은 적 없으면 null. 설정 화면의 워치 줄에 쓴다 */
        fun lastReceived(c: Context): Long? = prefs(c).getLong("lastReceivedMs", 0).takeIf { it > 0 }
    }
}
