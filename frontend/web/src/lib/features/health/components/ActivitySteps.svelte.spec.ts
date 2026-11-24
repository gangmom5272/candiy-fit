import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ActivitySteps from './ActivitySteps.svelte';
import ActivityAverageChart from './ActivityAverageChart.svelte';
import { formatActivityChange, getActivityPeriod, weekdayOrder } from '../activity';

it.each([
	[-0.03332222592469177, '0.0%', 'text-text-primary'],
	[0.03333333333333333, '0.0%', 'text-text-primary'],
	[0, '0.0%', 'text-text-primary'],
	[-0.05, '-0.1%', 'text-text-primary'],
	[0.05, '+0.1%', 'text-text-primary'],
	[null, '—', 'text-text-primary']
])('증감률 %s의 표시 %s와 상태 색상이 일치한다', async (value, label, colorClass) => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		summary: { average: '3,000보', recordedDays: 7, change: formatActivityChange(value) }
	});
	const change = page.getByText(label, { exact: true });
	await expect.element(change).toBeVisible();
	expect(change.element().classList.contains(colorClass)).toBe(true);
	for (const other of ['text-text-secondary', 'text-info', 'text-text-primary']) {
		if (other !== colorClass) expect(change.element().classList.contains(other)).toBe(false);
	}
});

it('평균 데이터가 갱신되면 이전 선택을 정리하고 새 값으로 다시 열 수 있다', async () => {
	const { rerender } = render(ActivityAverageChart, {
		values: [100, null, 0, 200],
		labels: ['월', '화', '수', '목'],
		caption: '선택 초기화 검증',
		period: '최근 4주'
	});
	await page.getByRole('button', { name: '월 · 평균 100보' }).click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('100보');
	await rerender({ values: [50, null, 0, 300] });
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	await expect
		.element(page.getByRole('button', { name: '월 · 평균 50보' }))
		.not.toHaveAttribute('aria-describedby');
	await page.getByRole('button', { name: '목 · 평균 300보' }).click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('300보');
});

it.each([
	['+10.9%', 'text-text-primary'],
	['-34.0%', 'text-text-primary'],
	['+0.0%', 'text-text-primary'],
	['—', 'text-text-primary']
])('증감률 %s의 부호와 대비 색상 %s를 유지한다', async (change, tone) => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		summary: { average: '7,760보', change, recordedDays: 6 }
	});
	await expect.element(page.getByText(change, { exact: true })).toHaveClass(tone);
});

it('표시값이 같은 정수여도 최고 요일과 막대 비율은 반올림 전 평균으로 판단한다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		recentDays: [
			{ date: '2026-10-05', value: 1, missing: false },
			{ date: '2026-10-12', value: 2, missing: false },
			{ date: '2026-10-06', value: 1, missing: false },
			{ date: '2026-10-13', value: 2, missing: false },
			{ date: '2026-10-20', value: 2, missing: false },
			{ date: '2026-10-07', value: 0, missing: false }
		]
	});
	await expect.element(page.getByRole('definition').nth(0)).toHaveTextContent('월요일 · 2보');
	await expect.element(page.getByRole('definition').nth(1)).toHaveTextContent('화요일 · 2보');
	await expect
		.element(page.getByRole('button', { name: '화요일 · 평균 2보', exact: true }))
		.toBeVisible();
	const monday = page
		.getByRole('button', { name: '월요일 · 평균 2보', exact: true })
		.element()
		.querySelector<HTMLElement>('[data-average-bar]');
	expect(Number.parseFloat(monday?.style.height ?? '')).toBeCloseTo(72);
});

it('0 평균은 선택 가능한 값으로 유지하고 null 요일·날짜는 평균에서 제외한다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		recentDays: [
			{ date: '2026-10-19', value: 0, missing: false },
			{ date: '2026-10-20', value: null, missing: false }
		],
		hourlyDays: [
			{ date: '2026-10-19', hourlySteps: Array(24).fill(0) },
			{ date: '2026-10-20', hourlySteps: null }
		]
	});
	const weekday = page.getByRole('figure', { name: '요일별 평균 걸음 수 차트' });
	expect(weekday.element().querySelectorAll('button')).toHaveLength(1);
	await weekday.getByRole('button', { name: '월요일 · 평균 0보' }).click();
	await expect.element(weekday.getByRole('tooltip')).toHaveTextContent('0보');
	const hourly = page.getByRole('figure', { name: '시간대별 평균 걸음 수 분포' });
	expect(hourly.element().querySelectorAll('button')).toHaveLength(0);
	await expect.element(hourly.getByRole('tooltip')).not.toBeInTheDocument();
	await expect.element(page.getByRole('definition').nth(0)).toHaveTextContent('—');
	await expect.element(page.getByRole('definition').nth(1)).toHaveTextContent('월요일 · 0보');
	await expect.element(page.getByRole('definition').nth(2)).toHaveTextContent('—');
	await expect.element(page.getByRole('definition').nth(3)).toHaveTextContent('00시 · 0보');
	expect(document.querySelectorAll('.step-summary-card dt')).toHaveLength(4);
	expect(
		Array.from(document.querySelectorAll('.step-summary-card dt')).map((node) => node.textContent)
	).toEqual(['최소', '최대', '최소', '최대']);
});

it.each([true, false])(
	'오늘 기록 존재 여부(%s)에 따라 완료 기록 0일과 빈 상태를 구분한다',
	async (recorded) => {
		render(ActivitySteps, {
			period: {
				label: '2026.10.26 ~ 11.01',
				hasRecords: recorded,
				days: [{ date: '2026-10-26', label: '10/26', weekday: '월', steps: recorded ? 1000 : null }]
			},
			summary: { average: '집계된 평균 없음', change: '—', recordedDays: 0 },
			average: null,
			referenceDate: '2026-10-26',
			recentDays: [
				{ date: '2026-10-19', value: 6000, missing: false, isInProgress: false },
				{ date: '2026-10-26', value: 1000, missing: false, isInProgress: true }
			]
		});
		if (recorded) {
			await expect.element(page.getByText(/평균에 반영된 완료 기록 0일/)).toHaveClass('sr-only');
			await expect.element(page.getByRole('link', { name: /1,000보.*10\/26/ })).toBeVisible();
			await expect.element(page.getByText('집계된 평균 없음', { exact: true })).toBeVisible();
		} else {
			await expect.element(page.getByText('선택한 기간의 걸음 수 기록이 없어요.')).toBeVisible();
			await expect.element(page.getByText(/평균에 반영된 완료 기록 0일/)).not.toBeInTheDocument();
		}
		await expect.element(page.getByRole('button', { name: '월요일 · 평균 6,000보' })).toBeVisible();
	}
);

function renderedBarOpacity(button: Element) {
	return Number(getComputedStyle(button.firstElementChild ?? button).opacity);
}

it.each([{ values: [3200, 7100, 5000] }, { values: [7100, 3200, 5000] }])(
	'요일 위치와 무관하게 큰 값일수록 진하고 결측값은 색상 기준에서 제외한다: $values',
	async ({ values }) => {
		const period = getActivityPeriod(0);
		render(ActivitySteps, {
			period,
			recentDays: [
				...values.map((value, index) => ({ date: period.days[index].date, value, missing: false })),
				{ date: period.days[3].date, value: 99999, missing: true },
				{ date: period.days[4].date, value: null, missing: false }
			]
		});
		const chart = page.getByRole('figure', { name: '요일별 평균 걸음 수 차트' });
		const buttons = Array.from(chart.element().querySelectorAll('button'));
		expect(buttons).toHaveLength(3);
		expect(new Set(buttons.map((button) => getComputedStyle(button).backgroundColor)).size).toBe(1);
		const ranked = buttons
			.map((button, index) => ({ value: values[index], opacity: renderedBarOpacity(button) }))
			.sort((left, right) => left.value - right.value);
		expect(ranked[0].opacity).toBeLessThan(ranked[1].opacity);
		expect(ranked[1].opacity).toBeLessThan(ranked[2].opacity);
		expect(ranked[2].opacity).toBe(1);
	}
);

it('시간에 관계없이 실제 평균이 큰 막대가 진하고 같은 값은 같은 진하기를 쓴다', () => {
	const values = Array(24).fill(0);
	values[1] = 1100;
	values[12] = 520;
	values[18] = 120;
	values[23] = 120;
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		hourlyDays: [{ date: '2026-10-19', hourlySteps: values }]
	});
	const opacity = (name: string) =>
		renderedBarOpacity(page.getByRole('button', { name, exact: true }).element());
	expect(opacity('01시 · 평균 1,100보')).toBeGreaterThan(opacity('12시 · 평균 520보'));
	expect(opacity('12시 · 평균 520보')).toBeGreaterThan(opacity('18시 · 평균 120보'));
	expect(opacity('18시 · 평균 120보')).toBe(opacity('23시 · 평균 120보'));
	expect(opacity('01시 · 평균 1,100보')).toBe(1);
	expect(page.getByRole('definition').nth(0).element().textContent).toContain('18시 · 120보');
	expect(values[0]).toBe(0);
});

it.each([1, 7])(
	'유효한 요일 %i개의 값이 모두 같으면 두 차트 모두 동일한 진하기를 쓴다',
	async (count) => {
		const period = getActivityPeriod(0);
		render(ActivitySteps, {
			period,
			recentDays: period.days
				.slice(0, count)
				.map(({ date }) => ({ date, value: 420, missing: false })),
			hourlyDays: [{ date: period.days[0].date, hourlySteps: Array(24).fill(420) }]
		});
		for (const label of ['요일별 평균 걸음 수 차트', '시간대별 평균 걸음 수 분포']) {
			const chart = page.getByRole('figure', { name: label });
			const strengths = Array.from(chart.element().querySelectorAll('button')).map(
				renderedBarOpacity
			);
			expect(strengths.length).toBeGreaterThan(0);
			expect(new Set(strengths).size).toBe(1);
			expect(strengths[0]).toBe(1);
		}
		const peak = page.getByRole('definition');
		await expect
			.element(peak.nth(0))
			.toHaveTextContent(`${weekdayOrder.slice(0, count).join('·')}요일 · 420보`);
		await expect
			.element(peak.nth(1))
			.toHaveTextContent(`${weekdayOrder.slice(0, count).join('·')}요일 · 420보`);
		await expect.element(peak.nth(2)).toHaveTextContent('00시 · 420보');
	}
);

it('소수 평균을 정수로 읽되 막대 높이와 원본 평균은 반올림하지 않는다', async () => {
	const period = getActivityPeriod(0);
	const hourlyDays = [5291, 5292, 5292].map((value, index) => ({
		date: period.days[index].date,
		hourlySteps: Array.from({ length: 24 }, (_, hour) => (hour === 0 ? value : 10000))
	}));
	const original = structuredClone(hourlyDays);
	render(ActivitySteps, {
		period,
		recentDays: [
			{ date: '2026-10-10', value: 5291, missing: false },
			{ date: '2026-10-17', value: 5292, missing: false },
			{ date: '2026-10-24', value: 5292, missing: false }
		],
		hourlyDays
	});
	const weekday = page.getByRole('button', { name: '토요일 · 평균 5,292보', exact: true });
	await weekday.click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('5,292보');
	const hour = page.getByRole('button', { name: '00시 · 평균 5,292보', exact: true });
	await hour.click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('5,292보');
	const bar = hour.element().querySelector<HTMLElement>('[data-average-bar]');
	expect(Number.parseFloat(bar?.style.height ?? '')).toBeCloseTo(
		((5291 + 5292 + 5292) / 3 / 10000) * 80,
		4
	);
	expect(hourlyDays).toEqual(original);
});

it('시간대 평균은 null 하루를 빼고 0을 포함한 정확한 값을 버튼과 요약에 표시한다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		hourlyDays: [
			{ date: '2026-10-20', hourlySteps: Array(24).fill(0) },
			{ date: '2026-10-21', hourlySteps: Array(24).fill(200) },
			{ date: '2026-10-22', hourlySteps: null }
		]
	});
	const bar = page.getByRole('button', { name: '18시 · 평균 100보', exact: true });
	await bar.click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('18시 · 평균');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('100보');
	const summary = page.getByRole('definition');
	expect(summary.all()).toHaveLength(2);
	await expect.element(summary.nth(0)).toHaveTextContent('00시 · 100보');
	await expect.element(summary.nth(1)).toHaveTextContent('00시 · 100보');
});

it('유효한 시간대 데이터가 없으면 막대나 0 요약을 만들지 않는다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		recentDays: [],
		hourlyDays: [{ date: '2026-10-20', hourlySteps: null }]
	});
	await expect
		.element(page.getByText('최근 4주에 걸음 수 기록이 없어요.', { exact: true }))
		.toBeVisible();
	await expect
		.element(page.getByText('선택한 기간의 기록이 없어요.', { exact: true }))
		.toBeVisible();
	expect(document.querySelectorAll('figure button')).toHaveLength(0);
	expect(document.querySelector('dl')).toBeNull();
	expect(document.querySelectorAll('.step-summary-card [aria-live]')).toHaveLength(0);
});

it('하단 안내 없이 막대 근처 툴팁에 선택한 시간대의 값을 표시한다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		recentDays: [{ date: '2026-10-26', value: 6842, missing: false }],
		hourlyDays: [
			{ date: '2026-10-20', hourlySteps: Array.from({ length: 24 }, (_, hour) => hour * 96) }
		]
	});
	const hint = page.getByText('막대를 선택하면 시간대 평균 걸음 수를 볼 수 있어요.', {
		exact: true
	});
	await expect
		.element(page.getByText('막대를 선택하면 요일별 평균 걸음 수를 볼 수 있어요.', { exact: true }))
		.not.toBeInTheDocument();
	await expect.element(hint).not.toBeInTheDocument();
	await page.getByRole('button', { name: '01시 · 평균 96보', exact: true }).click();
	await expect.element(hint).not.toBeInTheDocument();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('01시 · 평균');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('96보');
	await page.getByRole('button', { name: '02시 · 평균 192보', exact: true }).click();
	await expect.element(page.getByRole('tooltip')).not.toHaveTextContent('01시 · 평균');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('02시 · 평균');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('192보');
});

it('요일별 평균은 유효한 요일만 막대를 만들고 선택한 요일의 값으로 안내를 교체한다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		recentDays: [
			{ date: '2026-10-19', value: 6000, missing: false },
			{ date: '2026-10-26', value: 8000, missing: false },
			{ date: '2026-10-23', value: 5120, missing: false },
			{ date: '2026-10-20', value: 0, missing: true },
			{ date: '2026-10-21', value: null, missing: false },
			{ date: '2026-09-22', value: 9999, missing: false }
		]
	});
	const chart = page.getByRole('figure', { name: '요일별 평균 걸음 수 차트' });
	expect(chart.element().querySelectorAll('button')).toHaveLength(2);
	const hint = page.getByText('막대를 선택하면 요일별 평균 걸음 수를 볼 수 있어요.', {
		exact: true
	});
	await expect.element(hint).not.toBeInTheDocument();
	await page.getByRole('button', { name: '월요일 · 평균 7,000보', exact: true }).click();
	await expect.element(hint).not.toBeInTheDocument();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('월요일 · 평균');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('7,000보');
	page.getByRole('button', { name: '금요일 · 평균 5,120보', exact: true }).element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('금요일 · 평균');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('5,120보');
	await expect.element(page.getByRole('tooltip')).not.toHaveTextContent('월요일 · 평균');
	expect(chart.element().textContent).toContain('기록 없음');
});

it('최근 28일에 missing 기록만 있으면 숫자가 있어도 막대나 선택 안내를 만들지 않는다', async () => {
	render(ActivitySteps, {
		period: getActivityPeriod(0),
		recentDays: [
			{ date: '2026-10-26', value: 9999, missing: true },
			{ date: '2026-10-20', value: 0, missing: true },
			{ date: '2026-10-21', value: null, missing: true }
		]
	});
	await expect
		.element(page.getByText('최근 4주에 걸음 수 기록이 없어요.', { exact: true }))
		.toBeVisible();
	expect(
		document.querySelectorAll('.step-summary-card button, .step-summary-card [aria-live]')
	).toHaveLength(0);
});

it.each([1920, 1366, 768, 360])(
	'%ipx에서 데이터 미전달 시 mock 없이 두 빈 상태만 겹치지 않게 표시한다',
	async (width) => {
		const original = { width: window.innerWidth, height: window.innerHeight };
		try {
			await page.viewport(width, 1080);
			render(ActivitySteps, { period: getActivityPeriod(0) });
			await expect
				.element(page.getByText('최근 4주에 걸음 수 기록이 없어요.', { exact: true }))
				.toBeVisible();
			await expect
				.element(page.getByText('선택한 기간의 기록이 없어요.', { exact: true }))
				.toBeVisible();
			expect(
				document.querySelectorAll(
					'.step-summary-card button, .step-summary-card figure, .step-summary-card [aria-live], .step-summary-card dl'
				)
			).toHaveLength(0);
			const cards = Array.from(document.querySelectorAll('.step-summary-card'));
			expect(cards).toHaveLength(2);
			for (const card of cards) {
				const bounds = card.getBoundingClientRect();
				expect(bounds.left).toBeGreaterThanOrEqual(0);
				expect(bounds.right).toBeLessThanOrEqual(document.documentElement.clientWidth);
				const content = Array.from(card.children).map((child) => child.getBoundingClientRect());
				for (let index = 1; index < content.length; index++) {
					expect(content[index].top).toBeGreaterThanOrEqual(content[index - 1].bottom);
				}
				expect(content.at(-1)?.bottom).toBeLessThanOrEqual(bounds.bottom);
				expect(card.scrollWidth).toBeLessThanOrEqual(card.clientWidth);
			}
		} finally {
			await page.viewport(original.width, original.height);
		}
	}
);
