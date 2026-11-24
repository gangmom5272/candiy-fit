/** 주간 navigation의 달력 범위. 실제 조회/집계 종료일과 별도로 일요일까지 표시한다. */
export function formatHealthWeek(start: string): string {
	const end = new Date(Date.parse(`${start}T00:00:00Z`) + 6 * 86400000).toISOString().slice(0, 10);
	return `${start.replaceAll('-', '.')} ~ ${(start.slice(0, 4) === end.slice(0, 4) ? end.slice(5) : end).replaceAll('-', '.')}`;
}
