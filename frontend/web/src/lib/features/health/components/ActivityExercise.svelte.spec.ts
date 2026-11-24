import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ActivityExercise from './ActivityExercise.svelte';
import { getActivityPeriod, exerciseSessions } from '../activity';

it('시간의 총합·평균·일별 최고는 칼로리와 같은 위치에 표시하며 0과 결측을 구분한다', async () => {
	const period = getActivityPeriod(0);
	const { rerender } = render(ActivityExercise, {
		period,
		serverTime: { avg: 42.5, total: 85, values: [45, 40, null, null, null, null, null] }
	});
	const card = page.getByRole('region', { name: '운동 시간', exact: true });
	expect(card.element().querySelector(':scope > div:first-child > p')?.textContent?.trim()).toBe(
		'총 1시간 25분'
	);
	expect(card.element().querySelector(':scope > p')?.textContent?.trim()).toBe('평균 43분');
	const maximum = card.element().querySelectorAll(':scope > p')[1];
	expect(maximum?.textContent?.trim()).toBe('최고 45분');
	expect(maximum?.className).toBe(
		page
			.getByRole('region', { name: '소모 칼로리', exact: true })
			.element()
			.querySelectorAll(':scope > p')[1]?.className
	);
	await expect.element(card.getByText('최고 45분', { exact: true })).toBeVisible();
	await rerender({
		serverTime: { avg: 0, total: 0, values: [0, null, null, null, null, null, null] }
	});
	await expect.element(card.getByText('최고 0분', { exact: true })).toBeVisible();
	await rerender({
		serverTime: { avg: 60.1, total: 60.1, values: [60.1, null, null, null, null, null, null] }
	});
	await expect.element(card.getByText('최고 1시간 0분', { exact: true })).toBeVisible();
	expect(chartPointY(card.element().querySelector('[data-trend-point]'))).toBeCloseTo(18, 12);
	await rerender({ serverTime: { avg: null, total: null, values: Array(7).fill(null) } });
	await expect
		.element(card.getByText('선택한 기간에 기록이 없어요.', { exact: true }))
		.toBeVisible();
	await expect.element(card.getByText(/^최고 /)).not.toBeInTheDocument();
});

it('운동 심박 평균의 끝자리 오차만 정리하고 소수·단일 세션·null과 좌표를 유지한다', async () => {
	const period = getActivityPeriod(0);
	const rawAverage = (70.2 + 70.4) / 2;
	render(ActivityExercise, {
		period,
		sessions: [70.2, 70.4, 72.55, 70, null].map((avgHeartRate, index) => ({
			...exerciseSessions[0],
			id: `precision-${index}`,
			date: period.days[Math.max(0, index - 1)].date,
			avgHeartRate
		}))
	});
	const card = page.getByRole('region', { name: '운동 중 심박수', exact: true });
	await expect.element(card.getByText('평균 71 bpm', { exact: true })).toBeVisible();
	for (const [index, label] of ['70.3 bpm', '72.55 bpm', '70 bpm', '—'].entries()) {
		card.element().querySelector<HTMLButtonElement>(`[data-chart-point="${index}"]`)?.focus();
		await expect.element(page.getByRole('tooltip').getByText(label, { exact: true })).toBeVisible();
	}
	expect(rawAverage).toBe(70.30000000000001);
	expect(chartPointY(card.element().querySelector('[data-trend-point]'))).toBeCloseTo(
		58 - (rawAverage / 72.55) * 40,
		12
	);
});

it('칼로리 표시만 한 자리로 정리하고 심박 소수와 그래프 좌표는 유지한다', async () => {
	const period = getActivityPeriod(0);
	const value = 123.45000000000002;
	render(ActivityExercise, {
		period,
		sessions: [{ ...exerciseSessions[0], caloriesKcal: value, avgHeartRate: 72.55 }]
	});
	const card = page.getByRole('region', { name: '소모 칼로리', exact: true });
	await expect.element(card.getByText('평균 123 kcal', { exact: true })).toBeVisible();
	expect(card.element().querySelector(':scope > div:first-child > p')?.textContent?.trim()).toBe(
		'총 123.5 kcal'
	);
	expect(card.element().querySelectorAll(':scope > p')[1]?.textContent?.trim()).toBe(
		'최고 123.5 kcal'
	);
	card
		.getByRole('button', { name: `${period.days[0].date} 소모 칼로리 123.5 kcal`, exact: true })
		.element()
		.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('123.5 kcal');
	expect(chartPointY(card.element().querySelector('[data-trend-point]'))).toBeCloseTo(18, 12);
	page
		.getByRole('region', { name: '운동 중 심박수', exact: true })
		.element()
		.querySelector<HTMLButtonElement>('[data-chart-point="0"]')
		?.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('72.55 bpm');
});

it('세 지표의 0·null·정상값을 날짜별로 구분하고 심박 툴팁만 대시로 표현한다', async () => {
	const period = getActivityPeriod(0);
	render(ActivityExercise, {
		period,
		serverTime: { avg: 15, total: 30, values: [0, null, 30, null, null, null, null] },
		sessions: [0, null, 72].map((value, index) => ({
			...exerciseSessions[0],
			id: `session-${index}`,
			date: period.days[index].date,
			caloriesKcal: value,
			avgHeartRate: value,
			maxHeartRate: value
		}))
	});
	for (const [title, zeroLabel, zeroTooltip, normalTooltip] of [
		['운동 시간', '0시간 0분', '0시간 0분', '0시간 30분'],
		['소모 칼로리', '0 kcal', '0 kcal', '72 kcal'],
		['운동 중 심박수', '0 bpm', '—', '72 bpm']
	]) {
		const card = page.getByRole('region', { name: title, exact: true });
		const circles = card.element().querySelectorAll('[data-trend-point]');
		expect(circles).toHaveLength(2);
		expect(circles[0].getAttribute('data-date')).toBe(period.days[0].date);
		expect(chartPointY(circles[0])).toBeCloseTo(58, 12);
		expect(circles[1].getAttribute('data-date')).toBe(period.days[2].date);
		expect(chartPointY(circles[1])).toBeCloseTo(18, 12);
		// null은 측정점 없이 기준선으로 연결하고 툴팁에서는 대시를 표시한다.
		expect(card.element().querySelectorAll('polyline')).toHaveLength(1);
		expect(
			card.element().querySelector('polyline')?.getAttribute('points')?.split(' ')
		).toHaveLength(7);
		expect(card.element().querySelectorAll('svg > line[y1="58"]')).toHaveLength(0);
		expect(card.element().querySelectorAll('[data-chart-point]')).toHaveLength(7);
		const zero = card.getByRole('button', {
			name: `${period.days[0].date} ${title} ${zeroLabel}`,
			exact: true
		});
		zero.element().focus();
		await expect.element(page.getByRole('tooltip')).toHaveTextContent(zeroTooltip);
		expect(page.getByRole('tooltip').element().querySelector('strong')?.textContent).toBe(
			zeroTooltip
		);
		card.element().querySelector<HTMLButtonElement>('[data-chart-point="2"]')?.focus();
		await expect.element(page.getByRole('tooltip')).toHaveTextContent(normalTooltip);
		const missing = card.getByRole('button', {
			name: `${period.days[1].date} ${title} —`,
			exact: true
		});
		missing.element().focus();
		await expect
			.element(page.getByRole('tooltip'))
			.toHaveTextContent(period.days[1].date.replaceAll('-', '.'));
		expect(page.getByRole('tooltip').element().querySelector('strong')?.textContent).toBe('—');
		(document.activeElement as HTMLElement)?.blur();
		await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	}
});

it('칼로리 0은 유지하고 심박 0은 툴팁만 대시로 표시하며 null은 채우지 않는다', async () => {
	const period = getActivityPeriod(0);
	const { rerender } = render(ActivityExercise, {
		period,
		sessions: [{ ...exerciseSessions[0], caloriesKcal: 0, avgHeartRate: 0, maxHeartRate: 0 }]
	});
	await expect.element(page.getByText('평균 0 kcal', { exact: true })).toBeVisible();
	await expect.element(page.getByText('평균 0 bpm', { exact: true })).toBeVisible();
	const heartCard = page.getByRole('region', { name: '운동 중 심박수', exact: true }).element();
	expect(heartCard.querySelector(':scope > p')?.textContent?.trim()).toBe('평균 0 bpm');
	expect(heartCard.textContent).toContain('최고 0 bpm');
	expect(document.querySelectorAll('.activity-trend [stroke-dasharray]')).toHaveLength(0);
	for (const label of ['소모 칼로리 0 kcal', '운동 중 심박수 0 bpm']) {
		const point = page.getByRole('button', {
			name: `${period.days[0].date} ${label}`,
			exact: true
		});
		point.element().focus();
		await expect
			.element(page.getByRole('tooltip'))
			.toHaveTextContent(label.endsWith('kcal') ? '0 kcal' : '—');
		if (label.startsWith('운동 중'))
			expect(page.getByRole('tooltip').element().querySelector('strong')?.textContent).toBe('—');
	}
	expect(document.querySelectorAll('.activity-trend [data-trend-point]')).toHaveLength(3);
	// null 표시는 별도 데이터 상태로 검증한다. 현재 포커스의 해제도 확인한다.
	(document.activeElement as HTMLElement)?.blur();
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	await rerender({
		sessions: [
			{ ...exerciseSessions[0], caloriesKcal: null, avgHeartRate: null, maxHeartRate: null }
		]
	});
	await expect.element(page.getByText('평균 0 kcal', { exact: true })).not.toBeInTheDocument();
	await expect.element(page.getByText('평균 0 bpm', { exact: true })).not.toBeInTheDocument();
	expect(document.querySelectorAll('.activity-trend [data-trend-point]')).toHaveLength(1);
	expect(heartCard.querySelector(':scope > p')?.textContent?.trim()).toBe('기록 없음');
});

it('정상 심박 표기를 유지하며 0으로 갱신해도 점을 제거하지 않는다', async () => {
	const period = getActivityPeriod(0);
	const { rerender } = render(ActivityExercise, {
		period,
		sessions: [{ ...exerciseSessions[0], avgHeartRate: 72, maxHeartRate: 84 }]
	});
	await expect.element(page.getByText('평균 72 bpm', { exact: true })).toBeVisible();
	await expect.element(page.getByText('최고 84 bpm', { exact: true })).toBeVisible();
	const point = page
		.getByRole('button', { name: `${period.days[0].date} 운동 중 심박수 72 bpm`, exact: true })
		.element();
	point.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('72 bpm');
	await rerender({ sessions: [{ ...exerciseSessions[0], avgHeartRate: 0, maxHeartRate: 0 }] });
	const zeroPoint = page
		.getByRole('button', { name: `${period.days[0].date} 운동 중 심박수 0 bpm`, exact: true })
		.element();
	expect(zeroPoint).toBe(point);
	expect(document.activeElement).toBe(point);
	zeroPoint.blur();
	zeroPoint.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('—');
	expect(page.getByRole('tooltip').element().textContent).not.toContain('0 bpm');
});

it('미인정 걷기를 펼쳐도 인정된 종류와 집계·추이는 바뀌지 않는다', async () => {
	render(ActivityExercise, { period: getActivityPeriod(0) });
	const recorded = page.getByRole('list', { name: '기록된 운동 종류', exact: true });
	expect(recorded.element().querySelectorAll('li')).toHaveLength(4);
	await expect.element(page.getByText('평균 26분', { exact: true })).toBeVisible();
	await expect.element(page.getByText('총 2시간 59분', { exact: true })).toBeVisible();
	const before = Array.from(document.querySelectorAll('.activity-trend polyline')).map((line) =>
		line.getAttribute('points')
	);
	const typesBefore = recorded.element().textContent;
	await page
		.getByRole('button', { name: '운동 시간에 포함되지 않은 걷기 4건', exact: true })
		.click();
	const excluded = page.getByRole('list', { name: '운동 시간에 포함되지 않은 걷기', exact: true });
	expect(excluded.element().querySelectorAll('li')).toHaveLength(4);
	expect(recorded.element().contains(excluded.element())).toBe(false);
	expect(recorded.element().textContent).toBe(typesBefore);
	expect(
		Array.from(document.querySelectorAll('.activity-trend polyline')).map((line) =>
			line.getAttribute('points')
		)
	).toEqual(before);
});

it('기간 밖의 세션은 집계와 disclosure에 포함하지 않는다', async () => {
	render(ActivityExercise, { period: getActivityPeriod(1) });
	await expect
		.element(page.getByText('선택한 기간에 운동 기록이 없어요.', { exact: true }))
		.toBeVisible();
	expect(document.querySelector('button[aria-expanded]')).toBeNull();
});

it('정상 0분·종류 0건과 운동 시간은 있지만 종류 정보가 없는 상태를 구분한다', async () => {
	const period = getActivityPeriod(0);
	const { rerender } = render(ActivityExercise, {
		period,
		sessions: [],
		serverTime: { avg: 0, total: 0, values: period.days.map(() => 0) }
	});
	const card = page.getByRole('region', { name: '운동 종류', exact: true });
	await expect.element(card).toHaveTextContent('선택한 기간에 운동 기록이 없어요.');
	await expect.element(card).toHaveTextContent('총 0종');
	await expect
		.element(page.getByRole('list', { name: '기록된 운동 종류', exact: true }))
		.not.toBeInTheDocument();
	await rerender({ sessions: [{ ...exerciseSessions[0], durationMinutes: 0 }] });
	await expect.element(card).toHaveTextContent('총 1종');
	await expect
		.element(page.getByRole('list', { name: '기록된 운동 종류', exact: true }))
		.toBeVisible();
	await expect.element(page.getByText('평균 0분', { exact: true })).toBeVisible();
	await expect.element(card).not.toHaveTextContent('선택한 기간에 운동 기록이 없어요.');
	await rerender({
		sessions: [],
		serverTime: { avg: 30, total: 30, values: [30, null, null, null, null, null, null] }
	});
	await expect.element(card).toHaveTextContent('운동 종류 정보가 없어요.');
	await expect.element(card).not.toHaveTextContent('총 0종');
	await expect.element(page.getByText('평균 30분', { exact: true })).toBeVisible();
	await rerender({ sessions: [{ ...exerciseSessions[0], typeName: '  ' }] });
	await expect.element(card).toHaveTextContent('운동 종류 정보가 없어요.');
	await expect
		.element(page.getByRole('list', { name: '기록된 운동 종류', exact: true }))
		.not.toBeInTheDocument();
});

it('종류가 없더라도 동기화 대기와 측정값 결측을 운동 0건으로 바꾸지 않는다', async () => {
	const period = getActivityPeriod(0);
	const days = period.days.map((day) => ({
		date: day.date,
		value: null,
		missing: true,
		reason: 'UNSYNCED' as const,
		isInProgress: false
	}));
	const { rerender } = render(ActivityExercise, {
		period,
		sessions: [],
		exerciseDays: days,
		serverTime: { avg: null, total: null, values: days.map(() => null) }
	});
	const card = page.getByRole('region', { name: '운동 종류', exact: true });
	await expect.element(card).toHaveTextContent('운동 기록이 아직 동기화되지 않았어요.');
	await expect.element(card).not.toHaveTextContent('선택한 기간에 운동 기록이 없어요.');
	await rerender({
		serverTime: { avg: 0, total: 0, values: [0, null, null, null, null, null, null] },
		exerciseDays: days.map((day, index) =>
			index === 0 ? { ...day, value: 0, missing: false, reason: null } : day
		)
	});
	await expect.element(card).toHaveTextContent('운동 기록이 아직 동기화되지 않았어요.');
	await expect.element(card).not.toHaveTextContent('총 0종');
	await rerender({ exerciseDays: days.map((day) => ({ ...day, reason: 'NULL_VALUE' as const })) });
	await expect.element(card).toHaveTextContent('값 없음');
	await expect.element(card).not.toHaveTextContent('총 0종');
});

it('걸음 기록이 없는 기간도 미인정 세션이 있으면 0분으로 표시한다', async () => {
	const period = getActivityPeriod(1);
	render(ActivityExercise, {
		period,
		sessions: [
			{
				...exerciseSessions[0],
				date: period.days[0].date,
				countsAsExercise: false,
				avgSpeedMps: null
			}
		]
	});
	await expect.element(page.getByText('평균 0분', { exact: true })).toBeVisible();
	await expect.element(page.getByText('총 0종', { exact: true })).toBeVisible();
	await page
		.getByRole('button', { name: '운동 시간에 포함되지 않은 걷기 1건', exact: true })
		.click();
	await expect
		.element(page.getByRole('list', { name: '운동 시간에 포함되지 않은 걷기', exact: true }))
		.toBeVisible();
	expect(document.querySelectorAll('.activity-trend [data-trend-point]')).toHaveLength(1);
	const point = page.getByRole('button', {
		name: `${period.days[0].date} 운동 시간 0시간 0분`,
		exact: true
	});
	point.element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0시간 0분');
});

function chartPointY(point: Element | null) {
	if (!(point instanceof HTMLElement)) throw new Error('Missing chart point');
	const figure = point.closest('figure');
	const index = Number(
		figure?.querySelector<HTMLAnchorElement>(`a[href$="/${point.dataset.date}"]`)?.dataset
			.chartNavigation
	);
	const count = figure?.querySelectorAll('[data-chart-navigation]').length ?? 0;
	const x = 36 + (index * 468) / Math.max(1, count - 1);
	const coordinates = [...(figure?.querySelectorAll('polyline') ?? [])].flatMap((line) =>
		(line.getAttribute('points') ?? '').split(' ').map((pair) => pair.split(',').map(Number))
	);
	const coordinate = coordinates.find(([pointX]) => pointX === x);
	if (!coordinate) throw new Error('Missing line coordinate');
	return coordinate[1];
}
