package io.candiy.healthconnect.quickstart.coaching

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** AI 코치 탭의 계약 해석·문구·Markdown. 값은 모두 합성이다(명세 6·7절 예시와 같은 모양) */
class CoachingTest {
    private val conversationId = "22222222-2222-4222-8222-222222222222"
    private val requestId = "33333333-3333-4333-8333-333333333333"

    @Test fun `대화 목록을 읽고 다음 커서를 남긴다`() {
        val page = CoachingJson.conversations(
            """{"data":{"items":[{"id":"$conversationId","title":"지난주 걸음","summary":null,"createdAt":"2026-10-19T01:00:00Z","updatedAt":"2026-10-19T01:00:02Z","lastRequestStatus":"completed"}],"nextCursor":"abc"}}""",
        )
        assertEquals(1, page.items.size)
        assertEquals("지난주 걸음", page.items[0].title)
        assertEquals("abc", page.nextCursor)
        assertNull(CoachingJson.conversations("""{"data":{"items":[],"nextCursor":null}}""").nextCursor)
    }

    @Test fun `메시지 하나가 형식이 달라도 나머지는 남긴다`() {
        val page = CoachingJson.messages(
            """{"data":{"conversation":{"id":"$conversationId","title":"제목","summary":null,"createdAt":"x","updatedAt":"x"},
            "items":[
              {"id":"u1","requestId":"$requestId","role":"user","sequenceNo":1,"content":"질문","requestStatus":"completed","errorCode":null,"createdAt":"x","evidence":[],"errorContext":null},
              {"id":"bad","role":"assistant"},
              {"id":"a1","requestId":"$requestId","role":"assistant","sequenceNo":2,"content":"","requestStatus":"failed","errorCode":"HEALTH_DATA_UNAVAILABLE","createdAt":"x","evidence":[{},{}],"errorContext":{"reason":"DATA_NOT_READY","metrics":["sleeps"]}}
            ],"nextBeforeSequenceNo":null}}""",
        )
        assertEquals(listOf("u1", "a1"), page.items.map { it.id })
        // 모양이 다른 근거(빈 객체)는 그 근거만 빼고 메시지는 남긴다
        assertEquals(emptyList<Evidence>(), page.items[1].evidence)
        assertEquals(ErrorContext("DATA_NOT_READY", listOf("sleeps")), page.items[1].errorContext)
        assertNull(page.nextBeforeSequenceNo)
    }

    @Test fun `서버 이벤트를 종류별로 나눈다`() {
        assertEquals(SocketEvent.Ready, CoachingJson.event("""{"type":"ready","data":{"protocolVersion":"1.0"}}"""))
        assertEquals(SocketEvent.ConnectionError("AUTH_EXPIRED"), CoachingJson.event("""{"type":"connection_error","data":{"code":"AUTH_EXPIRED","message":"로그인이 필요합니다."}}"""))
        val accepted = CoachingJson.event(
            """{"type":"accepted","clientRequestId":"c","requestId":"$requestId","conversationId":"$conversationId","eventSeq":1,"data":{"status":"accepted","conversation":{"id":"$conversationId","title":"제목"}}}""",
        )
        assertEquals(SocketEvent.Accepted(requestId, conversationId, "제목"), accepted)
        assertEquals(SocketEvent.Delta("조각"), CoachingJson.event("""{"type":"delta","data":{"text":"조각"}}"""))
        assertEquals(SocketEvent.EvidenceItems(emptyList()), CoachingJson.event("""{"type":"evidence","data":{"items":[{},{},{}]}}"""))
        val failed = CoachingJson.event(
            """{"type":"error","clientRequestId":null,"requestId":null,"conversationId":null,"eventSeq":0,"data":{"code":"INVALID_QUESTION","message":"질문 내용을 확인해 주세요.","retryable":false,"accepted":false}}""",
        ) as SocketEvent.Failed
        assertFalse(failed.accepted)
        assertNull(failed.context)
        assertNull(CoachingJson.event("""{"type":"unknown_type","data":{}}"""))
        assertNull(CoachingJson.event("깨진 프레임"))
    }

    @Test fun `질문 프레임은 첫 질문의 대화 ID 를 null 로 보낸다`() {
        val o = JSONObject(CoachingJson.question("c", null, "질문"))
        assertEquals("question", o.getString("type"))
        assertTrue(o.isNull("conversationId"))
        assertEquals(setOf("type", "clientRequestId", "conversationId", "content"), o.keys().asSequence().toSet())
    }

    @Test fun `건강 조회 실패는 지표 이름으로 안내한다`() {
        assertEquals("수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.", CoachingText.error("HEALTH_DATA_UNAVAILABLE", ErrorContext("DATA_NOT_READY", listOf("sleeps"))))
        assertEquals("건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.", CoachingText.error("HEALTH_DATA_UNAVAILABLE", ErrorContext("DATA_NOT_READY", listOf("sleeps", "steps"))))
        assertEquals("건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.", CoachingText.error("HEALTH_DATA_UNAVAILABLE", ErrorContext("LOOKUP_FAILED", emptyList())))
        assertTrue(CoachingText.error("CANDIY_USER_NOT_LINKED").endsWith("홈 탭에서 건강 기록 연결이 끝났는지 확인해 주세요."))
        assertEquals("답변을 생성하지 못했습니다.", CoachingText.error("모르는_코드"))
    }

    @Test fun `불러오기 실패는 연결 문제일 때 그렇게 알린다`() {
        assertEquals("인터넷에 연결되지 않아 대화 목록을 불러오지 못했어요. 연결을 확인해 주세요.", CoachingText.loadFailed("대화 목록을", CoachingApi.NETWORK))
        // 서버 오류나 까닭을 모를 때는 기록이 사라진 것처럼 읽히지 않게 짧게만 알린다
        assertEquals("이전 대화를 불러오지 못했어요.", CoachingText.loadFailed("이전 대화를", "INTERNAL_ERROR"))
        assertEquals("이전 대화를 불러오지 못했어요.", CoachingText.loadFailed("이전 대화를", null))
    }

    @Test fun `질문 길이는 앞뒤 공백을 빼고 코드 포인트로 센다`() {
        assertFalse(CoachingText.isValid("   "))
        assertTrue(CoachingText.isValid(" 수면 "))
        assertEquals(2, CoachingText.length("😀😀"))
        assertFalse(CoachingText.isValid("가".repeat(2001)))
    }

    @Test fun `답변 Markdown 을 문단·소제목·목록·굵게로 나눈다`() {
        val blocks = CoachingMarkdown.parse("요약 **6시간** 이에요.\n\n### 자세히\n- 첫째\n  - 안쪽\n2. 둘째")
        assertEquals(5, blocks.size)
        val first = blocks[0] as CoachingMarkdown.Block.Paragraph
        assertEquals(listOf(false, true, false), first.spans.map { it.bold })
        assertTrue(blocks[1] is CoachingMarkdown.Block.Heading)
        assertEquals(1, (blocks[3] as CoachingMarkdown.Block.Item).depth)
        assertEquals("2.", (blocks[4] as CoachingMarkdown.Block.Item).marker)
    }

    @Test fun `짝 없는 굵게 기호는 글자로 두고 스트리밍 중에는 숨긴다`() {
        assertEquals("a **b", CoachingMarkdown.spans("a **b").joinToString("") { it.text })
        val streaming = CoachingMarkdown.parse("쓰는 중 **굵", streaming = true)[0] as CoachingMarkdown.Block.Paragraph
        assertEquals("쓰는 중 굵", streaming.spans.joinToString("") { it.text })
    }

    @Test fun `비교 표를 머리 행과 행으로 나눈다(AI 지침 v1_12 형식)`() {
        val blocks = CoachingMarkdown.parse(
            "최근 3일 수면이에요.\n\n| 날짜 | 수면 | 비고 |\n| --- | :---: | --- |\n| 11월 4일 | **6시간 10분** | |\n| 11월 5일 | 7시간 |\n\n이어지는 문단",
        )
        assertEquals(3, blocks.size)
        val table = blocks[1] as CoachingMarkdown.Block.Table
        assertEquals(listOf("날짜", "수면", "비고"), table.header.map { c -> c.joinToString("") { it.text } })
        assertEquals(2, table.rows.size)
        assertTrue(table.rows[0][1].single().bold)
        // 칸이 모자란 행은 빈 칸으로 채운다
        assertEquals(3, table.rows[1].size)
    }

    @Test fun `스트리밍 중 쓰다 만 표 줄은 숨긴다`() {
        val blocks = CoachingMarkdown.parse("| 날짜 | 수면 |\n| --- | --- |\n| 11월 4일 | 6시", streaming = true)
        val table = blocks.single() as CoachingMarkdown.Block.Table
        assertTrue(table.rows.isEmpty())
    }

    @Test fun `기울임·코드·링크는 기호 없이 글자로 보여 준다`() {
        val spans = CoachingMarkdown.spans("*참고*로 `steps` 값과 [안내](https://x.y) 보기, snake_case 는 그대로")
        assertEquals("참고로 steps 값과 안내 보기, snake_case 는 그대로", spans.joinToString("") { it.text })
        assertTrue(spans.first().italic)
        assertFalse(spans.last().italic)
    }

    @Test fun `들여 쓴 다음 줄은 같은 목록 항목에 붙인다`() {
        val blocks = CoachingMarkdown.parse("- **걷기**: 첫 줄\n  이어지는 설명\n- 둘째\n\n---\n\n끝")
        assertEquals(4, blocks.size)
        val first = blocks[0] as CoachingMarkdown.Block.Item
        assertEquals("걷기: 첫 줄\n이어지는 설명", first.spans.joinToString("") { it.text })
        assertTrue(first.spans.first().bold)
        assertEquals(CoachingMarkdown.Block.Rule, blocks[2])
    }

    @Test fun `대화 목록 시각은 오늘·어제·날짜로 보여 준다`() {
        val today = LocalDate.of(2026, 11, 6)
        // 기기 시간대와 관계없이 같은 날을 고르도록 정오 UTC 를 쓴다(한국 21시)
        assertTrue(time("2026-11-06T03:00:00Z", today).startsWith("오늘 "))
        assertTrue(time("2026-11-05T03:00:00Z", today).startsWith("어제 "))
        assertEquals("11.03", time("2026-11-03T03:00:00Z", today))
        assertEquals("", time("깨진 시각", today))
    }
}
