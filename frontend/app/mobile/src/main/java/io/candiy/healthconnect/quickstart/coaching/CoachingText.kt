package io.candiy.healthconnect.quickstart.coaching

/** 화면 문구. 웹(lib/features/coaching view.ts · session.svelte.ts)과 같은 말을 쓴다 */
object CoachingText {
    /**
     * 목록·대화를 불러오지 못했을 때의 안내. 연결 문제면 그렇게 알려, 기록이 사라졌거나 서버가 고장 난 것으로 읽히지 않게 한다.
     * @param subject "대화 목록을"처럼 조사까지 붙인 말
     */
    fun loadFailed(subject: String, code: String?): String =
        if (code == CoachingApi.NETWORK) "인터넷에 연결되지 않아 $subject 불러오지 못했어요. 연결을 확인해 주세요."
        else "$subject 불러오지 못했어요."

    /** 명세 1.3 공개 메시지. 서버가 이벤트로 보낸 message 가 있으면 그것을 먼저 쓴다 */
    private val ERRORS = mapOf(
        "AUTH_REQUIRED" to "로그인이 필요합니다.",
        "AUTH_EXPIRED" to "로그인이 필요합니다.",
        "CANDIY_USER_NOT_LINKED" to "앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.",
        "HEALTH_DATA_FORBIDDEN" to "건강데이터 조회 권한을 확인해 주세요.",
        "HEALTH_DATA_UNAVAILABLE" to "건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "AI_UNAVAILABLE" to "답변을 생성하지 못했습니다.",
        "GENERATION_TIMEOUT" to "답변을 생성하지 못했습니다.",
        "GENERATION_INTERRUPTED" to "답변을 생성하지 못했습니다.",
        "PERSISTENCE_FAILED" to "요청을 처리하지 못했습니다.",
        "INTERNAL_ERROR" to "요청을 처리하지 못했습니다.",
        "INVALID_QUESTION" to "질문 내용을 확인해 주세요.",
        "INVALID_MESSAGE" to "요청 내용을 확인해 주세요.",
        "CONVERSATION_NOT_FOUND" to "대화를 찾을 수 없습니다.",
        "CONVERSATION_BUSY" to "답변이 생성 중입니다.",
        "CONNECTION_FAILED" to "코칭 서버와 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        "RESULT_TIMEOUT" to "답변을 가져오지 못했습니다. 다시 시도해 주세요.",
    )

    private val METRICS = mapOf(
        "steps" to "걸음",
        "heart_rates" to "심박",
        "oxygen_saturations" to "산소포화도",
        "sleeps" to "수면",
        "exercises" to "운동",
    )

    val STAGES = mapOf(
        "reading_history" to "이전 대화를 확인하고 있어요",
        "fetching_health_data" to "건강 기록을 확인하고 있어요",
        "generating_answer" to "답변을 작성하고 있어요",
    )

    /** 이 폰 앱 안에서 바로 할 일을 알려 준다(웹은 "LiNK 앱에서 로그인한 뒤"라고 안내한다) */
    private val HINTS = mapOf(
        "CANDIY_USER_NOT_LINKED" to "홈 탭에서 건강 기록 연결이 끝났는지 확인해 주세요.",
    )

    /** 저장된 실패(errorCode·errorContext)나 실시간 오류를 화면 문구로 바꾼다 */
    fun error(code: String, context: ErrorContext? = null, serverMessage: String? = null): String {
        val base = when {
            code == "HEALTH_DATA_UNAVAILABLE" && context != null -> healthUnavailable(context)
            !serverMessage.isNullOrBlank() -> serverMessage
            else -> ERRORS[code] ?: "답변을 생성하지 못했습니다."
        }
        return HINTS[code]?.let { "$base $it" } ?: base
    }

    private fun healthUnavailable(context: ErrorContext): String {
        if (context.reason == "LOOKUP_FAILED") return ERRORS.getValue("HEALTH_DATA_UNAVAILABLE")
        val metrics = context.metrics.distinct()
        val label = if (metrics.size == 1) "${METRICS[metrics[0]] ?: "건강"} 데이터" else "건강데이터"
        return "${label}를 준비 중입니다. 잠시 후 다시 시도해 주세요."
    }

    /** 명세 7.3: 앞뒤 공백 제거 후 1~2,000 코드 포인트 */
    const val MAX_LENGTH = 2000

    fun normalize(content: String) = content.trim()

    fun length(content: String) = normalize(content).let { it.codePointCount(0, it.length) }

    fun isValid(content: String) = length(content) in 1..MAX_LENGTH
}
