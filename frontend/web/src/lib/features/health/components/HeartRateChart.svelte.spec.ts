import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import HeartRateChart from './HeartRateChart.svelte';
import { getHeartMockWeek } from '../heart-mock';
import { heartMissingMessage, type HeartMetricResult } from '../heart';
import { responsiveHeartFixture } from '../../../testing/heart-responsive-fixture';
import '../../../../routes/layout.css';

it('모바일은 숫자만 숨기고 분기점 이상은 점 옆에 세로 중심을 맞추며 원본 수치를 유지한다', async () => {
	const previous = { width: window.innerWidth, height: window.innerHeight };
	const result = getHeartMockWeek(-1);
	const original = structuredClone(result);
	render(HeartRateChart, { result });
	try {
		for (const width of [360, 390, 639, 640, 768, 1366, 1920, 390]) {
			await page.viewport(width, 900);
			const labels = [...document.querySelectorAll<HTMLElement>('[data-heart-label]')];
			expect(labels.map((label) => label.textContent)).toEqual(
				result.days
					.filter((day) => !day.missing && day.value !== null)
					.map((day) => String(day.value))
			);
			for (const label of labels) {
				if (width < 640) await expect.element(label).not.toBeVisible();
				else {
					await expect.element(label).toBeVisible();
					const box = label.getBoundingClientRect();
					const point = label.parentElement?.getBoundingClientRect();
					if (!point) throw new Error('평균점 없음');
					expect(box.top + box.height / 2).toBeCloseTo(point.top + point.height / 2, 1);
					const rightGap = box.left - point.right;
					const leftGap = point.left - box.right;
					expect(Math.min(Math.abs(rightGap - 5), Math.abs(leftGap - 5))).toBeLessThan(0.05);
				}
				// 비표시 중에도 기존 라벨 측정과 차트 배치를 유지한다.
				expect(label.offsetWidth).toBeGreaterThan(0);
				expect(label.offsetHeight).toBeGreaterThan(0);
			}
			for (const mark of document.querySelectorAll<HTMLElement>(
				'[data-heart-point], [data-heart-range]'
			)) {
				await expect.element(mark).toBeVisible();
			}
			await expect
				.element(page.getByText(`${result.summary.avg} bpm`, { exact: true }))
				.toBeVisible();
		}
		const point = page.getByRole('button', { name: `${result.days[0].date} 심박수 확인` });
		point.element().focus();
		await expect
			.element(page.getByRole('tooltip'))
			.toHaveTextContent(`${result.days[0].value} bpm`);
		await expect
			.element(page.getByRole('link', { name: `${result.days[0].date} 하루 상세` }))
			.toHaveAccessibleDescription(/평균 76 bpm/);
		expect(result).toEqual(original);
	} finally {
		await page.viewport(previous.width, previous.height);
	}
});

it.each([
	{ name: '상단 160 bpm 7일', value: 160, width: 1366, count: 7, single: false },
	{ name: '하단 40 bpm 7일', value: 40, width: 1920, count: 7, single: false },
	{ name: '평균만 있는 상단 하루', value: 160, width: 1366, count: 1, single: true }
])(
	'$name은 plot 밖 여유에서 숫자를 표시하고 범례·날짜·차트 요소와 겹치지 않는다',
	async ({ value, width, count, single }) => {
		const previous = { width: window.innerWidth, height: window.innerHeight };
		try {
			await page.viewport(width, 900);
			const result: HeartMetricResult = responsiveHeartFixture(
				'2026-11-10',
				Array.from({ length: 7 }, (_, index) =>
					single && index > 0 ? null : { avg: value, min: value, max: value, hours: 12 }
				)
			);
			if (single) {
				result.detail.dailyRanges[0] = { date: result.days[0].date, min: null, max: null };
				result.detail.measuredMin = null;
				result.detail.measuredMax = null;
			}
			const original = structuredClone(result);
			render(HeartRateChart, { result });
			await document.fonts.ready;
			const card = page.getByRole('region', { name: '일별 심박수' }).element();
			const labels = [...card.querySelectorAll<HTMLElement>('[data-heart-label]')];
			expect(labels).toHaveLength(count);
			await expect
				.poll(
					() =>
						labels.filter(
							(label) =>
								getComputedStyle(label).visibility === 'visible' &&
								label.getBoundingClientRect().width > 0
						).length
				)
				.toBe(count);
			const bounds = card.getBoundingClientRect();
			const boxes = labels.map((label) => label.getBoundingClientRect());
			const obstacles = [
				...card.querySelectorAll<HTMLElement>(
					'.heart-legend, [data-chart-navigation], [data-heart-point], [data-heart-range]'
				)
			].map((node) => {
				const box = node.getBoundingClientRect();
				if (!node.hasAttribute('data-heart-range')) return box;
				const center = box.left + box.width / 2;
				const cap = Number.parseFloat(getComputedStyle(node, '::before').borderTopWidth);
				return {
					left: center - 9,
					right: center + 9,
					top: box.top - cap,
					bottom: box.bottom + cap
				};
			});
			const overlaps = (a: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>, b: typeof a) =>
				a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
			for (const [index, label] of labels.entries()) {
				await expect.element(label).toBeVisible();
				const point = label.parentElement;
				if (!point) throw new Error('평균점 없음');
				const dot = point.getBoundingClientRect();
				const box = boxes[index];
				expect(box.left - dot.right).toBeCloseTo(5, 1);
				expect(box.top + box.height / 2).toBeCloseTo(dot.top + dot.height / 2, 1);
				expect(box.left).toBeGreaterThanOrEqual(bounds.left);
				expect(box.right).toBeLessThanOrEqual(bounds.right);
				expect(box.top).toBeGreaterThanOrEqual(bounds.top);
				expect(box.bottom).toBeLessThanOrEqual(bounds.bottom);
				expect(
					[...obstacles, ...boxes.slice(index + 1)].some((other) => overlaps(box, other))
				).toBe(false);
				expect(point.style.top).toBe(value === 160 ? '0%' : '100%');
			}
			expect(card.querySelectorAll('[data-heart-range]')).toHaveLength(single ? 0 : 7);
			await expect.element(page.getByText(`${value} bpm`, { exact: true })).toBeVisible();
			for (const index of single ? [0] : [0, 6]) {
				const day = result.days[index];
				page
					.getByRole('button', { name: `${day.date} 심박수 확인` })
					.element()
					.focus();
				await expect.element(page.getByRole('tooltip')).toBeVisible();
				expect(
					[...page.getByRole('tooltip').element().querySelectorAll('dd')].map(
						(node) => node.textContent
					)
				).toEqual([`${value} bpm`, single ? '—' : `${value} bpm`, single ? '—' : `${value} bpm`]);
				window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
				await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
				const link = page.getByRole('link', { name: `${day.date} 하루 상세` });
				await expect.element(link).toHaveAttribute('href', `/health/day/${day.date}`);
				await expect
					.element(link)
					.toHaveAccessibleDescription(
						single
							? '평균 160 bpm. 최저 기록 없음. 최고 기록 없음.'
							: `평균 ${value} bpm. 최저 ${value} bpm. 최고 ${value} bpm.`
					);
			}
			expect(result).toEqual(original);
		} finally {
			await page.viewport(previous.width, previous.height);
		}
	}
);

it('평균이 null이어도 실제 범위가 있으면 확인하고 모두 없으면 빈 상태를 유지한다', async () => {
	const result = getHeartMockWeek(-2);
	result.days[0].missing = false;
	result.detail.dailyRanges = [{ date: result.days[0].date, min: 62, max: 105 }];
	const { rerender } = render(HeartRateChart, { result });
	const point = page.getByRole('button', { name: `${result.days[0].date} 심박수 확인` });
	point.element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('62 bpm');
	expect(
		[...page.getByRole('tooltip').element().querySelectorAll('dd')].map((node) => node.textContent)
	).toEqual(['—', '62 bpm', '105 bpm']);
	await rerender({ result: { ...result, detail: { ...result.detail, dailyRanges: [] } } });
	await expect
		.element(page.getByRole('status'))
		.toHaveTextContent('선택한 기간에 심박 기록이 없어요.');
	await expect.element(page.getByRole('button')).not.toBeInTheDocument();
});

it('툴팁은 날짜로 평균·최저·최고를 결합하며 0과 null을 구분한다', async () => {
	const result = getHeartMockWeek(-1);
	result.days[0].value = 0;
	result.days[1].value = null;
	result.days[1].missing = false;
	result.detail.dailyRanges = [
		{ date: result.days[1].date, min: 62, max: null },
		{ date: result.days[0].date, min: null, max: 105 }
	];
	const original = structuredClone(result);
	render(HeartRateChart, { result });
	const first = page.getByRole('button', { name: `${result.days[0].date} 심박수 확인` });
	await first.hover();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.19');
	expect(
		[...page.getByRole('tooltip').element().querySelectorAll('dd')].map((node) => node.textContent)
	).toEqual(['0 bpm', '—', '105 bpm']);
	page
		.getByRole('button', { name: `${result.days[1].date} 심박수 확인` })
		.element()
		.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.20');
	expect(
		[...page.getByRole('tooltip').element().querySelectorAll('dd')].map((node) => node.textContent)
	).toEqual(['—', '62 bpm', '—']);
	const link = page.getByRole('link', { name: `${result.days[0].date} 하루 상세` });
	link.element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('클릭하여 하루 상세 보기');
	window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	expect(link.element().getAttribute('aria-describedby')).toBe(`heart-day-${result.days[0].date}`);
	expect(result).toEqual(original);
});

it('상단은 전달된 summary를 사용하고 평균선 없이 일별 범위를 날짜로 결합한다', async () => {
	const result = getHeartMockWeek(-1);
	result.summary.avg = 81.23;
	// 배열 순서를 신뢰하지 않고 같은 날짜의 범위를 찾는다.
	result.detail.dailyRanges.reverse();
	render(HeartRateChart, { result });
	const heading = page.getByRole('heading', { name: '일별 심박수', exact: true });
	await expect.element(heading).toBeVisible();
	expect(heading.element().parentElement?.querySelector('p')).toBeNull();
	await expect.element(page.getByText('평균 심박수', { exact: true })).toBeVisible();
	await expect.element(page.getByText('81.23 bpm', { exact: true })).toBeVisible();
	const legend = page.getByRole('list', { name: '심박 차트 범례' });
	await expect.element(legend.getByText('최저–최고 범위', { exact: true })).toBeVisible();
	await expect.element(legend.getByText('하루 평균', { exact: true })).toBeVisible();
	expect(document.querySelector('[data-average-line]')).toBeNull();
	expect(document.querySelectorAll('[data-heart-point]')).toHaveLength(6);
	expect(document.querySelectorAll('[data-heart-range]')).toHaveLength(6);
	expect(document.querySelector('[data-heart-point="2026-10-21"]')).toBeNull();
	expect(document.querySelector('[data-heart-range="2026-10-21"]')).toBeNull();
	const range = document.querySelector<HTMLElement>('[data-heart-range="2026-10-19"]');
	// 54~132는 기본 40~160 축에서 위 23.33%, 높이 65%다.
	expect(Number.parseFloat(range?.style.top ?? '')).toBeCloseTo(23.333);
	expect(Number.parseFloat(range?.style.height ?? '')).toBeCloseTo(65);
	await expect
		.element(page.getByRole('link', { name: '2026-10-19 하루 상세' }))
		.toHaveAttribute('href', '/health/day/2026-10-19');
});

it('평균만 있는 응답에서 최저·최고나 중간값을 만들어내지 않는다', async () => {
	const result = getHeartMockWeek(0);
	result.detail.dailyRanges[0] = { date: result.days[0].date, min: null, max: null };
	render(HeartRateChart, { result });
	await expect.element(page.getByText('집계된 평균 없음', { exact: true })).toBeVisible();
	expect(document.querySelectorAll('[data-heart-point]')).toHaveLength(1);
	expect(document.querySelectorAll('[data-heart-range]')).toHaveLength(0);
	expect(document.querySelectorAll('[data-average-line]')).toHaveLength(0);
	await expect
		.element(page.getByRole('link', { name: '2026-10-26 하루 상세' }))
		.toHaveAccessibleDescription(/평균 74.2 bpm.*최저 기록 없음.*최고 기록 없음/);
});

it('모두 결측이면 숫자 0과 평균선·점·범위 없이 사유를 유지한다', async () => {
	const result = getHeartMockWeek(-2);
	const reasons = ['BEFORE_LINK', 'UNSYNCED', 'NO_RECORD', 'NULL_VALUE'] as const;
	result.days = result.days.slice(0, 4).map((day, i) => ({ ...day, reason: reasons[i] }));
	render(HeartRateChart, { result });
	await expect
		.element(page.getByRole('status'))
		.toHaveTextContent('선택한 기간에 심박 기록이 없어요.');
	expect(
		document.querySelectorAll('[data-heart-point], [data-heart-range], [data-average-line]')
	).toHaveLength(0);
	await expect.element(page.getByText('집계된 평균 없음', { exact: true })).toBeVisible();
	for (const [index, day] of result.days.entries()) {
		await expect
			.element(page.getByRole('link', { name: `${day.date} 하루 상세` }))
			.toHaveAccessibleDescription(heartMissingMessage(reasons[index]));
	}
});

it('같은 날짜의 점·범위·날짜 링크가 하나의 x 좌표를 공유한다', async () => {
	const result = getHeartMockWeek(-1);
	render(HeartRateChart, { result });
	await expect.element(page.getByRole('region', { name: '일별 심박수' })).toBeVisible();
	for (const day of result.days.filter((day) => !day.missing)) {
		const point = document.querySelector<HTMLElement>(`[data-heart-point="${day.date}"]`);
		const range = document.querySelector<HTMLElement>(`[data-heart-range="${day.date}"]`);
		const link = document.querySelector<HTMLElement>(`a[aria-label="${day.date} 하루 상세"]`);
		expect(point?.style.left).toBe(range?.style.left);
		expect(point?.style.left).toBe(link?.style.left);
	}
});

it('현재 주의 하루만 표시해도 월요일 첫 slot에 점·범위·날짜를 배치한다', async () => {
	render(HeartRateChart, { result: getHeartMockWeek(0) });
	await expect.element(page.getByRole('link', { name: '2026-10-26 하루 상세' })).toBeVisible();
	const point = document.querySelector<HTMLElement>('[data-heart-point="2026-10-26"]');
	const range = document.querySelector<HTMLElement>('[data-heart-range="2026-10-26"]');
	const link = document.querySelector<HTMLElement>('a[aria-label="2026-10-26 하루 상세"]');
	expect(Number.parseFloat(point?.style.left ?? '')).toBeCloseTo(100 / 14);
	expect(point?.style.left).toBe(range?.style.left);
	expect(point?.style.left).toBe(link?.style.left);
	expect(document.querySelectorAll('.heart-chart a')).toHaveLength(1);
	expect(document.querySelectorAll('[data-heart-point]')).toHaveLength(1);
	expect(document.querySelectorAll('[data-heart-range]')).toHaveLength(1);
});
