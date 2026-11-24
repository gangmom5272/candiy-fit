package io.candiy.healthconnect.quickstart

/**
 * 동기화 한 회차 (와이어프레임 A-05 전송 기록). SDK 가 회차마다 남기는 로그에서 읽는다(건수·상태만).
 *
 *   `MM-dd HH:mm:ss.SSS I/CandiySync( pid): remote_transfer_result type=STEPS sent=3 dropped=0 ok=true …`
 *   `MM-dd HH:mm:ss.SSS I/CandiySync( pid): job_finished result=success status=success read=6 saved=6 sent=6 failed=0 …`
 *
 * 와이어프레임은 서버(sync_runs)에서 읽도록 그렸지만 아직 앱용 조회 API 가 없어, 이 폰에서 보낸 결과만 보여 준다.
 *
 * @property at 끝난 시각 "MM-dd HH:mm"
 * @property sentByType 이 회차에 지표별로 보낸 건수(1건 이상만). 키는 SDK 지표 이름(예: STEPS)
 */
data class SyncRun(
    val at: String,
    val ok: Boolean,
    val status: String,
    val read: Int,
    val sent: Int,
    val failed: Int,
    val sentByType: Map<String, Int>,
) {
    val kind: Kind
        get() = when {
            !ok -> Kind.FAILED
            failed > 0 -> Kind.PARTIAL
            sent == 0 -> Kind.EMPTY
            else -> Kind.SENT
        }

    enum class Kind { SENT, EMPTY, PARTIAL, FAILED }
}

object SyncHistory {
    private val TRANSFER = Regex("""remote_transfer_result\s+type=(\S+)\s+sent=(\d+)""")
    private val FINISHED = Regex("""job_finished\s""")

    /** 로그 줄들(오래된 것부터)에서 회차를 모은다. 최근 회차부터 최대 [limit]개 */
    fun parse(lines: List<String>, limit: Int = 60): List<SyncRun> {
        val runs = mutableListOf<SyncRun>()
        val pending = linkedMapOf<String, Int>()
        for (line in lines) {
            TRANSFER.find(line)?.let { m ->
                val n = m.groupValues[2].toInt()
                if (n > 0) pending.merge(m.groupValues[1].uppercase(), n, Int::plus)
            }
            if (!FINISHED.containsMatchIn(line)) continue
            fun field(key: String) = Regex("""\b$key=(\S+)""").find(line)?.groupValues?.get(1)
            runs += SyncRun(
                at = line.take(11).trim(),
                ok = field("result") == "success",
                status = field("status") ?: "-",
                read = field("read")?.toIntOrNull() ?: 0,
                sent = field("sent")?.toIntOrNull() ?: 0,
                failed = field("failed")?.toIntOrNull() ?: 0,
                sentByType = pending.toMap(),
            )
            pending.clear()
        }
        return runs.asReversed().take(limit)
    }
}
