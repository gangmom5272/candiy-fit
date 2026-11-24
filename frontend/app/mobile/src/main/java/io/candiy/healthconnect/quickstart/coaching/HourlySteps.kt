package io.candiy.healthconnect.quickstart.coaching

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONObject

/*
 * 활동 달력에서 고른 날의 시간대별 걸음(S15P31A403-440). 웹 hourly-steps.ts 와 같은 방식이다.
 * 날짜별 걸음 근거에는 시간대 값이 실려 오지 않아 건강 기록의 시간대 걸음 조회(/health/timeline)로 불러온다.
 * AI 가 받은 값이 아니라 지금 저장된 기록이라 화면에 그렇게 적는다. 값은 로그에 남기지 않는다.
 */

/** /health/timeline 응답에서 날짜별 0~23시 걸음만 읽는다. 기록이 없는 날은 null 이다 */
object HourlyStepsJson {
    fun days(body: String): Map<String, List<Double>?> {
        val days = JSONObject(body).getJSONObject("data").getJSONArray("days")
        return (0 until days.length()).associate { i ->
            val day = days.getJSONObject(i)
            val date = day.opt("date") as? String ?: throw IllegalArgumentException("date")
            val hours = if (day.isNull("hourlySteps")) null else day.getJSONArray("hourlySteps").let { values ->
                require(values.length() == 24) { "hourlySteps" }
                List(24) { hour -> (values.opt(hour) as? Number)?.toDouble()?.takeIf { it.isFinite() } ?: throw IllegalArgumentException("hourlySteps") }
            }
            date to hours
        }
    }
}

/**
 * 시간대별 걸음을 달 단위로 불러와 쌓아 둔다. 같은 달 안에서는 날짜를 바꿔도 다시 부르지 않는다.
 * 하루씩 부르면 날짜를 빠르게 바꿀 때마다 요청이 나가고 상자가 깜빡였다(웹 439).
 * - [fetch]는 start~end 의 날짜별 값을 돌려주고, 실패하면 null 이다. 실패한 범위는 쌓아 두지 않아 다음에 다시 부른다
 * - 같은 범위를 동시에 불러도 요청은 한 번이다(뒤에 온 쪽은 앞 요청이 끝난 뒤 쌓인 값을 쓴다)
 * 근거 창 하나가 들고 있다가 창이 닫히면 버린다
 */
class HourlyStepsLoader(private val fetch: suspend (start: String, end: String) -> Map<String, List<Double>?>?) {
    private val loaded = HashMap<String, List<Double>?>()
    private val mutex = Mutex()

    /** 이미 불러온 날인지. 기록이 없는 날(null)도 불러온 날이다 */
    fun has(date: String): Boolean = loaded.containsKey(date)

    /** 불러온 값. 기록이 없는 날과 아직 부르지 않은 날 모두 null 이라 [has]와 함께 쓴다 */
    fun peek(date: String): List<Double>? = loaded[date]

    /** 성공하면 true. 응답에 없는 날짜는 기록 없는 날로 채운다 */
    suspend fun load(start: String, end: String): Boolean = mutex.withLock {
        val dates = EvidenceView.datesBetween(start, end)
        if (dates.all { loaded.containsKey(it) }) return@withLock true
        val days = fetch(start, end) ?: return@withLock false
        dates.forEach { loaded[it] = days[it] }
        true
    }
}
