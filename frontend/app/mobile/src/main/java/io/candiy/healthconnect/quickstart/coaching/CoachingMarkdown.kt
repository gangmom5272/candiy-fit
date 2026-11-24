package io.candiy.healthconnect.quickstart.coaching

/**
 * AI 답변의 Markdown 을 앱 화면용 블록으로 나눈다. 웹(lib/features/coaching/markdown.ts, marked 사용)이 그리는 범위와 맞춘다:
 * 문단, 소제목(#~######), 점·번호 목록(여러 줄·안쪽 목록), 표, 구분선, 굵게·기울임. 링크는 글자만, 코드 표시는 기호만 뺀다.
 * AI 지침(ai/.../system_v1.12.md)은 문단·목록·표·### 소제목을 쓴다. 표는 여러 날·지표를 비교할 때 나온다.
 * HTML 로 해석하는 경로가 없다(글자로만 그린다). 라이브러리 없이 직접 구현했다.
 */
object CoachingMarkdown {
    sealed interface Block {
        data class Paragraph(val spans: List<Span>) : Block
        data class Heading(val spans: List<Span>) : Block
        /** marker: "•" 또는 "1." 같은 번호. depth 0 이 바깥 목록 */
        data class Item(val marker: String, val spans: List<Span>, val depth: Int) : Block
        data class Table(val header: List<List<Span>>, val rows: List<List<List<Span>>>) : Block
        data object Rule : Block
    }

    data class Span(val text: String, val bold: Boolean = false, val italic: Boolean = false)

    private val HEADING = Regex("""^#{1,6}\s+(.*?)\s*#*$""")
    private val BULLET = Regex("""^(\s*)[-*+]\s+(.*)$""")
    private val NUMBER = Regex("""^(\s*)(\d{1,3})[.)]\s+(.*)$""")
    private val RULE = Regex("""^\s*([-*_])(\s*\1){2,}\s*$""")
    private val TABLE_SEPARATOR = Regex("""^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$""")

    fun parse(content: String, streaming: Boolean = false): List<Block> {
        val lines = (if (streaming) hideIncomplete(content) else content).lines()
        val blocks = mutableListOf<Block>()
        val paragraph = mutableListOf<String>()
        fun flush() {
            if (paragraph.isNotEmpty()) blocks += Block.Paragraph(spans(paragraph.joinToString("\n")))
            paragraph.clear()
        }
        var i = 0
        while (i < lines.size) {
            val line = lines[i].trimEnd()
            val heading = HEADING.find(line)
            val bullet = BULLET.find(line)
            val number = NUMBER.find(line)
            when {
                line.isBlank() -> flush()
                isTableRow(line) && i + 1 < lines.size && TABLE_SEPARATOR.matches(lines[i + 1].trim()) -> {
                    flush()
                    val header = cells(line)
                    val rows = mutableListOf<List<List<Span>>>()
                    i += 2
                    while (i < lines.size && isTableRow(lines[i].trim())) {
                        rows += normalize(cells(lines[i].trim()), header.size)
                        i++
                    }
                    blocks += Block.Table(header, rows)
                    continue
                }
                RULE.matches(line) && paragraph.isEmpty() -> { flush(); blocks += Block.Rule }
                heading != null -> { flush(); blocks += Block.Heading(spans(heading.groupValues[1])) }
                bullet != null -> { flush(); i = item("•", bullet.groupValues[2], bullet.groupValues[1], lines, i, blocks); continue }
                number != null -> { flush(); i = item("${number.groupValues[2]}.", number.groupValues[3], number.groupValues[1], lines, i, blocks); continue }
                else -> paragraph += line.trim()
            }
            i++
        }
        flush()
        return blocks
    }

    /** 목록 한 항목. 다음 줄이 들여쓴 이어지는 글이면 같은 항목에 붙인다. 다음에 읽을 줄 번호를 돌려준다 */
    private fun item(marker: String, first: String, indent: String, lines: List<String>, start: Int, out: MutableList<Block>): Int {
        val text = StringBuilder(first.trim())
        var i = start + 1
        while (i < lines.size) {
            val next = lines[i]
            val continues = next.isNotBlank() && next.startsWith(" ") && BULLET.find(next) == null && NUMBER.find(next) == null
            if (!continues) break
            text.append('\n').append(next.trim())
            i++
        }
        out += Block.Item(marker, spans(text.toString()), depth(indent))
        return i
    }

    private fun depth(indent: String) = (indent.replace("\t", "    ").length / 2).coerceAtMost(3)

    private fun isTableRow(line: String) = line.trim().let { it.startsWith("|") && it.count { c -> c == '|' } >= 2 }

    private fun cells(line: String): List<List<Span>> =
        line.trim().removePrefix("|").removeSuffix("|").split("|").map { spans(it.trim()) }

    /** 칸 수가 머리 행과 다르면 모자란 칸은 비우고 넘치는 칸은 버린다(웹 marked 와 같은 처리) */
    private fun normalize(row: List<List<Span>>, size: Int) =
        List(size) { row.getOrElse(it) { emptyList() } }

    /**
     * 글줄 안의 꾸밈. **굵게**·__굵게__, *기울임*, `코드`(기호만 뺌), [글자](주소)(글자만). 짝이 없는 기호는 글자로 둔다.
     * 낱말 안의 밑줄(snake_case)은 기울임으로 보지 않는다.
     */
    fun spans(text: String): List<Span> {
        val out = mutableListOf<Span>()
        val plain = StringBuilder()
        var bold = false
        var italic = false
        fun emit() {
            if (plain.isNotEmpty()) out += Span(plain.toString(), bold, italic)
            plain.clear()
        }
        var i = 0
        while (i < text.length) {
            val rest = text.substring(i)
            when {
                rest.startsWith("**") || rest.startsWith("__") -> {
                    val mark = rest.substring(0, 2)
                    if (bold || text.indexOf(mark, i + 2) > i + 2) { emit(); bold = !bold; i += 2 } else { plain.append(mark); i += 2 }
                }
                rest[0] == '*' -> {
                    if (italic || text.indexOf('*', i + 1).let { it > i + 1 && text[i + 1] != ' ' }) { emit(); italic = !italic } else plain.append('*')
                    i++
                }
                rest[0] == '`' -> {
                    val end = text.indexOf('`', i + 1)
                    if (end > i) { plain.append(text, i + 1, end); i = end + 1 } else { plain.append('`'); i++ }
                }
                rest[0] == '[' -> {
                    val link = Regex("""^\[([^\]]+)]\([^)]*\)""").find(rest)
                    if (link != null) { plain.append(link.groupValues[1]); i += link.value.length } else { plain.append('['); i++ }
                }
                rest[0] == '\\' && rest.length > 1 && rest[1] in "*_`[]|#\\" -> { plain.append(rest[1]); i += 2 }
                else -> { plain.append(rest[0]); i++ }
            }
        }
        emit()
        return out.ifEmpty { listOf(Span("")) }
    }

    /** 스트리밍 중 아직 닫히지 않은 `**`·표 줄은 잠깐 숨긴다(다 오면 제 모양으로 바뀐다) */
    private fun hideIncomplete(text: String): String {
        var t = text
        if (Regex("""\*\*""").findAll(t).count() % 2 == 1) t = t.removeRange(t.lastIndexOf("**"), t.lastIndexOf("**") + 2)
        // 마지막 줄이 쓰다 만 표 줄이면 그 줄을 빼서 | 기호가 잠깐 보이지 않게 한다
        val last = t.substringAfterLast('\n')
        if (last.trim().startsWith("|") && !last.trim().endsWith("|")) t = t.substringBeforeLast('\n', "")
        return t
    }
}
