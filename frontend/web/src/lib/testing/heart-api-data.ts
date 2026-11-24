import { getHeartMockWeek, HEART_MOCK_TODAY } from '../features/health/heart-mock';

/** HTTP 테스트 전용. 요청 기간과 fixture 기간이 다르면 조용히 대체하지 않는다. */
export function heartMetricFixture(start: string, end: string) {
	const offset = Math.floor((Date.parse(start) - Date.parse(HEART_MOCK_TODAY)) / (7 * 86400000));
	const result = getHeartMockWeek(offset, { start, end });
	if (result.period.start !== start || result.period.end !== end)
		throw new Error(`심박 fixture에 없는 기간: ${start} ~ ${end}`);
	return structuredClone(result);
}
