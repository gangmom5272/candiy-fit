package io.candiy.healthconnect.quickstart

/**
 * 지표별 서버 전송 결과. SDK 가 동기화 때 지표마다 남기는 로그 한 줄에서 읽는다(건수·성공 여부만).
 *
 *   `MM-dd HH:mm:ss.SSS I/CandiySync( pid): remote_transfer_result type=STEPS sent=3 dropped=0 ok=true cursor=…`
 *
 * SDK 는 진행률·지표별 결과를 앱에 넘겨주지 않아(콜백은 시작 결과 한 번) 로그 복사본(FileLog)에서 읽는다.
 *
 * @property lastAt 마지막으로 전송을 시도한 시각 "MM-dd HH:mm"
 * @property ok 그때 지표 전송이 끝까지 성공했는지
 * @property lastSentAt 새 기록을 1건 이상 보낸 마지막 시각. 아직 없으면 null
 * @property lastSentCount 그때 보낸 건수
 */
data class TypeTransfer(
    val lastAt: String,
    val ok: Boolean,
    val lastSentAt: String?,
    val lastSentCount: Int,
)

object TransferLog {
    private val RESULT = Regex("""remote_transfer_result\s+type=(\S+)\s+sent=(\d+)(?:\s+dropped=\d+)?\s+ok=(true|false)""")

    /** 로그 줄들(오래된 것부터)에서 지표별 마지막 결과를 모은다. 키는 SDK 지표 이름(대문자, 예: STEPS) */
    fun parse(lines: List<String>): Map<String, TypeTransfer> {
        val out = mutableMapOf<String, TypeTransfer>()
        for (line in lines) {
            val m = RESULT.find(line) ?: continue
            val (type, sent, ok) = m.destructured
            val at = line.take(11).trim()
            val key = type.uppercase()
            val n = sent.toInt()
            val prev = out[key]
            out[key] = TypeTransfer(
                lastAt = at,
                ok = ok == "true",
                lastSentAt = if (n > 0) at else prev?.lastSentAt,
                lastSentCount = if (n > 0) n else prev?.lastSentCount ?: 0,
            )
        }
        return out
    }
}
