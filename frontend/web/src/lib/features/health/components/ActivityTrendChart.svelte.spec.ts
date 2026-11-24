import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import '../../../../routes/layout.css';
import ActivityTrendChart from './ActivityTrendChart.svelte';
import { getActivityPeriod } from '../activity';

it('운동 추이 날짜에만 이동 안내를 표시하고 측정점은 값 툴팁을 유지한다', async () => {
	const days = getActivityPeriod(0).days.slice(0, 2);
	render(ActivityTrendChart, {
		days,
		values: [0, null],
		caption: '운동 시간',
		label: '운동 시간',
		unit: '분'
	});
	const date = page.getByRole('link').first();
	date.element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('클릭하여 하루 상세 보기');
	expect(date.element().getAttribute('href')).toBe(`/health/day/${days[0].date}`);
	page.getByRole('button').element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0 분');
	expect(document.querySelectorAll('[role="tooltip"]')).toHaveLength(1);
	page.getByRole('link').last().element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('클릭하여 하루 상세 보기');
});

it('활동 화면의 결측 연결은 원본 null을 유지하며 7일 전체를 기준선으로 연결한다', async () => {
	const values = Object.freeze([0, null, 72, null, 0, 144, null]);
	const days = getActivityPeriod(0).days;
	render(ActivityTrendChart, {
		days,
		values,
		connectMissing: true,
		area: true,
		caption: '소모 칼로리 추이',
		label: '소모 칼로리',
		unit: 'kcal',
		range: [0, 144]
	});
	const chart = page.getByRole('figure').element();
	expect(chart.querySelectorAll('polyline')).toHaveLength(1);
	expect(chart.querySelector('polyline')?.getAttribute('points')).toBe(
		'36,58 114,58 192,38 270,58 348,58 426,18 504,58'
	);
	expect(chart.querySelectorAll('polygon')).toHaveLength(1);
	expect(chart.querySelectorAll('[data-trend-point]')).toHaveLength(4);
	expect(chart.querySelectorAll('[data-chart-point]')).toHaveLength(7);
	for (const [index, expected] of [
		'0 kcal',
		'—',
		'72 kcal',
		'—',
		'0 kcal',
		'144 kcal',
		'—'
	].entries()) {
		chart.querySelector<HTMLButtonElement>(`[data-chart-point="${index}"]`)?.focus();
		await expect
			.element(page.getByRole('tooltip'))
			.toHaveTextContent(days[index].date.replaceAll('-', '.'));
		expect(page.getByRole('tooltip').element().querySelector('strong')?.textContent).toBe(expected);
	}
	expect(values).toEqual([0, null, 72, null, 0, 144, null]);
});

it('전부 결측이면 안내만 표시하고 실제 0이 들어오면 7일 연결을 복원한다', async () => {
	const { rerender } = render(ActivityTrendChart, {
		days: getActivityPeriod(0).days,
		values: Array(7).fill(null),
		connectMissing: true,
		caption: '운동 중 심박수 추이'
	});
	expect(document.querySelectorAll('.activity-trend [data-trend-point]')).toHaveLength(0);
	expect(
		document.querySelectorAll('.activity-trend polyline, .activity-trend polygon')
	).toHaveLength(0);
	expect(document.querySelectorAll('.activity-trend [data-chart-point]')).toHaveLength(0);
	await expect.element(page.getByText('선택한 기간의 기록이 없어요.')).toBeVisible();
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	await rerender({ values: [0, null, null, null, null, null, null] });
	await expect.element(page.getByText('선택한 기간의 기록이 없어요.')).not.toBeInTheDocument();
	expect(document.querySelectorAll('.activity-trend [data-trend-point]')).toHaveLength(1);
	expect(document.querySelectorAll('.activity-trend polyline')).toHaveLength(1);
	expect(document.querySelectorAll('.activity-trend [data-chart-point]')).toHaveLength(7);
	document.querySelector<HTMLButtonElement>('[data-chart-point="0"]')?.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0');
	await rerender({ values: Array(7).fill(null) });
	await expect.element(page.getByText('선택한 기간의 기록이 없어요.')).toBeVisible();
	expect(
		document.querySelectorAll('.activity-trend polyline, .activity-trend [data-chart-point]')
	).toHaveLength(0);
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
});

it('0값 점을 유지하며 날짜 선택 영역은 24px 이상이고 점과 겹치지 않는다', async () => {
	const days = getActivityPeriod(0).days.slice(0, 3);
	render(ActivityTrendChart, {
		days,
		values: [0, null, 60],
		caption: '운동 시간 추이',
		label: '운동 시간',
		unit: '분',
		range: [0, 60]
	});
	await expect
		.element(page.getByRole('button', { name: '2026-10-19 운동 시간 0 분' }))
		.toBeVisible();
	const points = [...document.querySelectorAll('.activity-trend button')].map((node) =>
		node.getBoundingClientRect()
	);
	expect(points).toHaveLength(2);
	const dates = [...document.querySelectorAll<HTMLAnchorElement>('.activity-trend a')];
	expect(dates).toHaveLength(3);
	for (const [index, date] of dates.entries()) {
		expect(date.getAttribute('href')).toBe(`/health/day/${days[index].date}`);
		const r = date.getBoundingClientRect();
		expect(r.width).toBeGreaterThanOrEqual(24);
		expect(r.height).toBeGreaterThanOrEqual(24);
		for (const point of points)
			expect(
				r.bottom <= point.top ||
					r.top >= point.bottom ||
					r.right <= point.left ||
					r.left >= point.right
			).toBe(true);
	}
});

it('누락된 측정값을 0으로 연결하지 않고 선과 면적을 분리한다', async () => {
	render(ActivityTrendChart, {
		days: getActivityPeriod(0).days.slice(0, 5),
		values: [0.2, 0.3, null, 0.7, 0.8],
		caption: '소모 칼로리 추이',
		label: '소모 칼로리',
		unit: 'kcal',
		area: true
	});
	await expect
		.element(page.getByText('소모 칼로리 추이 날짜를 선택하면 하루 상세 기록으로 이동합니다.'))
		.toBeInTheDocument();
	expect(document.querySelectorAll('.activity-trend polyline')).toHaveLength(2);
	expect(document.querySelectorAll('.activity-trend polygon')).toHaveLength(2);
	expect(document.querySelectorAll('.activity-trend [data-trend-point]')).toHaveLength(4);
	expect(document.querySelectorAll('.activity-trend button')).toHaveLength(4);
	const firstPoint = page
		.getByRole('button', { name: '2026-10-19 소모 칼로리 0.2 kcal', exact: true })
		.element();
	const bounds = firstPoint.getBoundingClientRect();
	// 툴팁 DOM이 생성되기 전 같은 이벤트 루프에서 포인터가 움직여도 안전하다.
	firstPoint.dispatchEvent(
		new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' })
	);
	window.dispatchEvent(
		new PointerEvent('pointermove', {
			pointerType: 'mouse',
			clientX: bounds.x + bounds.width / 2,
			clientY: bounds.y + bounds.height / 2
		})
	);
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0.2 kcal');
	page
		.getByRole('button', { name: '2026-10-19 소모 칼로리 0.2 kcal', exact: true })
		.element()
		.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0.2 kcal');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.19');
});

it('빈 기간에는 추이선이나 측정점을 만들지 않는다', async () => {
	render(ActivityTrendChart, {
		days: getActivityPeriod(1).days,
		values: Array.from({ length: 7 }, () => null),
		caption: '운동 시간 추이'
	});
	await expect.element(page.getByText('선택한 기간의 기록이 없어요.')).toBeVisible();
	expect(
		document.querySelectorAll('.activity-trend polyline, .activity-trend [data-trend-point]')
	).toHaveLength(0);
	expect(document.querySelectorAll('.activity-trend button')).toHaveLength(0);
});

it('범위를 벗어난 실제 수치와 한 개의 값도 표시 영역 안에 보존한다', async () => {
	render(ActivityTrendChart, {
		days: getActivityPeriod(0).days.slice(0, 2),
		values: [250, null],
		range: [0, 100],
		caption: '운동 시간 추이'
	});
	await expect.element(page.getByRole('figure')).toBeVisible();
	const point = document.querySelector('[data-trend-point]');
	expect(chartPointY(point)).toBeGreaterThanOrEqual(18);
	expect(chartPointY(point)).toBeLessThanOrEqual(58);
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
