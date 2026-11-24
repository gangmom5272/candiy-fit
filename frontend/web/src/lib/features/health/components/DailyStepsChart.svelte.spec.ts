import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import DailyStepsChart from './DailyStepsChart.svelte';
import { getActivityPeriod } from '../activity';

it('같은 기간의 걸음 수가 클수록 진하고 날짜 순서를 바꿔도 같은 값의 진하기·높이를 유지한다', async () => {
	const values = [12000, 2000, 6000, 2000, 0, null, 3000];
	const makeDays = (steps: (number | null)[]) =>
		getActivityPeriod(0).days.map((day, index) => ({
			...day,
			steps: steps[index],
			missing: steps[index] === null
		}));
	const { container, rerender } = render(DailyStepsChart, {
		days: makeDays(values),
		average: 10000
	});
	const bars = [...container.querySelectorAll('svg rect')];
	const opacities = bars.map((bar) => Number(getComputedStyle(bar).opacity));
	expect(bars).toHaveLength(6);
	expect(opacities[0]).toBeGreaterThan(opacities[2]);
	expect(opacities[2]).toBeGreaterThan(opacities[5]);
	expect(opacities[5]).toBeGreaterThan(opacities[1]);
	expect(opacities[1]).toBe(opacities[3]);
	expect(opacities[3]).toBeGreaterThan(opacities[4]);
	expect(new Set(bars.map((bar) => getComputedStyle(bar).fill)).size).toBe(1);
	const original = new Map(
		values
			.filter((value) => value !== null)
			.map((value, index) => [
				value,
				{ opacity: opacities[index], height: bars[index].getAttribute('height') }
			])
	);
	const averageLine = container.querySelector('[stroke-dasharray]')?.outerHTML;
	const reordered = [0, 2000, 3000, null, 2000, 6000, 12000];
	await rerender({ days: makeDays(reordered), average: 10000 });
	const moved = [...container.querySelectorAll('svg rect')];
	for (const [index, value] of reordered.filter((value) => value !== null).entries()) {
		expect(Number(getComputedStyle(moved[index]).opacity)).toBe(original.get(value)?.opacity);
		expect(moved[index].getAttribute('height')).toBe(original.get(value)?.height);
	}
	expect(container.querySelector('[stroke-dasharray]')?.outerHTML).toBe(averageLine);
});

it('전체 0·동일 값·유효값 하나인 새 기간의 진하기를 재계산하고 결측을 막대로 만들지 않는다', async () => {
	const cases = [
		[0, 0, null, null, null, null, null],
		[4500, 4500, 4500, null, null, null, null],
		[null, null, null, 6000, null, null, null]
	];
	const { container, rerender } = render(DailyStepsChart, { days: [] });
	for (const values of cases) {
		const days = getActivityPeriod(0).days.map((day, index) => ({
			...day,
			steps: values[index],
			missing: values[index] === null
		}));
		await rerender({ days, average: null });
		const bars = [...container.querySelectorAll('svg rect')];
		const opacities = bars.map((bar) => Number(getComputedStyle(bar).opacity));
		expect(bars).toHaveLength(values.filter((value) => value !== null).length);
		expect(new Set(opacities).size).toBe(1);
		for (const [index, value] of values.filter((value) => value !== null).entries()) {
			expect(opacities[index]).toBeGreaterThan(0);
			expect(opacities[index]).toBeLessThanOrEqual(1);
			if (value === 0) expect(Number(bars[index].getAttribute('height'))).toBe(0);
			else expect(Number(bars[index].getAttribute('height'))).toBeGreaterThan(0);
		}
	}
});

it('서버 평균이나 날짜 포커스는 걸음 수의 내부 색상을 변경하지 않는다', async () => {
	const days = getActivityPeriod(0).days.map((day, index) => ({
		...day,
		steps: [6000, 12000, 0, null, null, null, null][index],
		missing: index > 2
	}));
	const { container, rerender } = render(DailyStepsChart, { days, average: 24000 });
	const snapshot = () =>
		[...container.querySelectorAll('svg rect')].map((bar) => ({
			fill: getComputedStyle(bar).fill,
			opacity: getComputedStyle(bar).opacity
		}));
	const before = snapshot();
	page
		.getByRole('link', { name: /^6,000보.*10\/19/ })
		.element()
		.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent(/^클릭하여 하루 상세 보기$/);
	expect(snapshot()).toEqual(before);
	await rerender({ days, average: null });
	expect(snapshot()).toEqual(before);
	expect(container.querySelector('[stroke-dasharray]')).toBeNull();
	expect(days.map((day) => day.steps)).toEqual([6000, 12000, 0, null, null, null, null]);
});

it.each([
	{ steps: 0, ticks: ['12,000', '8,000', '4,000', '0'], maximum: 12000 },
	{ steps: 1, ticks: ['12,000', '8,000', '4,000', '0'], maximum: 12000 },
	{ steps: 8200, ticks: ['12,000', '8,000', '4,000', '0'], maximum: 12000 },
	{ steps: 12000, ticks: ['12,000', '8,000', '4,000', '0'], maximum: 12000 },
	{ steps: 12001, ticks: ['15,000', '10,000', '5,000', '0'], maximum: 15000 },
	{ steps: 12344, ticks: ['15,000', '10,000', '5,000', '0'], maximum: 15000 },
	{ steps: 13000, ticks: ['15,000', '10,000', '5,000', '0'], maximum: 15000 },
	{ steps: 13457, ticks: ['15,000', '10,000', '5,000', '0'], maximum: 15000 },
	{ steps: 15000, ticks: ['15,000', '10,000', '5,000', '0'], maximum: 15000 },
	{ steps: 15001, ticks: ['18,000', '12,000', '6,000', '0'], maximum: 18000 },
	{ steps: 18234, ticks: ['24,000', '16,000', '8,000', '0'], maximum: 24000 },
	{ steps: 23150, ticks: ['24,000', '16,000', '8,000', '0'], maximum: 24000 },
	{ steps: 100000, ticks: ['120,000', '80,000', '40,000', '0'], maximum: 120000 },
	{
		steps: 1000000000,
		ticks: ['1,200,000,000', '800,000,000', '400,000,000', '0'],
		maximum: 1200000000
	}
])(
	'$steps보는 같은 간격의 정수 눈금과 원본 값의 높이로 표시한다',
	async ({ steps, ticks, maximum }) => {
		const days = getActivityPeriod(0).days.map((day, index) => ({
			...day,
			steps: index === 0 ? steps : null
		}));
		render(DailyStepsChart, { days });
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
		const displayedTicks = Array.from(
			chart.querySelectorAll(':scope > div:first-child > span'),
			(tick) => tick.textContent ?? ''
		);
		expect(displayedTicks).toEqual(ticks);
		const numericTicks = displayedTicks.map((tick) => Number(tick.replaceAll(',', '')));
		expect(numericTicks[0]).toBeGreaterThanOrEqual(steps);
		expect(numericTicks[0] - numericTicks[1]).toBe(numericTicks[1] - numericTicks[2]);
		expect(numericTicks[1] - numericTicks[2]).toBe(numericTicks[2] - numericTicks[3]);
		const bar = chart.querySelector('rect');
		expect(Number(bar?.getAttribute('height'))).toBeCloseTo((steps / maximum) * 156);
		expect(Number(chart.querySelector('[stroke-dasharray]')?.getAttribute('y1'))).toBeCloseTo(
			156 - (steps / maximum) * 156
		);
		expect(chart.querySelector('[data-step-value]')?.textContent).toBe(
			steps.toLocaleString('ko-KR')
		);
		const link = page.getByRole('link', {
			name: new RegExp(`^${steps.toLocaleString('ko-KR')}보.*10/19`)
		});
		await expect.element(link).toHaveAttribute('href', '/health/day/2026-10-19');
		link.element().focus();
		await expect.element(page.getByRole('tooltip')).toHaveTextContent(/^클릭하여 하루 상세 보기$/);
	}
);

it('일별 값보다 큰 서버 평균도 축에 포함하고 기간이 바뀌면 축을 다시 계산한다', async () => {
	const days = getActivityPeriod(0).days.map((day, index) => ({
		...day,
		steps: index === 0 ? 99999 : 6000,
		missing: index === 0
	}));
	const { rerender } = render(DailyStepsChart, { days, average: 13457.5 });
	const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
	expect(
		Array.from(
			chart.querySelectorAll(':scope > div:first-child > span'),
			(tick) => tick.textContent
		)
	).toEqual(['15,000', '10,000', '5,000', '0']);
	expect(chart.querySelectorAll('rect')).toHaveLength(6);
	expect(Number(chart.querySelector('rect')?.getAttribute('height'))).toBeCloseTo(62.4);
	expect(Number(chart.querySelector('[stroke-dasharray]')?.getAttribute('y1'))).toBeCloseTo(
		156 - (13457.5 / 15000) * 156
	);

	await rerender({
		days: days.map((day) => ({ ...day, steps: day.missing ? 99999 : 4000 })),
		average: null
	});
	expect(
		Array.from(
			chart.querySelectorAll(':scope > div:first-child > span'),
			(tick) => tick.textContent
		)
	).toEqual(['12,000', '8,000', '4,000', '0']);
	expect(Number(chart.querySelector('rect')?.getAttribute('height'))).toBeCloseTo(
		(4000 / 12000) * 156
	);
	expect(chart.querySelector('[stroke-dasharray]')).toBeNull();
});

it('합산이 표현 범위를 넘는 큰 값도 눈금·막대·평균선을 유한한 좌표로 표시한다', () => {
	const days = getActivityPeriod(0).days.map((day, index) => ({
		...day,
		steps: index < 2 ? Number.MAX_VALUE : null
	}));
	render(DailyStepsChart, { days });
	const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
	expect(chart.querySelectorAll('rect')).toHaveLength(2);
	for (const bar of chart.querySelectorAll('rect')) {
		expect(Number(bar.getAttribute('height'))).toBe(156);
		expect(Number(bar.getAttribute('y'))).toBe(0);
	}
	expect(Number(chart.querySelector('[stroke-dasharray]')?.getAttribute('y1'))).toBe(0);
	const ticks = Array.from(chart.querySelectorAll(':scope > div:first-child > span'), (tick) =>
		Number(tick.textContent)
	);
	expect(ticks).toHaveLength(4);
	expect(ticks.every(Number.isFinite)).toBe(true);
	expect(ticks[0]).toBe(Number.MAX_VALUE);
	expect(ticks[0]).toBeGreaterThan(ticks[1]);
	expect(ticks[1]).toBeGreaterThan(ticks[2]);
	expect(ticks[3]).toBe(0);
});

it('음수 원본 값은 보존하고 표시 범위 밖 막대·평균선이 차트 밖으로 나가지 않는다', async () => {
	const values = [-1000, 1, null, null, null, null, null];
	const days = Object.freeze(
		getActivityPeriod(0).days.map((day, index) => Object.freeze({ ...day, steps: values[index] }))
	);
	const { rerender } = render(DailyStepsChart, { days, average: -499.5 });
	const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
	expect(
		Array.from(
			chart.querySelectorAll(':scope > div:first-child > span'),
			(tick) => tick.textContent
		)
	).toEqual(['12,000', '8,000', '4,000', '0']);
	expect(chart.querySelectorAll('rect')).toHaveLength(1);
	expect(Number(chart.querySelector('rect')?.getAttribute('height'))).toBeCloseTo(0.013);
	expect(Number(chart.querySelector('rect')?.getAttribute('y'))).toBeCloseTo(155.987);
	expect(chart.querySelector('[stroke-dasharray]')).toBeNull();
	expect(
		Array.from(chart.querySelectorAll('[data-step-value]'), (value) => value.textContent)
	).toEqual(['-1,000', '1']);
	const chartBounds = chart.getBoundingClientRect();
	for (const value of chart.querySelectorAll('[data-step-value]')) {
		const bounds = value.getBoundingClientRect();
		expect(Number.isFinite(bounds.top)).toBe(true);
		expect(bounds.top).toBeGreaterThanOrEqual(chartBounds.top);
		expect(bounds.bottom).toBeLessThanOrEqual(chartBounds.bottom);
	}
	const negativeDate = page.getByRole('link', { name: /^-1,000보.*10\/19/ });
	await expect.element(negativeDate).toHaveAttribute('href', '/health/day/2026-10-19');
	negativeDate.element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent(/^클릭하여 하루 상세 보기$/);
	expect(days.map((day) => day.steps)).toEqual(values);

	await rerender({ days, average: 0.5 });
	expect(Number(chart.querySelector('[stroke-dasharray]')?.getAttribute('y1'))).toBeCloseTo(
		155.9935
	);
	await rerender({ days, average: 0 });
	expect(Number(chart.querySelector('[stroke-dasharray]')?.getAttribute('y1'))).toBe(156);
});

it.each([NaN, Infinity, -Infinity])(
	'비정상 값 %s는 원본을 보존하면서 표시 축·막대·평균선 계산에서 제외한다',
	async (invalid) => {
		const days = Object.freeze(
			getActivityPeriod(0).days.map((day, index) =>
				Object.freeze({ ...day, steps: index === 0 ? invalid : index === 1 ? 1 : null })
			)
		);
		const { rerender } = render(DailyStepsChart, { days, average: invalid });
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
		expect(
			Array.from(
				chart.querySelectorAll(':scope > div:first-child > span'),
				(tick) => tick.textContent
			)
		).toEqual(['12,000', '8,000', '4,000', '0']);
		expect(chart.querySelectorAll('rect')).toHaveLength(1);
		expect(Number(chart.querySelector('rect')?.getAttribute('height'))).toBeCloseTo(0.013);
		expect(Number(chart.querySelector('rect')?.getAttribute('y'))).toBeCloseTo(155.987);
		expect(chart.querySelector('[stroke-dasharray]')).toBeNull();
		expect(chart.querySelector('[data-step-value]')?.textContent).toBe(
			invalid.toLocaleString('ko-KR')
		);
		expect(days[0].steps).toBe(invalid);
		expect(days[1].steps).toBe(1);

		await rerender({ days: [days[0]], average: invalid });
		expect(
			Array.from(
				chart.querySelectorAll(':scope > div:first-child > span'),
				(tick) => tick.textContent
			)
		).toEqual(['12,000', '8,000', '4,000', '0']);
		expect(chart.querySelectorAll('rect')).toHaveLength(0);
		expect(chart.querySelector('[stroke-dasharray]')).toBeNull();
		expect(chart.querySelector('[data-step-value]')?.textContent).toBe(
			invalid.toLocaleString('ko-KR')
		);
	}
);

it('극단적인 소수 경계에서도 상한이 원본 최댓값을 포함하고 좌표가 축을 넘지 않는다', () => {
	const value = 3.0000000000000004e23;
	const days = getActivityPeriod(0).days.map((day, index) => ({
		...day,
		steps: index === 0 ? value : null
	}));
	render(DailyStepsChart, { days, average: value });
	const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
	const ticks = Array.from(chart.querySelectorAll(':scope > div:first-child > span'), (tick) =>
		Number(tick.textContent)
	);
	expect(ticks).toHaveLength(4);
	expect(ticks.every(Number.isFinite)).toBe(true);
	expect(ticks[0]).toBeGreaterThanOrEqual(value);
	expect(ticks[0]).toBeGreaterThan(ticks[1]);
	expect(ticks[1]).toBeGreaterThan(ticks[2]);
	expect(ticks[2]).toBeGreaterThan(ticks[3]);
	expect(ticks[3]).toBe(0);
	for (const bar of chart.querySelectorAll('rect')) {
		expect(Number(bar.getAttribute('height'))).toBeLessThanOrEqual(156);
		expect(Number(bar.getAttribute('height'))).toBeGreaterThanOrEqual(0);
		expect(Number(bar.getAttribute('y'))).toBeGreaterThanOrEqual(0);
		expect(Number(bar.getAttribute('y'))).toBeLessThanOrEqual(156);
	}
	expect(chart.querySelectorAll('rect')).toHaveLength(1);
	const averageY = Number(chart.querySelector('[stroke-dasharray]')?.getAttribute('y1'));
	expect(averageY).toBeGreaterThanOrEqual(0);
	expect(averageY).toBeLessThanOrEqual(156);
	expect(chart.querySelector('[data-step-value]')?.textContent).toBe(value.toLocaleString('ko-KR'));
	expect(days[0].steps).toBe(value);
});

it('전달된 실제 0보와 기록 없는 날을 구분하고 해당 날짜로 연결한다', async () => {
	const days = getActivityPeriod(0)
		.days.slice(0, 3)
		.map((day, index) => ({ ...day, steps: [0, null, 6000][index] }));
	render(DailyStepsChart, { days });
	await expect
		.element(page.getByRole('link', { name: /^0보.*10\/19/ }))
		.toHaveAttribute('href', '/health/day/2026-10-19');
	await expect
		.element(page.getByRole('link', { name: /^기록 없음.*10\/20/ }))
		.toHaveAttribute('href', '/health/day/2026-10-20');
	await expect.element(page.getByRole('link', { name: /^6,000보.*10\/21/ })).toBeVisible();
	expect(document.querySelectorAll('.daily-steps-chart rect')).toHaveLength(2);
	page
		.getByRole('link', { name: /^0보.*10\/19/ })
		.element()
		.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent(/^클릭하여 하루 상세 보기$/);
	page
		.getByRole('link', { name: /^기록 없음.*10\/20/ })
		.element()
		.focus();
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
});

it.each([{ days: [] }, { days: getActivityPeriod(1).days }])(
	'빈 기간 또는 모두 null이면 안전한 기본 축과 기록 없음을 표시한다 (%j)',
	async ({ days }) => {
		render(DailyStepsChart, { days });
		await expect.element(page.getByText('선택한 기간의 걸음 수 기록이 없어요.')).toBeVisible();
		expect(document.querySelectorAll('.daily-steps-chart rect')).toHaveLength(0);
		expect(document.querySelectorAll('.daily-steps-chart [stroke-dasharray]')).toHaveLength(0);
		const chart = page.getByRole('figure', { name: '일별 걸음 수 차트', exact: true }).element();
		expect(
			Array.from(
				chart.querySelectorAll(':scope > div:first-child > span'),
				(tick) => tick.textContent
			)
		).toEqual(['12,000', '8,000', '4,000', '0']);
	}
);
