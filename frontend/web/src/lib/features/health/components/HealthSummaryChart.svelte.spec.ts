import { page, userEvent } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import { tick } from 'svelte';
import '../../../../routes/layout.css';
import HealthSummaryChart from './HealthSummaryChart.svelte';
import { healthSummaries, summaryDates, type HealthSummary } from '../summary';
import { adjacentDay } from '../day-detail';
import { formatSleepMinutes } from '../sleep';

function normalizedTop(y: number) {
	const style = document.createElement('span').style;
	style.top = `${(y / 132) * 100}%`;
	return style.top;
}

it.each([healthSummaries[0], healthSummaries[2]])(
	'$label 막대의 진하기는 날짜 순서와 서버 평균이 아닌 실제 값에 따라 유지된다',
	async (base) => {
		const values = [600, 200, 400, 200, null, 0, 100];
		const { container, rerender } = render(HealthSummaryChart, {
			summary: { ...base, values, average: 900 }
		});
		await expect.element(page.getByRole('figure')).toBeVisible();
		const bars = [...container.querySelectorAll('svg rect')];
		const opacities = bars.map((bar) => Number(getComputedStyle(bar).opacity));
		expect(bars).toHaveLength(6);
		expect(opacities[0]).toBeGreaterThan(opacities[2]);
		expect(opacities[2]).toBeGreaterThan(opacities[1]);
		expect(opacities[1]).toBe(opacities[3]);
		expect(opacities[3]).toBeGreaterThan(opacities[5]);
		expect(opacities[5]).toBeGreaterThan(opacities[4]);
		expect(new Set(bars.map((bar) => getComputedStyle(bar).fill)).size).toBe(1);
		const recorded = values.filter((value) => value !== null);
		const original = new Map(
			recorded.map((value, index) => [
				value,
				{ opacity: opacities[index], height: bars[index].getAttribute('height') }
			])
		);
		const reordered = [0, 200, 600, 100, null, 200, 400];
		await rerender({ summary: { ...base, values: reordered, average: 900 } });
		const moved = [...container.querySelectorAll('svg rect')];
		for (const [index, value] of reordered.filter((value) => value !== null).entries()) {
			expect(Number(getComputedStyle(moved[index]).opacity)).toBe(original.get(value)?.opacity);
			// 날짜 이동은 같은 값의 막대 높이에도 영향을 주지 않는다.
			expect(moved[index].getAttribute('height')).toBe(original.get(value)?.height);
		}
		await rerender({ summary: { ...base, values: reordered, average: null } });
		for (const [index, value] of reordered.filter((value) => value !== null).entries()) {
			expect(Number(getComputedStyle(moved[index]).opacity)).toBe(original.get(value)?.opacity);
		}
		expect(container.querySelector('[stroke-dasharray]')).toBeNull();
	}
);

it('동일 값·전체 0·유효값 하나인 기간에서도 결측과 구분한 유한한 진하기를 표시한다', async () => {
	const cases = [
		[240, 240, 240],
		[0, 0, null],
		[null, 360, null]
	];
	const { container, rerender } = render(HealthSummaryChart, {
		summary: { ...healthSummaries[2], values: cases[0], average: null }
	});
	for (const values of cases) {
		await rerender({ summary: { ...healthSummaries[2], values, average: null } });
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

it('작은 수면 값을 선택해도 내부 색상·좌표·평균선은 보존하고 테두리와 원본 툴팁만 표시한다', async () => {
	const values = [480, 240, 360, 240, null, 0, 120];
	const days = values.map((value, index) => ({
		date: adjacentDay('2026-11-10', index),
		value,
		missing: value === null,
		reason: value === null ? ('NO_RECORD' as const) : null,
		isInProgress: false
	}));
	const { container } = render(HealthSummaryChart, {
		interactive: true,
		summary: { ...healthSummaries[2], values, days, average: 300 }
	});
	const bars = [...container.querySelectorAll('svg rect')];
	const snapshot = () =>
		bars.map((bar) => ({
			fill: getComputedStyle(bar).fill,
			opacity: getComputedStyle(bar).opacity,
			y: bar.getAttribute('y'),
			height: bar.getAttribute('height')
		}));
	const before = snapshot();
	const average = container.querySelector('[stroke-dasharray]')?.outerHTML;
	page.getByRole('button', { name: '2026-11-11 수면 4시간 0분', exact: true }).element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.11.11');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('4시간 0분');
	const selection = container.querySelector<HTMLElement>('[data-bar-selection]');
	expect(selection).not.toBeNull();
	if (!selection) throw new Error('선택 테두리 없음');
	expect(Number.parseFloat(getComputedStyle(selection).borderTopWidth)).toBeGreaterThan(0);
	expect(getComputedStyle(selection).backgroundColor).toBe('rgba(0, 0, 0, 0)');
	expect(snapshot()).toEqual(before);
	expect(container.querySelector('[stroke-dasharray]')?.outerHTML).toBe(average);
	await userEvent.keyboard('{ArrowRight}');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.11.12');
	expect(snapshot()).toEqual(before);
	await userEvent.keyboard('{Escape}');
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	expect(container.querySelectorAll('[data-bar-selection]')).toHaveLength(0);
});

it.each([
	{ index: 0, value: 10001, ticks: [12000, 6000, 0], labels: ['12,000', '6,000', '0'] },
	{ index: 0, value: 15001, ticks: [16000, 8000, 0], labels: ['16,000', '8,000', '0'] },
	{ index: 1, value: 120.1, ticks: [150, 75, 0], labels: ['2h 30m', '1h 15m', '0'] },
	{ index: 1, value: 600.5, ticks: [660, 330, 0], labels: ['11h', '5h 30m', '0'] },
	{ index: 2, value: 480.1, ticks: [540, 270, 0], labels: ['9h', '4h 30m', '0'] },
	{ index: 3, value: 90.1, ticks: [100, 80, 60], labels: ['100', '80', '60'] },
	{ index: 3, value: 50, ticks: [90, 70, 50], labels: ['90', '70', '50'] }
])(
	'경계값 $value의 눈금·라벨·데이터 좌표가 일치한다 ($index)',
	async ({ index, value, ticks, labels }) => {
		const summary = {
			...healthSummaries[index],
			values: [value],
			dates: ['11.05'],
			average: value
		};
		const { container } = render(HealthSummaryChart, { summary });
		await expect.element(page.getByRole('figure')).toBeVisible();
		await document.fonts.ready;
		const axisLabels = [
			...container.querySelectorAll<HTMLSpanElement>(
				'figure > div[aria-hidden="true"] > span:not([data-summary-date])'
			)
		];
		const gridLines = [
			...container.querySelectorAll<SVGLineElement>('svg line[stroke="var(--color-border)"]')
		];
		expect(axisLabels.map((label) => label.textContent)).toEqual(labels);
		expect(gridLines).toHaveLength(3);
		for (const [i, line] of gridLines.entries()) {
			const expectedY = 96 - ((ticks[i] - ticks[2]) / (ticks[0] - ticks[2])) * 72;
			expect(Number(line.getAttribute('y1'))).toBe(expectedY);
			// 축의 선언 좌표를 검증하고 실제 렌더링 정렬은 home-ux E2E에서 별도로 검증한다.
			expect(Number.parseFloat(axisLabels[i].style.top)).toBe(expectedY);
		}
		const point = container.querySelector(
			summary.kind === 'bar' ? 'svg rect' : '[data-summary-point]'
		);
		const expectedY = 96 - ((value - ticks[2]) / (ticks[0] - ticks[2])) * 72;
		if (summary.kind === 'bar') expect(Number(point?.getAttribute('y'))).toBeCloseTo(expectedY, 6);
		else expect((point as HTMLElement).style.top).toBe(normalizedTop(expectedY));
		expect(summary.values).toEqual([value]);
		expect(summary.average).toBe(value);
	}
);

it('일별 최댓값보다 큰 서버 평균도 같은 표시 축 안의 원래 좌표에 그린다', async () => {
	const { container } = render(HealthSummaryChart, {
		summary: { ...healthSummaries[0], values: [10001, null], average: 15001 }
	});
	await expect.element(page.getByRole('figure')).toBeVisible();
	const line = container.querySelector('line[stroke-dasharray]');
	expect(Number(line?.getAttribute('y1'))).toBeCloseTo(96 - (15001 / 16000) * 72, 6);
	expect(Number(container.querySelector('rect')?.getAttribute('y'))).toBeCloseTo(
		96 - (10001 / 16000) * 72,
		6
	);
	expect(container.querySelectorAll('rect')).toHaveLength(1);
});

it.each(healthSummaries)(
	'$label: 극단값도 유한한 SVG 속성으로 그리고 원본은 보존한다',
	async (base) => {
		const values = [-Number.MAX_VALUE, 0, Number.MAX_VALUE, null];
		const { container } = render(HealthSummaryChart, {
			summary: { ...base, values, average: Number.MAX_VALUE }
		});
		await expect.element(page.getByRole('figure')).toBeVisible();
		for (const node of container.querySelectorAll('svg line, svg circle, svg rect, svg polyline')) {
			for (const attribute of ['y', 'cy', 'y1', 'y2', 'height', 'points']) {
				const value = node.getAttribute(attribute);
				if (value === null) continue;
				expect(value).not.toMatch(/Infinity|NaN/);
				for (const coordinate of value.split(/[\s,]+/)) {
					expect(Number.isFinite(Number(coordinate))).toBe(true);
				}
			}
		}
		const lines = [
			...container.querySelectorAll('svg line[x1="0"][x2="580"][stroke="var(--color-border)"]')
		];
		expect(lines.map((line) => Number(line.getAttribute('y1')))).toEqual([24, 60, 96]);
		expect(values).toEqual([-Number.MAX_VALUE, 0, Number.MAX_VALUE, null]);
	}
);

it('요일 경계를 넘는 최근 7일은 실제 날짜 순서로 표시한다', async () => {
	const summary = healthSummaries[1];
	const days = Array.from({ length: 7 }, (_, index) => ({
		date: adjacentDay('2026-10-27', index),
		value: index === 2 ? null : index * 10,
		missing: index === 2,
		reason: index === 2 ? ('UNSYNCED' as const) : null,
		isInProgress: index === 6
	}));
	render(HealthSummaryChart, {
		summary: {
			...summary,
			days,
			values: days.map((day) => day.value),
			dates: days.map((day) => day.date.slice(5).replace('-', '.'))
		},
		interactive: true
	});
	await expect.element(page.getByRole('figure')).toBeVisible();
	const labels = [...document.querySelectorAll('[data-summary-date]')];
	expect(labels.map((label) => label.getAttribute('data-summary-date'))).toEqual([
		'10.27',
		'10.28',
		'10.29',
		'10.30',
		'10.31',
		'11.01',
		'11.02'
	]);
	for (const [index, label] of labels.entries()) {
		expect(Number.parseFloat((label as HTMLElement).style.left)).toBeCloseTo(
			((16 + (index * 548) / 6) / 580) * 100
		);
	}
	expect(document.querySelectorAll('[data-chart-point]')).toHaveLength(6);
});

it.each(healthSummaries)(
	'$label 차트에서 기록 없는 날짜와 실제 값을 구분해 읽을 수 있다',
	async (summary) => {
		render(HealthSummaryChart, { summary });
		const chart = page.getByRole('figure', { name: `${summary.label} 최근 7일 기록` });
		await expect.element(chart).toBeVisible();
		for (const [index, value] of summary.values.entries()) {
			await expect
				.element(chart)
				.toHaveTextContent(
					`${summaryDates[index]}: ${value === null ? '기록 없음' : summary.id === 'sleep' ? formatSleepMinutes(value) : `${value}${summary.unit}`}`
				);
		}
		await expect.element(chart).toHaveTextContent(summary.value);
	}
);

it('기본 범위를 넘는 값도 잘리지 않고 결측 사이의 선은 연결하지 않는다', async () => {
	render(HealthSummaryChart, {
		summary: { ...healthSummaries[3], values: [50, null, 210], average: 130 }
	});
	await expect.element(page.getByRole('figure')).toBeVisible();
	expect(document.querySelectorAll('polyline')).toHaveLength(2);
	for (const point of document.querySelectorAll('[data-summary-point]:not([data-missing])')) {
		expect(Number.parseFloat((point as HTMLElement).style.top)).toBeGreaterThanOrEqual(
			Number.parseFloat(normalizedTop(24))
		);
		expect(Number.parseFloat((point as HTMLElement).style.top)).toBeLessThanOrEqual(
			Number.parseFloat(normalizedTop(96))
		);
	}
});

it.each([1, 2, 7])('%i개 데이터에서는 해당 개수의 날짜만 표시한다', async (count) => {
	render(HealthSummaryChart, {
		summary: { ...healthSummaries[3], values: Array.from({ length: count }, () => 75) }
	});
	await expect.element(page.getByRole('figure')).toBeVisible();
	expect(document.querySelectorAll('[data-summary-date]')).toHaveLength(count);
});

it('모두 결측이고 기간 평균도 null이면 평균선과 수치 막대를 만들지 않는다', async () => {
	render(HealthSummaryChart, {
		summary: { ...healthSummaries[0], values: [null, null], average: null }
	});
	await expect.element(page.getByRole('figure')).toBeVisible();
	expect(document.querySelectorAll('rect, [stroke-dasharray]')).toHaveLength(0);
});

it('빈 데이터에서도 가짜 값이나 선택 지점을 만들지 않는다', async () => {
	const { container } = render(HealthSummaryChart, {
		interactive: true,
		summary: {
			...healthSummaries[0],
			values: [],
			dates: [],
			days: [],
			average: null,
			value: '기록 없음'
		}
	});
	await expect.element(page.getByRole('figure')).toHaveTextContent('기록 없음');
	expect(
		container.querySelectorAll('button, rect, [data-summary-point], polyline, [role="tooltip"]')
	).toHaveLength(0);
});

it.each([1, 2, 7])('API 데이터 %i개도 월~일 slot에 날짜와 점을 배치한다', async (count) => {
	const week = Array.from({ length: 7 }, (_, index) => ({
		date: adjacentDay('2026-10-26', index),
		value: 75,
		missing: false,
		reason: null,
		isInProgress: false
	}));
	const complete = render(HealthSummaryChart, {
		summary: { ...healthSummaries[3], days: week, values: week.map((day) => day.value) }
	});
	await expect.element(complete.container.querySelector('figure')!).toBeVisible();
	const weeklyPoints = [...complete.container.querySelectorAll('[data-summary-point]')].map(
		(point) => (Number.parseFloat((point as HTMLElement).style.left) / 100) * 580
	);
	expect(weeklyPoints[0]).toBeLessThan(weeklyPoints[6]);
	const days = week.slice(0, count);
	const partial = render(HealthSummaryChart, {
		summary: {
			...healthSummaries[3],
			days,
			dates: days.map((day) => day.date),
			values: days.map((day) => day.value)
		}
	});
	await expect.element(partial.container.querySelector('figure')!).toBeVisible();
	const points = [...partial.container.querySelectorAll('[data-summary-point]')];
	const labels = [...partial.container.querySelectorAll<HTMLElement>('[data-summary-date]')];
	const svg = partial.container.querySelector('svg');
	if (!svg) throw new Error('주간 차트 없음');
	expect(points).toHaveLength(count);
	expect(labels).toHaveLength(count);
	for (const [index, point] of points.entries()) {
		const expected = weeklyPoints[index];
		expect((Number.parseFloat((point as HTMLElement).style.left) / 100) * 580).toBeCloseTo(
			expected
		);
		expect(Number.parseFloat(labels[index].style.left)).toBeCloseTo(
			(expected / svg.viewBox.baseVal.width) * 100
		);
	}
});

it.each(healthSummaries)('$label 툴팁은 일별 원본 값·0·결측을 구분한다', async (summary) => {
	const days = [
		{ date: '2026-10-19', value: 74.25, missing: false, reason: null, isInProgress: false },
		{ date: '2026-10-20', value: 0, missing: false, reason: null, isInProgress: true },
		{
			date: '2026-10-21',
			value: null,
			missing: true,
			reason: 'UNSYNCED' as const,
			isInProgress: false
		}
	];
	render(HealthSummaryChart, {
		interactive: true,
		summary: {
			...summary,
			days,
			values: days.map((day) => day.value),
			dates: ['10.19', '10.20', '10.21'],
			average: 999
		}
	});
	const points = page.getByRole('button');
	await expect.element(points.nth(0)).toBeVisible();
	expect(document.querySelectorAll('[data-chart-point]')).toHaveLength(2);
	expect(document.querySelectorAll('[data-summary-date]')).toHaveLength(3);
	points.nth(0).element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.19');
	await expect.element(page.getByRole('tooltip')).not.toHaveTextContent('KST');
	await expect
		.element(page.getByRole('tooltip'))
		.toHaveTextContent(summary.id === 'sleep' ? '1시간 14분' : `74.25 ${summary.unit}`);
	await expect.element(page.getByRole('tooltip')).not.toHaveTextContent('999');
	await points.nth(1).click();
	await expect
		.element(page.getByRole('tooltip'))
		.toHaveTextContent(summary.id === 'sleep' ? '0시간 0분' : `0 ${summary.unit}`);
	await expect
		.element(page.getByRole('tooltip'))
		.toHaveTextContent('오늘 기록 · 기간 평균에서 제외');
	const missing = document.querySelector('[data-missing-point]');
	if (!missing) throw new Error('결측 지점 없음');
	// 포인터 이탈 이벤트를 직접 보내며 실제 선택 영역의 hover는 페이지 E2E에서 검증한다.
	points.nth(1).element().blur();
	missing.dispatchEvent(
		new PointerEvent('pointermove', {
			bubbles: true,
			pointerType: 'mouse',
			clientX: -1,
			clientY: -1
		})
	);
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	await expect.element(page.getByRole('figure')).toHaveTextContent('10.21: 동기화 대기');
	expect(document.querySelectorAll('[data-bar-selection]')).toHaveLength(0);
	await points.nth(1).click();
	await expect
		.element(page.getByRole('tooltip'))
		.toHaveTextContent(summary.id === 'sleep' ? '0시간 0분' : `0 ${summary.unit}`);
	window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
});

function navigationSummary(base = healthSummaries[0]) {
	const values = [74.25, 0, null, 62, null, 81, 90];
	const days = values.map((value, index) => ({
		date: adjacentDay('2026-10-19', index),
		value,
		missing: value === null,
		reason: value === null ? (index === 2 ? ('UNSYNCED' as const) : ('NO_RECORD' as const)) : null,
		isInProgress: index === 6
	}));
	return {
		...base,
		values,
		days,
		dates: days.map((day) => day.date.slice(5).replace('-', '.'))
	};
}

const dayHref = (date: string) => `/health/day/${date}`;
const metricHref = (summary: HealthSummary) => `/health/${summary.path}${summary.hash ?? ''}`;
const dateLinks = () => page.getByRole('link', { name: /하루 상세 보기$/ });

it.each(healthSummaries)(
	'$label 날짜 hover는 값·0·결측 모두 기존 하루 상세 이동 안내를 표시한다',
	async (base) => {
		const summary = navigationSummary(base);
		const navigations: string[] = [];
		const { container } = render(HealthSummaryChart, {
			summary,
			dayHref,
			metricHref: metricHref(summary),
			onDayNavigate(event, date) {
				event.preventDefault();
				navigations.push(date);
			}
		});
		container.style.paddingTop = '160px';
		const plot = container.querySelector('svg');
		if (!plot) throw new Error('날짜 안내의 차트가 필요합니다.');
		for (const index of [0, 1, 2]) {
			const date = dateLinks().nth(index);
			await date.hover();
			await expect.element(page.getByRole('tooltip')).toHaveTextContent('클릭하여 하루 상세 보기');
			await expect.element(page.getByRole('tooltip')).toBeVisible();
			const guide = page.getByRole('tooltip').element().getBoundingClientRect();
			expect(guide.top).toBeGreaterThanOrEqual(date.element().getBoundingClientRect().bottom + 8);
			expect(guide.top).toBeGreaterThanOrEqual(plot.getBoundingClientRect().bottom);
			expect(document.querySelectorAll('[role="tooltip"]')).toHaveLength(1);
			expect(
				container.querySelectorAll('[data-bar-selection], [data-point-selection]')
			).toHaveLength(0);
			expect(date.element().getAttribute('href')).toBe(dayHref(summary.days[index].date));
			await date.click();
			expect(navigations).toEqual(summary.days.slice(0, index + 1).map((day) => day.date));
		}
	}
);

it.each(healthSummaries)(
	'$label 정지 포인터의 레이아웃 진입은 숫자·결측 날짜의 키보드 선택을 바꾸지 않는다',
	async (base) => {
		const summary = navigationSummary(base);
		const { container } = render(HealthSummaryChart, {
			summary,
			dayHref,
			metricHref: metricHref(summary)
		});
		container.style.paddingTop = '160px';
		const links = dateLinks();
		const hoverPosition = async (target: ReturnType<typeof dateLinks>) => {
			let position: { x: number; y: number } | undefined;
			const record = (event: PointerEvent) => {
				if (event.pointerType === 'mouse') position = { x: event.clientX, y: event.clientY };
			};
			window.addEventListener('pointermove', record, true);
			try {
				await target.hover();
			} finally {
				window.removeEventListener('pointermove', record, true);
			}
			if (!position) throw new Error('실제 마우스 hover 좌표가 필요합니다.');
			return position;
		};
		const keyboardState = (index: number) => {
			const target = links.nth(index).element();
			const tooltip = container.querySelector('[role="tooltip"]');
			return {
				focused: document.activeElement === target,
				describedBy: target.getAttribute('aria-describedby'),
				tooltipId: tooltip?.id ?? null,
				text: tooltip?.textContent ?? null
			};
		};
		const layoutEnter = async (index: number, position: { x: number; y: number }) => {
			links
				.nth(index)
				.element()
				.dispatchEvent(
					new PointerEvent('pointerenter', {
						pointerType: 'mouse',
						clientX: position.x,
						clientY: position.y
					})
				);
			await tick();
		};
		const expectGuideBelow = async (index: number) => {
			await expect.element(page.getByRole('tooltip')).toHaveTextContent('클릭하여 하루 상세 보기');
			await expect.element(page.getByRole('tooltip')).toBeVisible();
			const guide = page.getByRole('tooltip').element();
			expect(guide.getBoundingClientRect().top).toBeGreaterThanOrEqual(
				links.nth(index).element().getBoundingClientRect().bottom + 8
			);
			expect(links.nth(index).element().getAttribute('aria-describedby')).toBe(guide.id);
		};

		const firstPosition = await hoverPosition(links.nth(5));
		await expectGuideBelow(5);
		await userEvent.keyboard('{Tab}');
		await expect.element(links.first()).toHaveFocus();
		await userEvent.keyboard('{End}');
		await expect.element(links.last()).toHaveFocus();
		await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.25');
		await expect
			.element(page.getByRole('tooltip'))
			.toHaveTextContent(base.id === 'sleep' ? formatSleepMinutes(90) : `90 ${base.unit}`);
		const numericBefore = keyboardState(6);
		expect(numericBefore.describedBy).toBe(numericBefore.tooltipId);
		expect(numericBefore.describedBy).not.toBeNull();
		await layoutEnter(5, firstPosition);
		const numericAfter = keyboardState(6);

		const secondPosition = await hoverPosition(links.nth(4));
		await expectGuideBelow(4);
		await userEvent.keyboard('{Home}{ArrowRight}');
		await expect.element(links.nth(1)).toHaveFocus();
		await expect
			.element(page.getByRole('tooltip'))
			.toHaveTextContent(base.id === 'sleep' ? '0시간 0분' : `0 ${base.unit}`);
		await userEvent.keyboard('{ArrowRight}');
		await expect.element(links.nth(2)).toHaveFocus();
		await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
		const missingBefore = keyboardState(2);
		expect(missingBefore.describedBy).toBeNull();
		await layoutEnter(4, secondPosition);
		const missingAfter = keyboardState(2);

		// 같은 날짜 안에서도 실제 좌표를 옮기면 키보드 선택 뒤에 마우스 안내를 다시 읽는다.
		const exposedDate = links.nth(4).element().getBoundingClientRect();
		await links.nth(4).hover({ position: { x: exposedDate.width / 2 + 2, y: 12 } });
		await expectGuideBelow(4);
		expect({ numeric: numericAfter, missing: missingAfter }).toEqual({
			numeric: numericBefore,
			missing: missingBefore
		});
	}
);

it.each(['nav', 'header'] as const)(
	'%s에 가리는 날짜 안내는 숨기고 공간이 생기면 그래프 아래에 다시 표시한다',
	async (tag) => {
		const { container } = render(HealthSummaryChart, {
			summary: navigationSummary(),
			dayHref,
			metricHref: metricHref(healthSummaries[0])
		});
		container.style.paddingTop = '160px';
		const date = dateLinks().first();
		await date.hover();
		await expect.element(page.getByRole('tooltip')).toBeVisible();
		const navigation = document.createElement(tag);
		navigation.setAttribute('aria-label', '주 메뉴');
		navigation.style.cssText =
			tag === 'nav'
				? `position:fixed;left:0;right:0;bottom:0;top:${date.element().getBoundingClientRect().bottom + 12}px`
				: `position:fixed;left:0;right:0;top:0;height:${date.element().getBoundingClientRect().bottom + 12}px`;
		document.body.append(navigation);
		try {
			window.dispatchEvent(new Event('resize'));
			await expect
				.poll(() => {
					const guide = container.querySelector('[role="tooltip"]');
					return guide ? getComputedStyle(guide).visibility : undefined;
				})
				.toBe('hidden');
			navigation.remove();
			window.dispatchEvent(new Event('resize'));
			await expect.element(page.getByRole('tooltip')).toBeVisible();
			expect(
				page.getByRole('tooltip').element().getBoundingClientRect().top
			).toBeGreaterThanOrEqual(date.element().getBoundingClientRect().bottom + 8);
		} finally {
			navigation.remove();
			window.dispatchEvent(new Event('resize'));
		}
	}
);

it.each(healthSummaries)(
	'$label 날짜 이동 모드는 값·0·결측의 라벨 행에만 겹치지 않는 날짜 링크를 제공한다',
	async (base) => {
		const summary = navigationSummary(base);
		const navigations: { date: string; href: string | null; mouseEvent: boolean }[] = [];
		const { container } = render(HealthSummaryChart, {
			summary,
			dayHref,
			metricHref: metricHref(summary),
			onDayNavigate(event, date) {
				event.preventDefault();
				if (!(event.currentTarget instanceof HTMLAnchorElement))
					throw new Error('날짜 이동 링크가 필요합니다.');
				navigations.push({
					date,
					href: event.currentTarget.getAttribute('href'),
					mouseEvent: event instanceof MouseEvent
				});
			}
		});
		container.style.paddingTop = '32px';
		const links = dateLinks();
		await expect.element(links.first()).toBeVisible();
		expect(container.querySelectorAll('button')).toHaveLength(0);
		expect(container.querySelectorAll('[data-date-target]')).toHaveLength(7);
		expect(container.querySelectorAll('[data-chart-point]')).toHaveLength(5);
		expect(container.querySelectorAll('[data-missing-point]')).toHaveLength(2);
		expect(container.querySelectorAll('[data-summary-date]')).toHaveLength(7);
		const elements = [...container.querySelectorAll<HTMLAnchorElement>('[data-date-target]')];
		const metric = container.querySelector<HTMLAnchorElement>('[data-metric-target]');
		const frameMetric = container.querySelector<HTMLAnchorElement>('[data-metric-frame-target]');
		expect(metric?.getAttribute('href')).toBe(metricHref(summary));
		expect(metric?.tabIndex).toBe(-1);
		expect(frameMetric?.getAttribute('href')).toBe(metricHref(summary));
		expect(frameMetric?.tabIndex).toBe(-1);
		expect(frameMetric?.getAttribute('aria-label')).toBe(`${base.label} 차트 상세 보기`);
		expect(elements.filter((link) => link.tabIndex === 0)).toHaveLength(1);
		const plot = elements[0].parentElement;
		if (!plot) throw new Error('날짜 링크의 차트 영역이 필요합니다.');
		expect(plot.style.minWidth).toBe('180px');
		plot.style.width = '180px';
		expect(elements[0].closest('[data-health-chart-scroll]')).not.toBeNull();
		const axis = container.querySelector('figure > div[aria-hidden="true"]');
		const tick = axis?.querySelector('span');
		const pane = elements[0].closest('[data-health-chart-scroll]');
		if (!axis || !tick || !pane) throw new Error('축과 차트 여백이 필요합니다.');
		const axisBounds = axis.getBoundingClientRect();
		const tickBounds = tick.getBoundingClientRect();
		const paneBounds = pane.getBoundingClientRect();
		for (const position of [
			{ x: axisBounds.x + axisBounds.width / 2, y: axisBounds.y + 40 },
			{ x: tickBounds.x + tickBounds.width / 2, y: tickBounds.y + tickBounds.height / 2 },
			{ x: paneBounds.right - 6, y: paneBounds.y + 60 }
		]) {
			expect(
				document.elementFromPoint(position.x, position.y)?.closest('[data-metric-frame-target]')
			).toBe(frameMetric);
		}
		for (const [index, link] of elements.entries()) {
			expect(link.getAttribute('href')).toBe(dayHref(summary.days[index].date));
			expect(link.dataset.dateTarget).toBe(summary.days[index].date);
			const bounds = link.getBoundingClientRect();
			expect(bounds.width).toBeGreaterThanOrEqual(24);
			expect(bounds.height).toBe(24);
			expect(bounds.top - plot.getBoundingClientRect().top).toBe(104);
			expect(bounds.bottom - plot.getBoundingClientRect().top).toBe(128);
			if (index > 0) {
				const previous = elements[index - 1].getBoundingClientRect();
				expect(previous.right).toBeLessThanOrEqual(bounds.left + 0.02);
			}
			const bar = container.querySelector(`[data-summary-bar="${index}"]`);
			if (bar) {
				const graphic = bar.getBoundingClientRect();
				expect(graphic.bottom).toBeLessThan(bounds.top);
				expect(
					document
						.elementFromPoint(graphic.x + graphic.width / 2, graphic.y + graphic.height / 2)
						?.closest('[data-metric-target]')
				).toBe(metric);
			}
			const point = container.querySelector<HTMLElement>(`[data-summary-point="${index}"]`);
			if (point) {
				const graphic = point.getBoundingClientRect();
				expect(graphic.bottom).toBeLessThan(bounds.top);
				expect(
					document
						.elementFromPoint(graphic.x + graphic.width / 2, graphic.y + graphic.height / 2)
						?.closest('[data-metric-target]')
				).toBe(metric);
			}
			const label = container.querySelectorAll('[data-summary-date]')[index];
			const labelBounds = label.getBoundingClientRect();
			expect(labelBounds.left).toBeGreaterThanOrEqual(bounds.left);
			expect(labelBounds.right).toBeLessThanOrEqual(bounds.right);
			expect(labelBounds.top - plot.getBoundingClientRect().top).toBe(110);
			expect(
				document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + 12)?.closest('a')
			).toBe(link);
		}
		await expect
			.element(links.nth(1))
			.toHaveAccessibleName(
				`${summary.days[1].date} ${base.id === 'heart' ? '일별 평균 심박수' : base.label} ${base.id === 'sleep' ? '0시간 0분' : `0 ${base.unit}`} 하루 상세 보기`
			);
		await expect
			.element(links.nth(2))
			.toHaveAccessibleName(
				`${summary.days[2].date} ${base.id === 'heart' ? '일별 평균 심박수' : base.label} 동기화 대기 하루 상세 보기`
			);
		await expect
			.element(links.nth(4))
			.toHaveAccessibleName(
				`${summary.days[4].date} ${base.id === 'heart' ? '일별 평균 심박수' : base.label} 기록 없음 하루 상세 보기`
			);
		await links.nth(1).click();
		await userEvent.keyboard('{Escape}');
		await links.nth(2).click();
		expect(navigations).toEqual([
			{ date: summary.days[1].date, href: dayHref(summary.days[1].date), mouseEvent: true },
			{ date: summary.days[2].date, href: dayHref(summary.days[2].date), mouseEvent: true }
		]);
		await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
		expect(container.querySelectorAll('[data-bar-selection]')).toHaveLength(0);
	}
);

it.each(healthSummaries)(
	'$label 막대·점·0·결측과 빈 배경은 날짜 이동 없이 기존 지표 상세 링크를 클릭한다',
	async (base) => {
		const summary = navigationSummary(base);
		const dayNavigations: string[] = [];
		const metricNavigations: string[] = [];
		const { container } = render(HealthSummaryChart, {
			summary,
			dayHref,
			metricHref: metricHref(summary),
			onDayNavigate(event, date) {
				event.preventDefault();
				dayNavigations.push(date);
			}
		});
		const metric = page.getByRole('link', { name: `${base.label} 상세 보기`, exact: true });
		await expect.element(metric).toBeVisible();
		expect(metric.element()).toBe(container.querySelector('[data-metric-target]'));
		expect(metric.element().closest('[data-health-chart-scroll]')).not.toBeNull();
		metric.element().addEventListener('click', (event) => {
			event.preventDefault();
			metricNavigations.push(metric.element().getAttribute('href') ?? '');
		});
		for (const index of [0, 1, 2, null]) {
			const bounds = metric.element().getBoundingClientRect();
			const graphic = container.querySelector(
				`[data-summary-${base.kind === 'bar' ? 'bar' : 'point'}="${index}"]`
			);
			const graphicBounds = graphic?.getBoundingClientRect();
			const position = graphicBounds
				? {
						x: graphicBounds.x + graphicBounds.width / 2 - bounds.left,
						y: graphicBounds.y + graphicBounds.height / 2 - bounds.top
					}
				: {
						x: index === null ? bounds.width / 2 : bounds.width * ((16 + index * (548 / 6)) / 580),
						y: index === null ? 8 : 96
					};
			expect(
				document
					.elementFromPoint(bounds.left + position.x, bounds.top + position.y)
					?.closest('[data-metric-target]')
			).toBe(metric.element());
			await metric.hover({ position });
			if (index === 0 || index === 1) {
				await expect
					.element(page.getByRole('tooltip'))
					.toHaveTextContent(summary.days[index].date.replaceAll('-', '.'));
			} else await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
			await metric.click({ position });
			await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
		}
		expect(metricNavigations).toEqual(Array(4).fill(metricHref(summary)));
		expect(dayNavigations).toEqual([]);
		expect(container.querySelectorAll('[data-date-target][tabindex="0"]')).toHaveLength(1);
	}
);

it('날짜 링크는 하나의 Tab 진입점에서 결측을 포함해 방향키·Home·End로 이동하고 Enter를 전달한다', async () => {
	const summary = navigationSummary();
	const navigations: string[] = [];
	const { container } = render(HealthSummaryChart, {
		summary,
		dayHref,
		metricHref: metricHref(summary),
		onDayNavigate(event, date) {
			event.preventDefault();
			navigations.push(date);
		}
	});
	const links = dateLinks();
	await expect.element(links.first()).toBeVisible();
	await userEvent.keyboard('{Tab}');
	links.first().element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.19');
	await userEvent.keyboard('{ArrowRight}');
	await expect.element(links.nth(1)).toHaveFocus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0 보');
	await userEvent.keyboard('{ArrowRight}');
	await expect.element(links.nth(2)).toHaveFocus();
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	expect(container.querySelectorAll('[data-bar-selection]')).toHaveLength(0);
	await userEvent.keyboard('{Enter}');
	expect(navigations).toEqual([summary.days[2].date]);
	await userEvent.keyboard('{End}');
	await expect.element(links.last()).toHaveFocus();
	await expect
		.element(page.getByRole('tooltip'))
		.toHaveTextContent('오늘 기록 · 기간 평균에서 제외');
	page.getByRole('tooltip').element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveFocus();
	await userEvent.keyboard('{Escape}');
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	await expect.element(links.last()).toHaveFocus();
	await userEvent.keyboard('{Home}');
	await expect.element(links.first()).toHaveFocus();
	const tabbable = [...container.querySelectorAll<HTMLAnchorElement>('[data-date-target]')].filter(
		(link) => link.tabIndex === 0
	);
	expect(tabbable).toEqual([links.first().element()]);
});

it('0값 날짜 라벨의 마우스 클릭은 하루 상세 이동을 한 번 전달한다', async () => {
	const summary = navigationSummary(healthSummaries[1]);
	const navigations: string[] = [];
	const { container } = render(HealthSummaryChart, {
		summary,
		dayHref,
		metricHref: metricHref(summary),
		onDayNavigate(event, date) {
			event.preventDefault();
			navigations.push(date);
		}
	});
	// 위쪽에 기존 32px 툴팁 기준 상자를 배치할 여유를 둔다.
	container.style.paddingTop = '160px';
	const zero = dateLinks().nth(1);
	await expect.element(zero).toBeVisible();
	await zero.click();
	expect(navigations).toEqual([summary.days[1].date]);
});

it('키보드로 진입한 날짜 라벨은 고정 헤더와 주 메뉴 사이에 노출하고 결측 포커스도 유지한다', async () => {
	const { container } = render(HealthSummaryChart, {
		summary: navigationSummary(),
		dayHref,
		metricHref: metricHref(healthSummaries[0])
	});
	const header = document.createElement('header');
	header.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:64px';
	const navigation = document.createElement('nav');
	navigation.setAttribute('aria-label', '주 메뉴');
	navigation.style.cssText = 'position:fixed;bottom:0;left:0;width:100%;height:80px';
	document.body.append(header, navigation);
	const originalScroll = window.scrollY;
	container.style.paddingTop = `${Math.max(0, innerHeight - 180)}px`;
	container.style.paddingBottom = '120px';
	try {
		const links = dateLinks();
		await expect.element(links.first()).toBeVisible();
		expect(links.first().element().getBoundingClientRect().bottom).toBeGreaterThan(
			navigation.getBoundingClientRect().top
		);
		await userEvent.keyboard('{Tab}');
		links.first().element().focus();
		await expect.element(links.first()).toHaveFocus();
		for (const [index, target] of [links.first(), links.nth(2)].entries()) {
			if (index > 0) {
				await userEvent.keyboard('{ArrowRight}{ArrowRight}');
				await expect.element(links.nth(2)).toHaveFocus();
			}
			const bounds = target.element().getBoundingClientRect();
			expect(bounds.top).toBeGreaterThanOrEqual(header.getBoundingClientRect().bottom);
			expect(bounds.bottom).toBeLessThanOrEqual(navigation.getBoundingClientRect().top);
		}
		await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	} finally {
		header.remove();
		navigation.remove();
		window.scrollTo({ top: originalScroll, behavior: 'instant' });
	}
});

it.each([1, 2])(
	'부분 응답 %i일은 실제 날짜만 링크로 만들고 결측에 가짜 막대·툴팁을 만들지 않는다',
	async (count) => {
		const days = Array.from({ length: count }, (_, index) => ({
			date: adjacentDay('2026-10-19', index),
			value: null,
			missing: true,
			reason: 'NO_RECORD' as const,
			isInProgress: false
		}));
		const { container } = render(HealthSummaryChart, {
			summary: {
				...healthSummaries[0],
				days,
				values: days.map((day) => day.value),
				dates: days.map((day) => day.date.slice(5).replace('-', '.')),
				average: null
			},
			dayHref,
			metricHref: metricHref(healthSummaries[0])
		});
		await expect.element(page.getByRole('figure')).toBeVisible();
		const links = [...container.querySelectorAll<HTMLAnchorElement>('[data-date-target]')];
		if (count === 2) {
			const plot = links[0].parentElement;
			if (!plot) throw new Error('부분 응답 차트 영역이 필요합니다.');
			plot.style.width = '180px';
			expect(links[1].getBoundingClientRect().right).toBeLessThan(
				plot.getBoundingClientRect().left + 90
			);
		}
		expect(links.map((link) => link.getAttribute('href'))).toEqual(
			days.map((day) => dayHref(day.date))
		);
		expect(
			container.querySelectorAll(
				'rect, [data-summary-tooltip-anchor], [data-bar-selection], button'
			)
		).toHaveLength(0);
		links[0].focus();
		await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	}
);
