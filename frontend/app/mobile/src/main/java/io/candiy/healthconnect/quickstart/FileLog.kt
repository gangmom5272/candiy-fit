package io.candiy.healthconnect.quickstart

import android.content.Context
import android.util.Log
import java.io.File
import java.security.MessageDigest
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * 노트북 없이 다닌 뒤에도 동기화가 어떻게 됐는지 볼 수 있게, 앱 내부 파일에 로그를 남긴다.
 *
 *  - candiy_log.txt: 이 앱이 직접 남기는 줄 (512KB 넘으면 .1 로 넘김)
 *  - candiy_sdk_log.txt: CANDiY SDK 로그(candiy·CandiySync·CandiyAlarm·CandiyHC)를 이 앱 프로세스의
 *    logcat 에서 떠 온 것. SDK 는 파일 로그가 없고 손댈 수도 없어서, 앱이 실행되거나 워치 기록을 받을 때마다 떠 둔다.
 *
 * 꺼내기: adb exec-out run-as io.candiy.healthconnect.quickstart cat files/candiy_log.txt
 *        adb exec-out run-as io.candiy.healthconnect.quickstart cat files/candiy_sdk_log.txt
 *
 * 앱이 쓰는 줄에는 건강 수치를 넣지 않는다. 예외는 종류·발생 위치만 남긴다.
 * SDK 로그는 대부분 건수·상태지만 서버 오류 본문·예외 문구가 붙는 줄이 있어, 옮겨 적을 때 [LogRedaction.sdkLine]으로 가린다.
 */
object FileLog {
    private const val MAX_BYTES = 512 * 1024
    private val fmt = DateTimeFormatter.ofPattern("MM-dd HH:mm:ss")
    private val SDK_TAGS = setOf("candiy", "CandiySync", "CandiyAlarm", "CandiyHC", "CandiyWatchRx")
    private var dir: File? = null

    /**
     * 가리기(점검표 2-8) 전 버전이 남긴 로그. 예외 문구·서버 오류 본문이 그대로 들어 있을 수 있어
     * 덮어 설치 뒤 처음 시작할 때 한 번 지운다. watch_log.txt 는 디버그 빌드가 받아 둔 워치 로그 사본이다.
     */
    private val LEGACY_FILES = listOf(
        "candiy_log.txt", "candiy_log.1.txt",
        "candiy_sdk_log.txt", "candiy_sdk_log.1.txt", "candiy_sdk_log.last",
        "watch_log.txt",
    )
    private const val CLEANUP_PREFS = "candiy_file_log"
    private const val CLEANUP_KEY = "legacy_logs_cleared_v1"

    @Synchronized
    fun init(context: Context) {
        if (dir != null) return
        val app = context.applicationContext
        dir = app.filesDir
        val prefs = app.getSharedPreferences(CLEANUP_PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(CLEANUP_KEY, false)) {
            val removed = LEGACY_FILES.count { File(app.filesDir, it).delete() }
            prefs.edit().putBoolean(CLEANUP_KEY, true).apply()
            i("candiy", "legacy_logs_cleared removed=$removed")
        }
    }

    @Synchronized
    fun i(tag: String, msg: String) {
        Log.i(tag, msg)
        append("candiy_log.txt", "${LocalDateTime.now().format(fmt)} I/$tag $msg")
    }

    @Synchronized
    fun e(tag: String, msg: String, t: Throwable? = null) {
        // logcat에도 예외 문구를 남기지 않는다. "candiy" 태그는 SDK 로그 복사본에 함께 들어간다
        val line = if (t == null) msg else "$msg ${LogRedaction.throwable(t)}"
        Log.e(tag, line)
        append("candiy_log.txt", "${LocalDateTime.now().format(fmt)} E/$tag $line")
    }

    /**
     * logcat 에서 SDK 태그만 골라 파일에 덧붙인다. 앞서 떠 둔 줄은 건너뛴다.
     * 앱은 같은 앱(uid)이 남긴 로그만 읽을 수 있다 — 15분 주기 동기화처럼 지난 프로세스가 남긴 줄도 포함된다.
     * logcat 버퍼가 차면 오래된 줄은 사라지므로, 앱 실행·워치 기록 수신·동기화 직후마다 떠 둔다.
     */
    @Synchronized
    fun snapshotSdkLog() {
        val d = dir ?: return
        try {
            val lines = ProcessBuilder(listOf("logcat", "-d", "-v", "time") + SDK_TAGS.map { "$it:V" } + "*:S")
                .redirectErrorStream(true).start().inputStream.bufferedReader().readLines()
                .filter { line -> SDK_TAGS.any { line.contains("/$it(") || line.contains("/$it ") } }
            // 어디까지 옮겼는지는 원문 대신 해시로만 남긴다. 원문 줄을 파일에 두면 가리기를 거치지 않은 값이 남는다
            val marker = File(d, "candiy_sdk_log.last")
            val last = if (marker.exists()) marker.readText() else ""
            val idx = if (last.isEmpty()) -1 else lines.indexOfLast { sha256(it) == last }
            val fresh = lines.drop(idx + 1)
            fresh.forEach { append("candiy_sdk_log.txt", LogRedaction.sdkLine(it)) }
            lines.lastOrNull()?.let { marker.writeText(sha256(it)) }
            Log.i("CandiyFileLog", "sdk_log_snapshot appended=${fresh.size}")
        } catch (e: Exception) {
            Log.e("CandiyFileLog", "sdk_log_snapshot_failed", e)
        }
    }

    /** SDK 로그에서 마지막 동기화 결과(건수·상태만)를 찾는다. 없으면 null. */
    @Synchronized
    fun lastSync(): LastSync? {
        val line = File(dir ?: return null, "candiy_sdk_log.txt").takeIf { it.exists() }
            ?.readLines()?.lastOrNull { it.contains("job_finished") } ?: return null
        fun field(key: String) = Regex("\\b$key=(\\S+)").find(line)?.groupValues?.get(1)
        return LastSync(
            time = line.take(11).trim(), // logcat 시각 "MM-dd HH:mm"
            ok = field("result") == "success",
            status = field("status") ?: "-",
            sent = field("sent")?.toIntOrNull() ?: 0,
            failed = field("failed")?.toIntOrNull() ?: 0,
        )
    }

    data class LastSync(val time: String, val ok: Boolean, val status: String, val sent: Int, val failed: Int)

    /** 지표별 마지막 전송 결과 (TransferLog). 지난 로그 파일(.1)까지 읽어 오래 새 기록이 없던 지표도 마지막 전송 시각이 남는다 */
    @Synchronized
    fun transfers(): Map<String, TypeTransfer> {
        val d = dir ?: return emptyMap()
        val lines = listOf("candiy_sdk_log.1.txt", "candiy_sdk_log.txt")
            .map { File(d, it) }.filter { it.exists() }.flatMap { it.readLines() }
        return TransferLog.parse(lines)
    }

    /** 동기화 회차 목록 (SyncHistory). 지난 로그 파일(.1)까지 읽는다 */
    @Synchronized
    fun history(): List<SyncRun> {
        val d = dir ?: return emptyList()
        val lines = listOf("candiy_sdk_log.1.txt", "candiy_sdk_log.txt")
            .map { File(d, it) }.filter { it.exists() }.flatMap { it.readLines() }
        return SyncHistory.parse(lines)
    }

    private fun sha256(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray()).joinToString("") { "%02x".format(it) }

    private fun append(name: String, line: String) {
        val f = File(dir ?: return, name)
        try {
            if (f.exists() && f.length() > MAX_BYTES) f.renameTo(File(f.parentFile, name.replace(".txt", ".1.txt")))
            f.appendText(line + "\n")
        } catch (_: Exception) {
            // 로그 저장 실패가 동기화를 막으면 안 된다
        }
    }
}
