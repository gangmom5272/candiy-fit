import { describe, expect, it } from 'vitest';
import { activityMetricFixture } from './activity-api-data';
import { sleepMetricFixture } from './sleep-api-data';
import { sleepResponsiveFixture } from './sleep-responsive-fixture';
import { dayMetricFixture } from './day-detail-api-fixture';
import { dayResponsiveFixture } from './day-responsive-fixture';
import { homeDailyMetricFixture } from './home-api-fixture';

const day = '2026-10-26';
const generators = [
	['activity steps', () => activityMetricFixture('steps', '2026-10-19', day)],
	['activity exercises', () => activityMetricFixture('exercises', '2026-10-19', day)],
	['sleep', () => sleepMetricFixture('sleeps', '2026-10-19', day)],
	['oxygen', () => sleepMetricFixture('oxygen_saturations', '2026-10-19', day)],
	[
		'oxygen after source date',
		() => sleepMetricFixture('oxygen_saturations', '2026-10-27', '2026-11-02')
	],
	['responsive sleep', () => sleepResponsiveFixture('sleeps', '2026-11-03', '2026-11-09')],
	[
		'responsive oxygen',
		() => sleepResponsiveFixture('oxygen_saturations', '2026-11-03', '2026-11-09')
	],
	['day steps', () => dayMetricFixture('steps', day)],
	['day exercise', () => dayMetricFixture('exercises', '2026-10-19')],
	['day sleep', () => dayMetricFixture('sleeps', '2026-10-20')],
	['responsive day', () => dayResponsiveFixture('2026-11-09').metrics.sleeps],
	['home steps', () => homeDailyMetricFixture('steps')],
	['home sleep', () => homeDailyMetricFixture('sleeps')],
	['home next wake date', () => homeDailyMetricFixture('sleeps', '2026-10-27')]
] as const;

describe('API v47 정상 HTTP fixture의 필수 필드(명세 2.2, 화면용 부분 타입과 구분)', () => {
	it.each(generators)('%s는 nullable 필드도 키를 생략하지 않는다', (_, generate) => {
		const data = generate();
		const values = data.days.flatMap((row) =>
			!row.missing && !row.isInProgress && row.value !== null ? [row.value] : []
		);
		expect(data.summary.avg).toBe(
			values.length
				? Math.round((values.reduce((sum, value) => sum + value, 0) / values.length) * 100) / 100
				: null
		);
		for (const row of data.days) {
			expect(row).toHaveProperty('wearHours');
			expect(row).toHaveProperty('isWellWorn');
		}
		for (const row of data.detail.dailyRanges) expect(row).toHaveProperty('sampleCount');
		if (data.metric === 'oxygen_saturations')
			expect(data.detail.dailyRanges.map((row) => row.date)).toEqual(
				data.days.map((row) => row.date)
			);
		for (const night of data.detail.nights) {
			for (const key of [
				'sessionMinutes',
				'unspecifiedMinutes',
				'unknownMinutes',
				'avgHeartRate',
				'minHeartRate',
				'maxHeartRate',
				'spo2Avg',
				'spo2Min',
				'spo2Max',
				'spo2Count',
				'spo2LowCount',
				'spo2LowTimes'
			])
				expect(night).toHaveProperty(key);
			expect(night.startAt).toMatch(/Z$/);
			expect(night.endAt).toMatch(/Z$/);
			expect(new Date(Date.parse(night.endAt) + 9 * 3600000).toISOString().slice(0, 10)).toBe(
				night.date
			);
		}
	});

	it('운동 세션은 공개 식별자·분류·거리와 경과 시간을 일관되게 반환한다', () => {
		const data = activityMetricFixture('exercises', '2026-10-19', day);
		for (const session of data.detail.sessions) {
			expect(session).toHaveProperty('recordId');
			expect(session).not.toHaveProperty('id');
			expect(session).toHaveProperty('distanceM', null);
			expect(['cardio', 'strength', 'flexibility', 'other']).toContain(session.category);
			expect((Date.parse(session.endAt) - Date.parse(session.startAt)) / 60000).toBe(
				session.durationMinutes
			);
		}
	});

	it('홈의 실제 수면 분 변경은 단계별 합계를 강제로 맞추지 않는다', () => {
		const data = homeDailyMetricFixture('sleeps', '2026-10-23');
		expect(data.days[0].value).toBe(420);
		expect(data.detail.nights[0].sleepMinutes).toBe(420);
		// 원본 단계는 화면 테스트의 독립 저장값이다. 합계 보정을 하지 않는다.
		expect(data.detail.nights[0].deepMinutes).toBe(80);
	});

	it('새 기상일의 홈 수면 값은 같은 날짜의 주 수면과 연결하고 모르는 단계는 null로 둔다', () => {
		const data = homeDailyMetricFixture('sleeps', '2026-10-27');
		expect(data.detail.nights).toHaveLength(1);
		expect(data.detail.nights[0]).toMatchObject({
			date: '2026-10-27',
			sleepMinutes: 420,
			sessionMinutes: 420,
			deepMinutes: null
		});
	});

	it('주 수면의 산소포화도 상세는 같은 기상일 응답과 같은 원본을 쓴다', () => {
		const sleep = sleepMetricFixture('sleeps', '2026-10-19', day);
		const oxygen = sleepMetricFixture('oxygen_saturations', '2026-10-19', day);
		for (const night of sleep.detail.nights) {
			expect(night.spo2Avg).toBe(oxygen.days.find((row) => row.date === night.date)?.value);
			expect(night.spo2Min).toBe(
				oxygen.detail.dailyRanges.find((row) => row.date === night.date)?.min
			);
		}
	});

	it('낮은 산소포화도 시각은 v42 이후의 UTC 초 단위 형식과 개수·정렬을 유지한다', () => {
		const data = sleepResponsiveFixture('sleeps', '2026-11-03', '2026-11-09', 'extreme');
		for (const night of data.detail.nights) {
			const times: unknown = Reflect.get(night, 'spo2LowTimes');
			if (!Array.isArray(times)) throw new Error('측정이 있는 합성 밤에는 시각 배열이 필요합니다.');
			expect(times).toHaveLength(Reflect.get(night, 'spo2LowCount'));
			expect(times).toEqual([...times].sort());
			for (const time of times) expect(time).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
		}
	});
});
