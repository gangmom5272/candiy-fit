package io.candiy.healthconnect.quickstart.wear

import android.content.Context
import android.util.Log
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * 노트북 없이 걸은 뒤에도 무슨 일이 있었는지 확인할 수 있게, logcat 과 함께 앱 내부 파일에도 남긴다.
 *
 * 파일: files/candiy_log.txt (512KB 넘으면 candiy_log.1.txt 로 넘기고 새로 시작)
 * 꺼내기: adb exec-out run-as io.candiy.healthconnect.quickstart cat files/candiy_log.txt
 *
 * 건강 수치는 쓰지 않는다 — 개수·여부·상태만 남긴다. 예외는 종류·발생 위치만 남긴다.
 */
object FileLog {
    private const val MAX_BYTES = 512 * 1024
    private val fmt = DateTimeFormatter.ofPattern("MM-dd HH:mm:ss")
    private var file: File? = null

    /** 가리기(점검표 2-8) 전 버전이 남긴 로그. 예외 문구가 들어 있을 수 있어 덮어 설치 뒤 처음 시작할 때 한 번 지운다. */
    private val LEGACY_FILES = listOf("candiy_log.txt", "candiy_log.1.txt")
    private const val CLEANUP_PREFS = "candiy_file_log"
    private const val CLEANUP_KEY = "legacy_logs_cleared_v1"

    @Synchronized
    fun init(context: Context) {
        if (file != null) return
        val app = context.applicationContext
        file = File(app.filesDir, "candiy_log.txt")
        val prefs = app.getSharedPreferences(CLEANUP_PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(CLEANUP_KEY, false)) {
            val removed = LEGACY_FILES.count { File(app.filesDir, it).delete() }
            prefs.edit().putBoolean(CLEANUP_KEY, true).apply()
            i("CandiyFileLog", "legacy_logs_cleared removed=$removed")
        }
    }

    @Synchronized
    fun i(tag: String, msg: String) {
        Log.i(tag, msg)
        write("I", tag, msg)
    }

    @Synchronized
    fun e(tag: String, msg: String, t: Throwable? = null) {
        val line = if (t == null) msg else "$msg ${describe(t)}"
        Log.e(tag, line)
        write("E", tag, line)
    }

    /**
     * 예외는 종류와 앱 코드 안 발생 위치(파일:줄)만 남긴다. 예외 문구에는 값이 들어갈 수 있다.
     * 기준: 반출 금지 점검표 2-8 (저장소 infra/data-export-checklist.md). 폰 앱의 LogRedaction.throwable과 같다.
     */
    private fun describe(t: Throwable): String {
        val frame = t.stackTrace.firstOrNull { it.className.startsWith("io.candiy.healthconnect") }
            ?: t.stackTrace.firstOrNull()
        return if (frame == null) t.javaClass.simpleName
        else "${t.javaClass.simpleName} at ${frame.fileName}:${frame.lineNumber}"
    }

    /** 로그 파일 끝에서 최대 [maxBytes] 만큼. 폰으로 보낼 때 쓴다(메시지 크기 제한). */
    @Synchronized
    fun tailBytes(maxBytes: Int): ByteArray {
        val bytes = file?.takeIf { it.exists() }?.readBytes() ?: return ByteArray(0)
        return if (bytes.size <= maxBytes) bytes else bytes.copyOfRange(bytes.size - maxBytes, bytes.size)
    }

    private fun write(level: String, tag: String, msg: String) {
        val f = file ?: return
        try {
            if (f.exists() && f.length() > MAX_BYTES) {
                f.renameTo(File(f.parentFile, "candiy_log.1.txt"))
            }
            f.appendText("${LocalDateTime.now().format(fmt)} $level/$tag $msg\n")
        } catch (_: Exception) {
            // 로그 저장 실패가 기록 자체를 막으면 안 된다
        }
    }
}
