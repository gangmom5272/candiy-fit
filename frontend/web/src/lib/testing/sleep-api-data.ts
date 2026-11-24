import { getSleepMockWeek, SLEEP_MOCK_TODAY } from '../features/health/sleep-mock';
import type { SleepMetricResult } from '../features/health/sleep';
import { sleepNightFixture } from './health-api-fixture-fields';

/** HTTP fixture만 생성한다. 운영 화면에서는 사용하지 않는다. */
export function sleepMetricFixture(
	metric: SleepMetricResult['metric'],
	start: string,
	end: string
) {
	const monday = Date.parse('2026-10-26');
	const sources = new Map<number, SleepMetricResult>();
	function sourceFor(date: string) {
		const offset = Math.floor((Date.parse(date) - monday) / (7 * 86400000));
		let source = sources.get(offset);
		if (!source) {
			const week = getSleepMockWeek(offset);
			source = structuredClone(metric === 'sleeps' ? week.sleep : week.oxygen);
			sources.set(offset, source);
		}
		return source;
	}
	const days = Array.from(
		{ length: (Date.parse(end) - Date.parse(start)) / 86400000 + 1 },
		(_, index) => {
			const date = new Date(Date.parse(start) + index * 86400000).toISOString().slice(0, 10);
			const day = sourceFor(date).days.find((day) => day.date === date);
			// 합성 원본 이후 날짜도 수치를 만들지 않고 미동기화 상태로 반환한다.
			if (!day && date > SLEEP_MOCK_TODAY)
				return {
					date,
					value: null,
					missing: true,
					reason: 'UNSYNCED' as const,
					isInProgress: false
				};
			if (!day) throw new Error(`수면 fixture 범위에 없는 날짜: ${date}`);
			return day;
		}
	);
	const source = sourceFor(start);
	const selected = [...sources.values()];
	const nights = selected
		.flatMap((s) => s.detail.nights)
		.filter((n) => n.date >= start && n.date <= end);
	const sourceRanges = selected
		.flatMap((s) => s.detail.dailyRanges)
		.filter((r) => r.date >= start && r.date <= end);
	const ranges =
		metric === 'oxygen_saturations'
			? days.map(
					(day) =>
						sourceRanges.find((range) => range.date === day.date) ?? {
							date: day.date,
							min: null,
							max: null
						}
				)
			: [];
	const values = days.flatMap((d) =>
		!d.missing && d.value !== null ? [{ date: d.date, value: d.value }] : []
	);
	const sorted = [...values].sort((a, b) => a.value - b.value);
	const validRanges = ranges.filter((range) => values.some((day) => day.date === range.date));
	const minimum =
		validRanges
			.flatMap((r) => (r.min === null ? [] : [{ date: r.date, value: r.min }]))
			.sort((a, b) => a.value - b.value)[0] ?? null;
	const maximum =
		validRanges
			.flatMap((r) => (r.max === null ? [] : [{ date: r.date, value: r.max }]))
			.sort((a, b) => b.value - a.value)[0] ?? null;
	const completeWeek = start === source.period.start && end === source.period.end;
	return {
		...source,
		period: { start, end },
		days: days.map((day) => ({ ...day, wearHours: null, isWellWorn: null })),
		summary: completeWeek
			? source.summary
			: {
					...source.summary,
					// 수면·산소포화도는 유효 날짜의 단순 평균이다.
					avg: values.length
						? Number((values.reduce((sum, d) => sum + d.value, 0) / values.length).toFixed(2))
						: null,
					dailyMin: sorted[0] ?? null,
					dailyMax: values.find((d) => d.value === sorted.at(-1)?.value) ?? null,
					daysWithData: values.length,
					daysUsedForSummary: values.length,
					completedDays: days.filter((d) => d.reason !== 'BEFORE_LINK').length,
					missingDays: days
						.filter((d) => d.missing && d.reason !== 'UNSYNCED' && d.reason !== 'BEFORE_LINK')
						.map((d) => d.date),
					unsyncedDays: days.filter((d) => d.reason === 'UNSYNCED').map((d) => d.date)
				},
		detail: {
			...source.detail,
			nights: nights.map((night) => {
				const offset = Math.floor((Date.parse(night.date) - monday) / (7 * 86400000));
				const oxygen = getSleepMockWeek(offset).oxygen;
				const range = oxygen.detail.dailyRanges.find((row) => row.date === night.date);
				return sleepNightFixture(night, {
					avg: oxygen.days.find((row) => row.date === night.date)?.value ?? null,
					min: range?.min ?? null,
					max: range?.max ?? null
				});
			}),
			dailyRanges: ranges.map((range) => ({ ...range, sampleCount: null })),
			measuredMin: completeWeek ? source.detail.measuredMin : minimum,
			measuredMax: completeWeek ? source.detail.measuredMax : maximum
		}
	};
}
