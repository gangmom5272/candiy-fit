import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { homeDailyMetricFixture, lifestyleScoreFixture } from '#lib/testing/home-api-fixture.js';
import { getHomeMetric, getLifestyleScore, homeScoreStatuses, homeRequestError } from './home-api';
import { metricDayViewFixture } from '#lib/testing/health-api-fixture-fields.js';
import { isHealthDataNotReady } from '#lib/features/health/data-readiness.js';

function expectFriendlyDescription(description: string) {
	expect(description.trim()).not.toBe('');
	expect(description.split(/(?<=[.!?])\s+/u).length).toBeLessThanOrEqual(2);
	expect(description).not.toMatch(
		/\d+\s*점|\d+\s*\/\s*\d+|만점|세부\s*점수|구간|비례|반올림|사사오입|기준점|합산|정규화|산식|계산식/
	);
	expect(description).not.toMatch(/질환|부정맥|빈맥|서맥|진단|치료/);
}

it('Home GET 409 미연동은 일반 조회 장애로 바꾸지 않고 공개 연동 안내를 유지한다', async () => {
	const client = createApiClient({
		fetch: async () =>
			Response.json(
				{
					error: {
						code: 'CANDIY_USER_NOT_LINKED',
						message: 'Internal diagnostic must not be displayed'
					}
				},
				{ status: 409 }
			)
	});
	const error = await client.request('/health/lifestyle-score').catch((error) => error);
	expect(homeRequestError(error)).toBe(
		'앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.'
	);
});

it.each(['steps', 'exercises', 'sleeps'] as const)(
	'Home %s는 오늘 하루만 조회하고 summary 대신 날짜별 상태·값을 보존한다',
	async (metric) => {
		const data = homeDailyMetricFixture(metric);
		const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ data }));
		const controller = new AbortController();
		const day = await getHomeMetric(
			metric,
			'2026-10-26',
			controller.signal,
			createApiClient({ fetch })
		);
		expect(fetch).toHaveBeenCalledExactlyOnceWith(
			`/api/v1/health/metrics/${metric}?start=2026-10-26&end=2026-10-26`,
			expect.objectContaining({ signal: controller.signal })
		);
		expect(day).toEqual(metricDayViewFixture(data.days[0]));
	}
);

it('응답에 오늘 날짜가 없으면 이전 날짜나 0으로 대체하지 않는다', async () => {
	const data = homeDailyMetricFixture('steps');
	data.days[0].date = '2026-10-25';
	const client = createApiClient({ fetch: async () => Response.json({ data }) });
	await expect(getHomeMetric('steps', '2026-10-26', undefined, client)).rejects.toThrow(
		'home metric day'
	);
});

it('선택한 KST 하루를 조회하고 저장된 총점·등급·수면 심박을 재집계 없이 보존한다', async () => {
	const data = lifestyleScoreFixture();
	const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ data }));
	const controller = new AbortController();
	const result = await getLifestyleScore(data.date, controller.signal, createApiClient({ fetch }));
	expect(fetch).toHaveBeenCalledExactlyOnceWith(
		'/api/v1/health/lifestyle-score?date=2026-10-26',
		expect.objectContaining({ method: 'GET', signal: controller.signal })
	);
	expect(result).toEqual(data);
	expect(
		homeScoreStatuses(result, false, null).map(({ label, score }) => ({ label, score }))
	).toEqual([
		{ label: '훌륭', score: 85 },
		{ label: '관심', score: 58 },
		{ label: '안정', score: 70 }
	]);
});

it('점수로 등급을 재판정하지 않고 서버 grade를 표시명으로 변환한다', () => {
	const data = lifestyleScoreFixture();
	data.areas.heart.grade = 4;
	expect(homeScoreStatuses(data, false, null)[2]).toMatchObject({ label: '관리', score: 70 });
});

it('점수 설명은 해당 날짜의 실제 활동·주 수면·수면 심박 값을 따르고 서버 점수·등급은 유지한다', () => {
	const data = lifestyleScoreFixture();
	const statuses = homeScoreStatuses(data, false, null);
	expect(statuses[0].description).toMatch(/7,200보/);
	expect(statuses[0].description).toMatch(/인정 운동(?:은)?\s*19분/);
	expect(statuses[0].description).toMatch(/좋은.*활동|활동.*좋/);
	expect(statuses[1].description).toMatch(/6시간\s*6분/);
	expect(statuses[2].description).toMatch(/64\.5\s*bpm/);
	expect(statuses[2].description).toMatch(/평소.*58\s*bpm/);
	expect(statuses[2].description).toMatch(/6\.5\s*bpm.*높/);
	for (const { description } of statuses) expectFriendlyDescription(description);

	data.areas.activity.steps.value = 8500;
	data.areas.activity.exercise.minutes = 32;
	data.areas.sleep.sleepMinutes = 481;
	data.areas.heart.sleepHrAvg = 57.5;
	data.areas.heart.baselineHrAvg = 60;
	const changed = homeScoreStatuses(data, false, null);
	expect(changed[0].description).toMatch(/8,500보/);
	expect(changed[0].description).toMatch(/인정 운동(?:은)?\s*32분/);
	expect(changed[1].description).toMatch(/8시간\s*1분/);
	expect(changed[2].description).toMatch(/57\.5\s*bpm/);
	expect(changed[2].description).toMatch(/평소.*60\s*bpm/);
	expect(changed[2].description).toMatch(/2\.5\s*bpm.*낮/);
	expect(changed.map(({ description }) => description)).not.toEqual(
		statuses.map(({ description }) => description)
	);
	expect(changed.map(({ label, score }) => ({ label, score }))).toEqual([
		{ label: '훌륭', score: 85 },
		{ label: '관심', score: 58 },
		{ label: '안정', score: 70 }
	]);
	expect(data.total.score).toBe(71);
	for (const { description } of changed) expectFriendlyDescription(description);
});

it('실제 0분·0점은 표시하고 점수가 없는 0보와 확인할 수 없는 운동 시간은 0점·0분으로 채우지 않는다', () => {
	const data = lifestyleScoreFixture();
	data.areas.activity = {
		score: 0,
		grade: 4,
		steps: { value: 800, score: 0 },
		exercise: { minutes: 0, score: 0 }
	};
	data.areas.sleep = { score: 0, grade: 4, sleepMinutes: 0 };
	const [activity, sleep] = homeScoreStatuses(data, false, null);
	expect(activity).toMatchObject({ score: 0, label: '관리' });
	expect(activity.description).toMatch(/800보/);
	expect(activity.description).toMatch(/인정 운동(?:은)?\s*0분/);
	expect(sleep).toMatchObject({ score: 0, label: '관리' });
	expect(sleep.description).toMatch(/0분/);
	expect(sleep.description).toMatch(/기록.*확인/);
	expect(sleep.description).not.toMatch(/조금.*더|늘려|늘리/);
	expectFriendlyDescription(activity.description);
	expectFriendlyDescription(sleep.description);

	data.areas.activity = {
		score: null,
		grade: null,
		steps: { value: 0, score: null },
		exercise: { minutes: null, score: null }
	};
	const [missing] = homeScoreStatuses(data, false, null);
	expect(missing.score).toBeNull();
	expect(missing.description).toMatch(/0보/);
	expect(missing.description).toMatch(/활동.*점수.*(아직|확인)/);
	expect(missing.description).toMatch(/인정 운동.*시간.*확인.*(불가|어려|없)/);
	expect(missing.description).not.toMatch(/운동.*기록 없음/);
	expect(missing.description).not.toMatch(/0분|0점|반영한 점수/);
	expectFriendlyDescription(missing.description);
});

it('평소 심박 기준이 없으면 실제 수면 심박은 표시하고 비교 기록이 쌓이는 중임을 안내한다', () => {
	const data = lifestyleScoreFixture();
	data.areas.heart = {
		score: null,
		grade: null,
		sleepHrAvg: 64.5,
		baselineHrAvg: null,
		baselineNights: 0,
		checkWear: false
	};
	const heart = homeScoreStatuses(data, false, null)[2];
	expect(heart.score).toBeNull();
	expect(heart.description).toMatch(/64\.5\s*bpm/);
	expect(heart.description).toMatch(/평소.*기록.*(쌓|부족|준비)/);
	expect(heart.description).not.toMatch(/높|낮|같아요|착용|(?:^|\s)0\s*bpm/);
	expectFriendlyDescription(heart.description);
});

it('수면 심박 비교는 높음·낮음·같음을 구분하고 표시한 소수 첫째 자리 값끼리 차이를 계산한다', () => {
	const data = lifestyleScoreFixture();
	data.areas.heart.baselineHrAvg = 58;
	data.areas.heart.sleepHrAvg = 64.46;
	const higher = homeScoreStatuses(data, false, null)[2].description;
	expect(higher).toMatch(/64\.5\s*bpm/);
	expect(higher).toMatch(/6\.5\s*bpm.*높/);
	expect(higher).not.toMatch(/64\.46|6\.46/);
	expectFriendlyDescription(higher);

	data.areas.heart.sleepHrAvg = 51.54;
	const lower = homeScoreStatuses(data, false, null)[2].description;
	expect(lower).toMatch(/51\.5\s*bpm/);
	expect(lower).toMatch(/6\.5\s*bpm.*낮/);
	expect(lower).not.toMatch(/51\.54|6\.46|-6\.5/);
	expectFriendlyDescription(lower);

	data.areas.heart.sleepHrAvg = 58;
	const same = homeScoreStatuses(data, false, null)[2].description;
	expect(same).toMatch(/58\s*bpm.*같/);
	expect(same).not.toMatch(/높|낮/);
	expectFriendlyDescription(same);

	data.areas.heart.sleepHrAvg = 64.44;
	data.areas.heart.baselineHrAvg = 58.45;
	const rounded = homeScoreStatuses(data, false, null)[2].description;
	expect(rounded).toMatch(/64\.4\s*bpm/);
	expect(rounded).toMatch(/평소.*58\.5\s*bpm/);
	expect(rounded).toMatch(/5\.9\s*bpm.*높/);
	expect(rounded).not.toMatch(/64\.44|58\.45|6(?:\.0)?\s*bpm.*높/);
	expectFriendlyDescription(rounded);
});

it('워치 착용 안내는 서버 checkWear가 true인 경우에만 추가한다', () => {
	const data = lifestyleScoreFixture();
	const normal = homeScoreStatuses(data, false, null)[2];
	expect(normal.description).not.toMatch(/착용/);
	data.areas.heart.checkWear = true;
	const checkWear = homeScoreStatuses(data, false, null)[2];
	expect(checkWear.description).toMatch(/착용.*확인/);
	expect(checkWear.description).toMatch(/64\.5\s*bpm/);
	expect(checkWear).toMatchObject({ score: normal.score, label: normal.label });
	expectFriendlyDescription(checkWear.description);
	data.areas.heart.checkWear = null;
	expect(homeScoreStatuses(data, false, null)[2].description).not.toMatch(/착용/);
});

it('걸음이나 운동 한쪽의 기록이 충분해도 다른 활동을 늘리는 안내를 구분한다', () => {
	const data = lifestyleScoreFixture();
	data.areas.activity = {
		score: 55,
		grade: 3,
		steps: { value: 2500, score: 15 },
		exercise: { minutes: 30, score: 40 }
	};
	const shortSteps = homeScoreStatuses(data, false, null)[0];
	expect(shortSteps).toMatchObject({ score: 55, label: '관심' });
	expect(shortSteps.description).toMatch(/2,500보/);
	expect(shortSteps.description).toMatch(/인정 운동(?:은)?\s*30분/);
	expect(shortSteps.description).toMatch(/걷기.*늘|걸음.*늘/);
	expectFriendlyDescription(shortSteps.description);

	data.areas.activity = {
		score: 60,
		grade: 3,
		steps: { value: 7200, score: 60 },
		exercise: { minutes: 0, score: 0 }
	};
	const shortExercise = homeScoreStatuses(data, false, null)[0];
	expect(shortExercise).toMatchObject({ score: 60, label: '관심' });
	expect(shortExercise.description).toMatch(/7,200보/);
	expect(shortExercise.description).toMatch(/인정 운동(?:은)?\s*0분/);
	expect(shortExercise.description).toMatch(/운동[^.!?]*(늘|더해)/);
	expect(shortExercise.description).not.toBe(shortSteps.description);
	expectFriendlyDescription(shortExercise.description);
});

it('수면 시간 경계의 서버 점수·등급을 유지하면서 짧은 수면과 긴 수면 안내를 구분한다', () => {
	const cases = [
		{
			minutes: 210,
			score: 0,
			grade: 4,
			label: '관리',
			duration: /3시간\s*30분/,
			advice: /수면.*조금.*더/
		},
		{
			minutes: 270,
			score: 20,
			grade: 4,
			label: '관리',
			duration: /4시간\s*30분/,
			advice: /수면.*조금.*더/
		},
		{
			minutes: 330,
			score: 40,
			grade: 4,
			label: '관리',
			duration: /5시간\s*30분/,
			advice: /수면.*조금.*더/
		},
		{
			minutes: 333,
			score: 41,
			grade: 4,
			label: '관리',
			duration: /5시간\s*33분/,
			advice: /수면.*조금.*더/
		},
		{
			minutes: 390,
			score: 70,
			grade: 2,
			label: '안정',
			duration: /6시간\s*30분/,
			advice: /수면 시간을 조금 더 확보해 보세요\.$/
		},
		{
			minutes: 478,
			score: 99,
			grade: 1,
			label: '훌륭',
			duration: /7시간\s*58분/,
			advice: /좋은.*수면|수면.*좋/
		},
		{
			minutes: 479,
			score: 100,
			grade: 1,
			label: '훌륭',
			duration: /7시간\s*59분/,
			advice: /좋은.*수면|수면.*좋/
		},
		{
			minutes: 480,
			score: 100,
			grade: 1,
			label: '훌륭',
			duration: /8시간/,
			advice: /좋은.*수면|수면.*좋/
		},
		{
			minutes: 539,
			score: 100,
			grade: 1,
			label: '훌륭',
			duration: /8시간\s*59분/,
			advice: /좋은.*수면|수면.*좋/
		},
		{
			minutes: 540,
			score: 90,
			grade: 1,
			label: '훌륭',
			duration: /9시간/,
			advice: /좋은.*수면|수면.*좋/
		},
		{
			minutes: 599,
			score: 90,
			grade: 1,
			label: '훌륭',
			duration: /9시간\s*59분/,
			advice: /좋은.*수면|수면.*좋/
		},
		{
			minutes: 600,
			score: 40,
			grade: 4,
			label: '관리',
			duration: /10시간/,
			advice: /패턴.*살펴|패턴.*살피/
		}
	] as const;
	for (const { minutes, score, grade, label, duration, advice } of cases) {
		const data = lifestyleScoreFixture();
		data.areas.sleep = { score, grade, sleepMinutes: minutes };
		const sleep = homeScoreStatuses(data, false, null)[1];
		expect(sleep, `${minutes}분`).toMatchObject({ score, label });
		expect(sleep.description, `${minutes}분`).toMatch(duration);
		expect(sleep.description, `${minutes}분`).toMatch(advice);
		expectFriendlyDescription(sleep.description);
		if (minutes === 479 || minutes >= 540)
			expect(sleep.description).not.toMatch(/부족|감점|더 자|조금.*더|늘려|늘리/);
	}
});

it('심박 5·7·10 bpm 경계에서도 실제 차이와 서버 상태에 맞는 변화 관찰 안내를 표시한다', () => {
	const cases = [
		{
			average: 63,
			baseline: 58,
			difference: 5,
			score: 100,
			grade: 1,
			label: '훌륭',
			advice: /패턴.*살펴|패턴.*살피/
		},
		{
			average: 63.1,
			baseline: 58,
			difference: 5.1,
			score: 70,
			grade: 2,
			label: '안정',
			advice: /다음 기록도 가볍게 살펴보세요\.$/
		},
		{
			average: 65,
			baseline: 58,
			difference: 7,
			score: 70,
			grade: 2,
			label: '안정',
			advice: /다음 기록도 가볍게 살펴보세요\.$/
		},
		{
			average: 65.1,
			baseline: 58,
			difference: 7.1,
			score: 55,
			grade: 3,
			label: '관심',
			advice: /평소보다 높은 기록이 이어지는지 며칠간 살펴보세요\.$/
		},
		{
			average: 67.9,
			baseline: 58,
			difference: 9.9,
			score: 55,
			grade: 3,
			label: '관심',
			advice: /평소보다 높은 기록이 이어지는지 며칠간 살펴보세요\.$/
		},
		{
			average: 68,
			baseline: 58,
			difference: 10,
			score: 40,
			grade: 4,
			label: '관리',
			advice: /며칠.*변화.*살펴|며칠.*변화.*살피/
		},
		{
			average: 65.4,
			baseline: 58.4,
			difference: 7,
			score: 70,
			grade: 2,
			label: '안정',
			advice: /다음 기록도 가볍게 살펴보세요\.$/
		},
		{
			average: 68.1,
			baseline: 58.1,
			difference: 10,
			score: 40,
			grade: 4,
			label: '관리',
			advice: /며칠.*변화.*살펴|며칠.*변화.*살피/
		}
	] as const;
	for (const { average, baseline, difference, score, grade, label, advice } of cases) {
		const data = lifestyleScoreFixture();
		data.areas.heart = {
			...data.areas.heart,
			score,
			grade,
			sleepHrAvg: average,
			baselineHrAvg: baseline
		};
		const heart = homeScoreStatuses(data, false, null)[2];
		expect(heart, `${difference} bpm`).toMatchObject({ score, label });
		expect(heart.description).toContain(`${average} bpm`);
		expect(heart.description).toMatch(
			new RegExp(`평소.*${String(baseline).replace('.', '\\.')}\\s*bpm`)
		);
		expect(heart.description).toContain(`${difference} bpm`);
		expect(heart.description).toMatch(/높/);
		expect(heart.description).toMatch(advice);
		expectFriendlyDescription(heart.description);
	}
});

it('수면 안정과 관심의 후속 안내를 구분하고 같은 등급에서도 실제 수면 시간을 보존한다', () => {
	const cases = [
		{
			minutes: 390,
			score: 70,
			grade: 2,
			label: '안정',
			duration: /6시간\s*30분/,
			advice: '수면 시간을 조금 더 확보해 보세요.'
		},
		{
			minutes: 405,
			score: 75,
			grade: 2,
			label: '안정',
			duration: /6시간\s*45분/,
			advice: '수면 시간을 조금 더 확보해 보세요.'
		},
		{
			minutes: 360,
			score: 55,
			grade: 3,
			label: '관심',
			duration: /6시간/,
			advice: '충분히 쉴 수 있도록 수면 시간을 더 확보해 보세요.'
		},
		{
			minutes: 366,
			score: 58,
			grade: 3,
			label: '관심',
			duration: /6시간\s*6분/,
			advice: '충분히 쉴 수 있도록 수면 시간을 더 확보해 보세요.'
		}
	] as const;
	const descriptions = cases.map(({ minutes, score, grade, label, duration, advice }) => {
		const data = lifestyleScoreFixture();
		data.areas.sleep = { score, grade, sleepMinutes: minutes };
		const sleep = homeScoreStatuses(data, false, null)[1];
		expect(sleep).toMatchObject({ score, label });
		expect(sleep.description).toMatch(duration);
		expect(sleep.description.split(/(?<=[.!?])\s+/u)[1]).toBe(advice);
		expectFriendlyDescription(sleep.description);
		return sleep.description;
	});
	expect(descriptions[0]).not.toBe(descriptions[1]);
	expect(descriptions[2]).not.toBe(descriptions[3]);
	expect(descriptions[0].split(/(?<=[.!?])\s+/u)[1]).not.toBe(
		descriptions[2].split(/(?<=[.!?])\s+/u)[1]
	);
});

it('심박 안정과 관심의 후속 안내를 구분하고 같은 점수·등급에서도 실제 두 값과 차이를 보존한다', () => {
	const cases = [
		{
			current: 64.5,
			baseline: 58,
			difference: 6.5,
			score: 70,
			grade: 2,
			label: '안정',
			advice: '다음 기록도 가볍게 살펴보세요.'
		},
		{
			current: 64.8,
			baseline: 58.2,
			difference: 6.6,
			score: 70,
			grade: 2,
			label: '안정',
			advice: '다음 기록도 가볍게 살펴보세요.'
		},
		{
			current: 65.1,
			baseline: 58,
			difference: 7.1,
			score: 55,
			grade: 3,
			label: '관심',
			advice: '평소보다 높은 기록이 이어지는지 며칠간 살펴보세요.'
		},
		{
			current: 67.9,
			baseline: 58,
			difference: 9.9,
			score: 55,
			grade: 3,
			label: '관심',
			advice: '평소보다 높은 기록이 이어지는지 며칠간 살펴보세요.'
		}
	] as const;
	const descriptions = cases.map(
		({ current, baseline, difference, score, grade, label, advice }) => {
			const data = lifestyleScoreFixture();
			data.areas.heart = {
				...data.areas.heart,
				score,
				grade,
				sleepHrAvg: current,
				baselineHrAvg: baseline
			};
			const heart = homeScoreStatuses(data, false, null)[2];
			expect(heart).toMatchObject({ score, label });
			expect(heart.description).toContain(`${current} bpm`);
			expect(heart.description).toContain(`평소 ${baseline} bpm`);
			expect(heart.description).toContain(`${difference} bpm 높`);
			expect(heart.description.split(/(?<=[.!?])\s+/u)[1]).toBe(advice);
			expectFriendlyDescription(heart.description);
			return heart.description;
		}
	);
	expect(descriptions[0]).not.toBe(descriptions[1]);
	expect(descriptions[2]).not.toBe(descriptions[3]);
	expect(descriptions[0].split(/(?<=[.!?])\s+/u)[1]).not.toBe(
		descriptions[2].split(/(?<=[.!?])\s+/u)[1]
	);
});

it.each([
	{ area: 'sleep', mismatch: '등급', value: 366, score: 58, grade: 2, label: '안정' },
	{ area: 'sleep', mismatch: '등급', value: 390, score: 70, grade: 3, label: '관심' },
	{ area: 'sleep', mismatch: '측정값', value: 366, score: 70, grade: 2, label: '안정' },
	{ area: 'sleep', mismatch: '측정값', value: 390, score: 58, grade: 3, label: '관심' },
	{ area: 'heart', mismatch: '등급', value: 65.1, score: 55, grade: 2, label: '안정' },
	{ area: 'heart', mismatch: '등급', value: 64.5, score: 70, grade: 3, label: '관심' },
	{ area: 'heart', mismatch: '측정값', value: 65.1, score: 70, grade: 2, label: '안정' },
	{ area: 'heart', mismatch: '측정값', value: 64.5, score: 55, grade: 3, label: '관심' }
] as const)(
	'$area $label 응답의 $mismatch 불일치에서는 실측값과 서버 상태만 보존하고 추가 안내를 붙이지 않는다',
	({ area, value, score, grade, label }) => {
		const data = lifestyleScoreFixture();
		if (area === 'sleep') data.areas.sleep = { score, grade, sleepMinutes: value };
		else
			data.areas.heart = {
				...data.areas.heart,
				score,
				grade,
				sleepHrAvg: value,
				baselineHrAvg: 58
			};
		const before = structuredClone(data);
		const status = homeScoreStatuses(data, false, null)[area === 'sleep' ? 1 : 2];
		expect(status).toMatchObject({ score, label });
		if (area === 'sleep')
			expect(status.description).toMatch(value === 366 ? /6시간\s*6분/ : /6시간\s*30분/);
		else {
			expect(status.description).toContain(`${value} bpm`);
			expect(status.description).toContain('평소 58 bpm');
			expect(status.description).toContain(`${value === 65.1 ? 7.1 : 6.5} bpm 높`);
		}
		expect(status.description.split(/(?<=[.!?])\s+/u)).toHaveLength(1);
		expect(status.description).not.toMatch(/확보|충분히 쉴|다음 기록|이어지는지|살펴|살피|좋은/);
		expectFriendlyDescription(status.description);
		expect(data).toEqual(before);
	}
);

it('API 점수와 측정값이 어긋나면 저장값을 보존하고 칭찬이나 개선 조언을 단정하지 않는다', () => {
	const data = lifestyleScoreFixture();
	data.areas.activity.score = 84;
	data.areas.activity.grade = 1;
	data.areas.sleep = { score: 12, grade: 1, sleepMinutes: 366 };
	data.areas.heart = { ...data.areas.heart, score: 40, grade: 1 };
	const before = structuredClone(data);
	const [activity, sleep, heart] = homeScoreStatuses(data, false, null);
	expect(activity).toMatchObject({ score: 84, label: '훌륭' });
	expect(activity.description).toMatch(/7,200보/);
	expect(activity.description).toMatch(/인정 운동(?:은)?\s*19분/);
	expect(sleep).toMatchObject({ score: 12, label: '훌륭' });
	expect(sleep.description).toMatch(/6시간\s*6분/);
	expect(heart).toMatchObject({ score: 40, label: '훌륭' });
	expect(heart.description).toMatch(/64\.5\s*bpm/);
	expect(heart.description).toMatch(/6\.5\s*bpm.*높/);
	for (const { description } of [activity, sleep, heart]) {
		expectFriendlyDescription(description);
		expect(description).not.toMatch(/좋은|늘려|늘리|더해|꾸준|살펴|살피|조금.*더/);
	}
	expect(data).toEqual(before);

	// 원본 차이는 7을 넘지만 표시한 두 값의 차이는 7이어서 같은 구간으로 단정할 수 없다.
	data.areas.heart = { ...data.areas.heart, score: 55, grade: 3, sleepHrAvg: 65.04 };
	const rounded = homeScoreStatuses(data, false, null)[2];
	expect(rounded).toMatchObject({ score: 55, label: '관심' });
	expect(rounded.description).toMatch(/65\s*bpm/);
	expect(rounded.description).toMatch(/7\s*bpm.*높/);
	expectFriendlyDescription(rounded.description);
	expect(rounded.description).not.toMatch(/살펴|살피/);

	// 표시값만 보면 70점 구간이어도 원본 차이 7.04와 맞지 않으면 그 구간을 근거로 삼지 않는다.
	data.areas.heart.score = 70;
	data.areas.heart.grade = 2;
	const rawMismatch = homeScoreStatuses(data, false, null)[2];
	expect(rawMismatch).toMatchObject({ score: 70, label: '안정' });
	expect(rawMismatch.description).toMatch(/7\s*bpm.*높/);
	expectFriendlyDescription(rawMismatch.description);
	expect(rawMismatch.description).not.toMatch(/살펴|살피/);
});

it.each([
	{
		steps: 8400,
		stepsScore: 60,
		minutes: 30,
		exerciseScore: 40,
		score: 100,
		grade: 1,
		label: '훌륭',
		advice: /좋은.*활동|활동.*좋/
	},
	{
		steps: 5400,
		stepsScore: 48,
		minutes: 24,
		exerciseScore: 32,
		score: 80,
		grade: 1,
		label: '훌륭',
		advice: /좋은.*활동|활동.*좋/
	},
	{
		steps: 5400,
		stepsScore: 48,
		minutes: 18,
		exerciseScore: 24,
		score: 72,
		grade: 2,
		label: '안정',
		advice: /꾸준/
	},
	{
		steps: 3200,
		stepsScore: 30,
		minutes: 19,
		exerciseScore: 25,
		score: 55,
		grade: 3,
		label: '관심',
		advice: /걷기.*늘|걸음.*늘/
	},
	{
		steps: 3200,
		stepsScore: 30,
		minutes: 6,
		exerciseScore: 8,
		score: 38,
		grade: 4,
		label: '관리',
		advice: /운동[^.!?]*(늘|더해)/
	},
	{
		steps: 800,
		stepsScore: 0,
		minutes: 10,
		exerciseScore: 13,
		score: 13,
		grade: 4,
		label: '관리',
		advice: /걷기.*늘|걸음.*늘/
	}
] as const)(
	'활동 $score점은 실제 $steps보·$minutes분과 상태에 맞는 안내를 표시한다',
	({ steps, stepsScore, minutes, exerciseScore, score, grade, label, advice }) => {
		const data = lifestyleScoreFixture();
		data.areas.activity = {
			score,
			grade,
			steps: { value: steps, score: stepsScore },
			exercise: { minutes, score: exerciseScore }
		};
		const activity = homeScoreStatuses(data, false, null)[0];
		expect(activity).toMatchObject({ score, label });
		expect(activity.description).toContain(`${steps.toLocaleString('ko-KR')}보`);
		expect(activity.description).toContain(`${minutes}분`);
		expect(activity.description).toMatch(advice);
		expectFriendlyDescription(activity.description);
	}
);

it('같은 활동 점수·등급에서도 실제 걸음·운동 기록에 따라 부족한 활동의 안내를 바꾼다', () => {
	const data = lifestyleScoreFixture();
	data.areas.activity = {
		score: 48,
		grade: 4,
		steps: { value: 5000, score: 48 },
		exercise: { minutes: 0, score: 0 }
	};
	const first = homeScoreStatuses(data, false, null)[0];
	expect(first).toMatchObject({ score: 48, label: '관리' });
	expect(first.description).toMatch(/5,000보.*0분/);
	expect(first.description).toMatch(/운동[^.!?]*(늘|더해)/);

	data.areas.activity.steps = { value: 2500, score: 15 };
	data.areas.activity.exercise = { minutes: 25, score: 33 };
	const changed = homeScoreStatuses(data, false, null)[0];
	expect(changed).toMatchObject({ score: 48, label: '관리' });
	expect(changed.description).toMatch(/2,500보.*25분/);
	expect(changed.description).toMatch(/걷기.*늘|걸음.*늘/);
	expect(changed.description).not.toContain('5,000보');
	expectFriendlyDescription(first.description);
	expectFriendlyDescription(changed.description);
});

it.each(['activity', 'sleep', 'heart'] as const)(
	'%s 점수만 있으면 측정값 정보가 없음을 알리고 계산 실패를 단정하지 않는다',
	(area) => {
		const data = lifestyleScoreFixture();
		if (area === 'activity') {
			data.areas.activity.steps.value = null;
			data.areas.activity.exercise.minutes = null;
		} else if (area === 'sleep') {
			data.areas.sleep.sleepMinutes = null;
		} else {
			data.areas.heart.sleepHrAvg = null;
			data.areas.heart.baselineHrAvg = null;
		}
		const before = structuredClone(data);
		const index = { activity: 0, sleep: 1, heart: 2 }[area];
		const status = homeScoreStatuses(data, false, null)[index];
		expect(status.score).toBe(data.areas[area].score);
		expect(status.description).toMatch(/점수.*확인/);
		expect(status.description).toMatch(/(정보|기록).*(없|확인.*(불가|어려))/);
		expect(status.description).not.toMatch(
			/계산하지 못|계산할 수 없|좋은|늘려|늘리|더해|꾸준|살펴|살피/
		);
		expect(status.description).not.toMatch(/\d+(?:\.\d+)?\s*(보|분|bpm)/);
		expectFriendlyDescription(status.description);
		expect(data).toEqual(before);
	}
);

it.each(['activity', 'sleep', 'heart'] as const)(
	'%s 측정값만 있으면 실제 기록과 점수가 아직 확인되지 않았음을 함께 안내한다',
	(area) => {
		const data = lifestyleScoreFixture();
		data.areas[area].score = null;
		data.areas[area].grade = null;
		if (area === 'activity') {
			data.areas.activity.steps.score = null;
			data.areas.activity.exercise.score = null;
		}
		const index = { activity: 0, sleep: 1, heart: 2 }[area];
		const status = homeScoreStatuses(data, false, null)[index];
		expect(status).toMatchObject({ score: null, label: '기록 없음' });
		if (area === 'activity') {
			expect(status.description).toMatch(/7,200보/);
			expect(status.description).toMatch(/19분/);
		} else if (area === 'sleep') {
			expect(status.description).toMatch(/6시간\s*6분/);
		} else {
			expect(status.description).toMatch(/64\.5\s*bpm/);
			expect(status.description).toMatch(/평소.*58\s*bpm/);
			expect(status.description).toMatch(/6\.5\s*bpm.*높/);
		}
		expect(status.description).toMatch(/점수.*아직.*확인/);
		expect(status.description).not.toMatch(/좋은|늘려|늘리|더해|꾸준|살펴|살피|계산하지 못/);
		expectFriendlyDescription(status.description);
	}
);

it.each(['steps-value', 'exercise-minutes', 'steps-score', 'exercise-score'] as const)(
	'활동 상세의 %s만 없어도 확인한 실제 값은 유지하고 코칭을 단정하지 않는다',
	(missing) => {
		const data = lifestyleScoreFixture();
		if (missing === 'steps-value') data.areas.activity.steps.value = null;
		if (missing === 'exercise-minutes') data.areas.activity.exercise.minutes = null;
		if (missing === 'steps-score') data.areas.activity.steps.score = null;
		if (missing === 'exercise-score') data.areas.activity.exercise.score = null;
		const activity = homeScoreStatuses(data, false, null)[0];
		expect(activity).toMatchObject({ score: 85, label: '훌륭' });
		if (missing === 'steps-value') {
			expect(activity.description).toMatch(/걸음.*확인.*(불가|할 수 없)/);
			expect(activity.description).not.toContain('7,200보');
		} else expect(activity.description).toContain('7,200보');
		if (missing === 'exercise-minutes') {
			expect(activity.description).toMatch(/운동.*시간.*확인.*(불가|할 수 없)/);
			expect(activity.description).not.toMatch(/19분|0분/);
		} else expect(activity.description).toContain('19분');
		expect(activity.description).not.toMatch(/좋은|늘려|늘리|더해|꾸준|살펴|살피/);
		expectFriendlyDescription(activity.description);
	}
);

it.each(['activity', 'sleep', 'heart'] as const)(
	'%s 점수와 등급이 어긋나도 서버 표시를 유지하고 코칭을 생략한다',
	(area) => {
		const data = lifestyleScoreFixture();
		data.areas[area].grade = area === 'sleep' ? 1 : 4;
		const before = structuredClone(data);
		const index = { activity: 0, sleep: 1, heart: 2 }[area];
		const status = homeScoreStatuses(data, false, null)[index];
		expect(status).toMatchObject({
			score: data.areas[area].score,
			label: area === 'sleep' ? '훌륭' : '관리'
		});
		expect(status.description).not.toMatch(/좋은|늘려|늘리|더해|꾸준|살펴|살피|조금.*더/);
		expect(status.description).toMatch(
			area === 'activity'
				? /7,200보.*19분/
				: area === 'sleep'
					? /6시간\s*6분/
					: /64\.5\s*bpm.*58\s*bpm.*6\.5\s*bpm/
		);
		expectFriendlyDescription(status.description);
		expect(data).toEqual(before);
	}
);

it.each([
	{ average: 58, difference: /58\s*bpm.*같/, checkWear: false },
	{ average: 55.5, difference: /2\.5\s*bpm.*낮/, checkWear: false },
	{ average: 48, difference: /10\s*bpm.*낮/, checkWear: false },
	{ average: 48, difference: /10\s*bpm.*낮/, checkWear: true }
])(
	'심박 $average bpm의 낮음·같음은 의학 판단 없이 안내하고 checkWear=$checkWear를 따른다',
	({ average, difference, checkWear }) => {
		const data = lifestyleScoreFixture();
		data.areas.heart = {
			...data.areas.heart,
			score: 100,
			grade: 1,
			sleepHrAvg: average,
			baselineHrAvg: 58,
			checkWear
		};
		const heart = homeScoreStatuses(data, false, null)[2];
		expect(heart).toMatchObject({ score: 100, label: '훌륭' });
		expect(heart.description).toContain(`${average} bpm`);
		expect(heart.description).toMatch(/평소.*58\s*bpm/);
		expect(heart.description).toMatch(difference);
		if (checkWear) expect(heart.description).toMatch(/착용.*확인/);
		else {
			expect(heart.description).toMatch(/패턴.*살펴|패턴.*살피/);
			expect(heart.description).not.toMatch(/착용/);
		}
		expectFriendlyDescription(heart.description);
	}
);

it.each([
	{ missing: '현재값', current: null, baseline: 58, score: 70, grade: 2, label: '안정' },
	{ missing: '현재값', current: null, baseline: 58, score: null, grade: null, label: '기록 없음' },
	{ missing: '평소값', current: 64.5, baseline: null, score: 70, grade: 2, label: '안정' },
	{
		missing: '평소값',
		current: 64.5,
		baseline: null,
		score: null,
		grade: null,
		label: '기록 없음'
	},
	{ missing: '두 값', current: null, baseline: null, score: 70, grade: 2, label: '안정' },
	{ missing: '두 값', current: null, baseline: null, score: null, grade: null, label: '기록 없음' }
] as const)(
	'심박 $missing 누락·점수 $score에서도 확인한 기록과 착용 확인 안내를 유지한다',
	({ current, baseline, score, grade, label }) => {
		const data = lifestyleScoreFixture();
		data.areas.heart = {
			...data.areas.heart,
			score,
			grade,
			sleepHrAvg: current,
			baselineHrAvg: baseline,
			baselineNights: baseline === null ? 0 : 20,
			checkWear: true
		};
		const before = structuredClone(data);
		const heart = homeScoreStatuses(data, false, null)[2];
		expect(heart).toMatchObject({ score, label });
		expect(heart.description).toMatch(/워치.*착용.*확인/);
		if (current === null) {
			expect(heart.description).not.toContain('64.5 bpm');
			if (baseline !== null) expect(heart.description).toMatch(/오늘.*(측정값|심박).*확인.*없/);
		} else expect(heart.description).toContain('64.5 bpm');
		if (baseline === null) {
			expect(heart.description).not.toContain('58 bpm');
			if (current !== null) expect(heart.description).toMatch(/평소.*확인.*없/);
		} else expect(heart.description).toMatch(/평소.*58\s*bpm/);
		if (current === null && baseline === null) {
			expect(heart.description).not.toMatch(/\d+(?:\.\d+)?\s*bpm/);
			expect(heart.description).toMatch(/심박.*(기록|정보).*(없|확인.*없)/);
		}
		expect(heart.description).not.toMatch(/6\.5\s*bpm|(?:^|\s)0\s*bpm|높|낮|같/);
		expect(heart.description).not.toMatch(/좋은|늘려|늘리|더해|꾸준|살펴|살피|계산하지 못/);
		expectFriendlyDescription(heart.description);
		expect(data).toEqual(before);
	}
);

it.each(['BEFORE_LINK', 'UNSYNCED', 'NO_RECORD'] as const)(
	'행 없는 %s 응답의 null·사유와 사용자 상태를 유지한다',
	async (reason) => {
		const data = lifestyleScoreFixture();
		data.reason = reason;
		data.computedAt = null;
		data.total = { score: null, missingAreas: ['activity', 'sleep', 'heart'] };
		data.areas = {
			activity: {
				score: null,
				grade: null,
				steps: { score: null, value: null },
				exercise: { score: null, minutes: null }
			},
			sleep: { score: null, grade: null, sleepMinutes: null },
			heart: {
				score: null,
				grade: null,
				sleepHrAvg: null,
				baselineHrAvg: null,
				baselineNights: null,
				checkWear: null
			}
		};
		const result = await getLifestyleScore(
			data.date,
			undefined,
			createApiClient({ fetch: async () => Response.json({ data }) })
		);
		expect(result).toEqual(data);
		const expected = { BEFORE_LINK: '연동 전', UNSYNCED: '동기화 대기', NO_RECORD: '기록 없음' }[
			reason
		];
		expect(
			homeScoreStatuses(result, false, null).every(
				(status) => status.label === expected && status.score === null
			)
		).toBe(true);
		for (const status of homeScoreStatuses(result, false, null)) {
			expect(status.description).not.toMatch(
				/반영한 점수|비교한 점수|평균 심박은|실제 수면 시간은/
			);
			expect(status.description).not.toMatch(/\d+(?:\.\d+)?\s*(보|분|bpm)/);
		}
	}
);

it('실제 0점과 null을 구분하고 기준선이 없어 심박 점수가 null이어도 수면 심박 원본을 보존한다', async () => {
	const data = lifestyleScoreFixture();
	data.total = { score: 0, missingAreas: ['sleep', 'heart'] };
	data.areas.activity = {
		score: 0,
		grade: 4,
		steps: { value: 800, score: 0 },
		exercise: { minutes: 0, score: 0 }
	};
	data.areas.sleep = { score: null, grade: null, sleepMinutes: null };
	data.areas.heart = {
		score: null,
		grade: null,
		sleepHrAvg: 64.5,
		baselineHrAvg: null,
		baselineNights: 0,
		checkWear: false
	};
	const result = await getLifestyleScore(
		data.date,
		undefined,
		createApiClient({ fetch: async () => Response.json({ data }) })
	);
	expect(result.total.score).toBe(0);
	expect(result.areas.activity.exercise.minutes).toBe(0);
	expect(result.areas.heart.sleepHrAvg).toBe(64.5);
	expect(homeScoreStatuses(result, false, null)[0]).toMatchObject({
		score: 0,
		label: '관리',
		tone: 'danger'
	});
	expect(homeScoreStatuses(result, false, null)[2]).toMatchObject({
		score: null,
		label: '기록 없음'
	});
});

it('없는 등급을 거부하고 API·네트워크 실패를 예시 점수로 대체하지 않는다', async () => {
	const data = lifestyleScoreFixture();
	const malformed = { ...data, areas: { ...data.areas, sleep: { ...data.areas.sleep, grade: 5 } } };
	await expect(
		getLifestyleScore(
			data.date,
			undefined,
			createApiClient({ fetch: async () => Response.json({ data: malformed }) })
		)
	).rejects.toThrow('grade');
	await expect(
		getLifestyleScore(
			data.date,
			undefined,
			createApiClient({
				fetch: async () =>
					Response.json(
						{ error: { code: 'HEALTH_DATA_UNAVAILABLE', message: '점수 준비 중' } },
						{ status: 503 }
					)
			})
		)
	).rejects.toMatchObject({ status: 503 });
	await expect(
		getLifestyleScore(
			data.date,
			undefined,
			createApiClient({
				fetch: async () => {
					throw new TypeError('Network error');
				}
			})
		)
	).rejects.toThrow('Network error');
	expect(homeRequestError(new TypeError('Failed to fetch'))).toBe(
		'건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'
	);
});

it.each([
	['DATA_NOT_READY', '건강 기록을 준비 중이에요. 잠시 후 다시 확인해 주세요.'],
	['LOOKUP_FAILED', '건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'],
	[null, '건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.']
])(
	'Home 오류 %s는 기술 메시지·요청 ID 없이 서버 사유에 맞게 안내한다',
	async (reason, expected) => {
		const error = await createApiClient({
			fetch: async () =>
				Response.json(
					{
						error: {
							code: 'HEALTH_DATA_UNAVAILABLE',
							message: 'Internal lookup failed',
							context: reason ? { reason, metrics: ['steps'] } : null
						}
					},
					{ status: 503, headers: { 'X-Request-Id': '00000000-0000-4000-8000-000000000001' } }
				)
		})
			.request('/health/lifestyle-score')
			.catch((error: unknown) => error);
		expect(homeRequestError(error)).toBe(expected);
	}
);

it.each([
	{
		status: 503,
		code: 'HEALTH_DATA_UNAVAILABLE',
		context: { reason: 'DATA_NOT_READY' },
		preparing: true
	},
	{
		status: 500,
		code: 'HEALTH_DATA_UNAVAILABLE',
		context: { reason: 'DATA_NOT_READY' },
		preparing: false
	},
	{
		status: 409,
		code: 'HEALTH_DATA_UNAVAILABLE',
		context: { reason: 'DATA_NOT_READY' },
		preparing: false
	},
	{ status: 503, code: 'INTERNAL_ERROR', context: { reason: 'DATA_NOT_READY' }, preparing: false },
	{
		status: 503,
		code: 'HEALTH_DATA_UNAVAILABLE',
		context: { reason: 'LOOKUP_FAILED' },
		preparing: false
	},
	{ status: 503, code: 'HEALTH_DATA_UNAVAILABLE', context: null, preparing: false },
	{ status: 503, code: 'HEALTH_DATA_UNAVAILABLE', context: { reason: 'UNKNOWN' }, preparing: false }
])(
	'준비 상태는 HTTP·오류 코드·사유가 모두 일치해야 한다: $status/$code/$context',
	async ({ status, code, context, preparing }) => {
		const client = createApiClient({
			fetch: async () =>
				Response.json({ error: { code, context, message: '진단 원문' } }, { status })
		});
		const error = await client.request('/health/lifestyle-score').catch((value: unknown) => value);
		expect(isHealthDataNotReady(error)).toBe(preparing);
		expect(homeRequestError(error)).toBe(
			preparing
				? '건강 기록을 준비 중이에요. 잠시 후 다시 확인해 주세요.'
				: '건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'
		);
	}
);

it('네트워크 오류나 오류처럼 생긴 일반 객체를 준비 상태로 바꾸지 않는다', () => {
	expect(isHealthDataNotReady(new TypeError('Failed to fetch'))).toBe(false);
	expect(
		isHealthDataNotReady({
			status: 503,
			code: 'HEALTH_DATA_UNAVAILABLE',
			context: { reason: 'DATA_NOT_READY' }
		})
	).toBe(false);
});

it('점수 준비·실제 오류·정상 무기록을 서로 다른 상태로 표시하고 수치를 만들지 않는다', () => {
	const preparing = homeScoreStatuses(null, false, '건강 기록을 준비 중이에요.', true);
	expect(preparing.map((area) => area.label)).toEqual(['준비 중', '준비 중', '준비 중']);
	expect(preparing.map((area) => area.score)).toEqual([null, null, null]);
	expect(homeScoreStatuses(null, false, '조회 오류').map((area) => area.label)).toEqual([
		'조회 실패',
		'조회 실패',
		'조회 실패'
	]);
	expect(homeScoreStatuses(null, false, null).map((area) => area.label)).not.toContain('준비 중');
	expect(homeScoreStatuses(null, true, null, true).map((area) => area.label)).toEqual([
		'불러오는 중',
		'불러오는 중',
		'불러오는 중'
	]);
	const data = lifestyleScoreFixture();
	const pendingStatuses = [
		homeScoreStatuses(data, true, null),
		homeScoreStatuses(data, false, '건강 기록을 준비 중이에요.', true),
		homeScoreStatuses(data, false, '조회 오류')
	];
	for (const statuses of pendingStatuses) {
		for (const status of statuses) {
			expect(status.description).not.toMatch(
				/반영한 점수|비교한 점수|평균 심박은|실제 수면 시간은/
			);
			expect(status.description).not.toMatch(/7,200보|19분|6시간\s*6분|64\.5\s*bpm/);
		}
	}
});
