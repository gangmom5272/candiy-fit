package io.candiy.healthconnect.quickstart

/**
 * 파일 로그에 옮겨 적기 전에 건강 수치·서버 응답 본문이 섞일 수 있는 부분을 가린다.
 * 건수·종류·시간·상태처럼 문제를 찾는 데 필요한 값은 그대로 둔다.
 * 기준: 반출 금지 점검표 2-8 (저장소 infra/data-export-checklist.md)
 */
object LogRedaction {
    const val MASK = "<가림>"

    /** logcat `-v time` 줄의 머리(`MM-dd HH:mm:ss.SSS E/Tag( pid): `)와 본문 */
    private val LOGCAT_LINE = Regex("""^(.*?\(\s*\d+\):\s?)(.*)$""")

    /**
     * 서버 오류 본문(`error=`)·예외 문구(`msg=`)·응답 메시지(`message=`)가 붙는 키.
     * 본문 안에 `값=…` 같은 글이 섞여도 새지 않게 줄 끝까지 가린다.
     */
    private val FREE_TEXT_VALUE = Regex("""\b(msg|error|message|err\w*)=.*$""")

    /**
     * 남겨도 되는 줄: `job_finished`처럼 밑줄 든 이벤트 이름(또는 `http`)으로 시작하고,
     * 뒤는 `키=값`이거나 숫자 없는 낱말(`[StepsRecord, HeartRateRecord]` 같은 종류 목록)뿐인 줄.
     * 여러 줄짜리 오류 본문은 logcat이 줄마다 머리를 붙여 찍어 둘째 줄부터 이 모양이 아니므로 통째로 가려진다.
     */
    private val EVENT_LINE = Regex("""^(?:http|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?:\s+(?:[A-Za-z]\w*=\S*|[^\s\d=]+))*\s*$""")

    /** 예외 스택의 위치 줄. 클래스·파일·줄 번호만 있다. */
    private val STACK_FRAME = Regex("""^\s*(?:at [\w.$<>]+\([\w.$ ]*(?::\d+)?\)|\.\.\. \d+ more)\s*$""")

    /** 예외 머리 줄(`java.lang.X: 문구`, `Caused by: X: 문구`). 종류만 남기고 문구는 가린다. */
    private val EXCEPTION_HEADER = Regex("""^\s*((?:Caused by: |Suppressed: )?[\w.$]*(?:Exception|Error|Throwable)[\w$]*)(:.*)?$""")

    private val NUMBER = Regex("""\d+(?:\.\d+)?""")

    /** 위 모양이 아닌 줄은 본문을 통째로 가린다. 형식을 모르는 글에는 값이 들어갈 수 있다. */
    fun sdkLine(line: String): String {
        val (head, body) = LOGCAT_LINE.find(line)?.destructured ?: return MASK
        val masked = FREE_TEXT_VALUE.replace(body) { "${it.groupValues[1]}=$MASK" }
        val safe = when {
            EVENT_LINE.matches(masked) -> masked
            STACK_FRAME.matches(body) -> body
            else -> EXCEPTION_HEADER.matchEntire(body)?.let {
                if (it.groupValues[2].isEmpty()) it.groupValues[1] else "${it.groupValues[1]}: $MASK"
            } ?: MASK
        }
        return head + safe
    }

    /** SDK 콜백 문구처럼 형식을 모르는 글. 숫자를 가리고 길이를 줄인다. */
    fun freeText(text: String?): String = text?.replace(NUMBER, "#")?.take(80) ?: "-"

    /** 예외는 종류와 앱 코드 안 발생 위치(파일:줄)만 남긴다. 예외 문구에는 값이 들어갈 수 있다. */
    fun throwable(t: Throwable): String {
        val frame = t.stackTrace.firstOrNull { it.className.startsWith("io.candiy.healthconnect") }
            ?: t.stackTrace.firstOrNull()
        return if (frame == null) t.javaClass.simpleName
        else "${t.javaClass.simpleName} at ${frame.fileName}:${frame.lineNumber}"
    }
}
