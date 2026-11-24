import { page } from 'vitest/browser';
import { expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import HealthSummaryCard from './HealthSummaryCard.svelte';
import { healthSummaries, type HealthSummary } from '../summary';

const metricLinks = [
	{
		summary: healthSummaries[0],
		href: '/health/activity',
		datedHref: '/health/activity?date=2026-10-25',
		nextHref: '/health/activity?date=2025-12-31'
	},
	{
		summary: healthSummaries[1],
		href: '/health/activity#exercise',
		datedHref: '/health/activity?date=2026-10-25#exercise',
		nextHref: '/health/activity?date=2025-12-31#exercise'
	},
	{
		summary: healthSummaries[2],
		href: '/health/sleep',
		datedHref: '/health/sleep?date=2026-10-25',
		nextHref: '/health/sleep?date=2025-12-31'
	},
	{
		summary: healthSummaries[3],
		href: '/health/heart',
		datedHref: '/health/heart?date=2026-10-25',
		nextHref: '/health/heart?date=2025-12-31'
	}
];

function summaryWithDays(summary: HealthSummary): HealthSummary {
	return {
		...summary,
		days: summary.values.map((value, index) => ({
			date: `2026-10-${19 + index}`,
			value,
			missing: value === null,
			reason: value === null ? 'NO_RECORD' : null,
			isInProgress: false
		}))
	};
}

it.each(metricLinks)(
	'$summary.label 카드에 기준일을 전달하지 않으면 기존 상세 경로를 유지한다',
	async ({ summary, href }) => {
		render(HealthSummaryCard, { summary });
		await expect.element(page.getByRole('link')).toHaveAttribute('href', href);
		await expect.element(page.getByRole('figure')).toBeVisible();
	}
);

it.each(metricLinks)(
	'$summary.label 카드와 차트의 상세 링크는 기준일을 함께 갱신하고 날짜별 링크는 유지한다',
	async ({ summary: base, href, datedHref, nextHref }) => {
		const summary = summaryWithDays(base);
		const dayHref = (date: string) => `/health/day/${date}`;
		const { container, rerender } = render(HealthSummaryCard, { summary, dayHref });

		async function expectMetricLinks(expected: string) {
			const card = container.querySelector<HTMLAnchorElement>('.health-summary-link');
			expect(card?.getAttribute('href')).toBe(expected);
			await expect
				.element(page.getByRole('link', { name: `${summary.label} 상세 보기`, exact: true }))
				.toHaveAttribute('href', expected);
			await expect
				.element(page.getByRole('link', { name: `${summary.label} 차트 상세 보기`, exact: true }))
				.toHaveAttribute('href', expected);
			const dates = [...container.querySelectorAll<HTMLAnchorElement>('[data-date-target]')];
			expect(dates.map((link) => link.getAttribute('href'))).toEqual(
				summary.days!.map((day) => `/health/day/${day.date}`)
			);
		}

		await expectMetricLinks(href);
		await rerender({ summary, dayHref, referenceDate: '2026-10-25' });
		await expectMetricLinks(datedHref);
		await rerender({ summary, dayHref, referenceDate: '2025-12-31' });
		await expectMetricLinks(nextHref);
		await rerender({ summary, dayHref, referenceDate: undefined });
		await expectMetricLinks(href);
	}
);

it('로딩은 수치나 이동 링크 없이 알리고 오류·빈 기록과 구분한다', async () => {
	render(HealthSummaryCard, {
		label: '걸음 수',
		loadingMessage: '선택한 기간을 다시 조회하고 있어요.'
	});
	await expect
		.element(page.getByRole('status'))
		.toHaveTextContent('선택한 기간을 다시 조회하고 있어요.');
	await expect.element(page.getByRole('link')).not.toBeInTheDocument();
	await expect.element(page.getByRole('figure')).not.toBeInTheDocument();
	await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
	await expect.element(page.getByText('기록 없음', { exact: true })).not.toBeInTheDocument();
});

it('설명 옆 결측 상태 요소는 제거하고 날짜별 접근성 설명은 유지한다', async () => {
	render(HealthSummaryCard, {
		summary: {
			...healthSummaries[2],
			values: [null, 376],
			dates: ['10.19', '10.20'],
			days: [
				{ date: '2026-10-19', value: null, missing: true, reason: 'UNSYNCED', isInProgress: false },
				{ date: '2026-10-20', value: 376, missing: false, reason: null, isInProgress: false }
			]
		}
	});
	await expect.element(page.getByRole('link')).not.toHaveTextContent('동기화 대기');
	await expect.element(page.getByRole('link')).toHaveTextContent(healthSummaries[2].description);
	await expect.element(page.getByRole('figure')).toHaveTextContent('10.19: 동기화 대기');
});

it('오늘의 실제 심박 기록과 평균 결측은 유지하고 기록 일수 안내만 제거한다', async () => {
	render(HealthSummaryCard, {
		summary: {
			...healthSummaries[3],
			label: '일별 심박수',
			value: '집계된 평균 없음',
			average: null,
			values: [74.2],
			dates: ['10.26'],
			recordedDays: 0,
			completedDays: 0,
			days: [{ date: '2026-10-26', value: 74.2, missing: false, reason: null, isInProgress: true }]
		}
	});
	await expect.element(page.getByText('평균에 반영된 기록 0일')).not.toBeInTheDocument();
	await expect.element(page.getByText('집계된 평균 없음', { exact: true })).toBeVisible();
	await expect
		.element(page.getByRole('button', { name: '2026-10-26 일별 평균 심박수 74.2 bpm' }))
		.toBeVisible();
	await expect
		.element(page.getByText('오늘 기록은 기간 평균에서 제외해요.'))
		.not.toBeInTheDocument();
	await expect.element(page.getByText('평균선', { exact: true })).not.toBeInTheDocument();
});

it('오류 상태에는 가짜 차트 대신 해당 지표의 재시도를 제공한다', async () => {
	const onRetry = vi.fn();
	render(HealthSummaryCard, { label: '수면', error: '수면 데이터를 준비 중입니다.', onRetry });
	await expect.element(page.getByRole('alert')).toHaveTextContent('수면 데이터를 준비 중입니다.');
	await expect.element(page.getByRole('figure')).not.toBeInTheDocument();
	await page.getByRole('button', { name: '수면 다시 시도' }).click();
	expect(onRetry).toHaveBeenCalledOnce();
});

it('준비 상태는 장애 경고나 가짜 차트 없이 표시하며 해당 지표를 다시 확인한다', async () => {
	const onRetry = vi.fn();
	render(HealthSummaryCard, {
		label: '걸음 수',
		preparing: true,
		error: '서버 준비 상태',
		onRetry
	});
	await expect.element(page.getByRole('status')).toHaveTextContent('건강 기록을 준비 중이에요.');
	await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
	await expect.element(page.getByRole('figure')).not.toBeInTheDocument();
	await expect.element(page.getByRole('link')).not.toBeInTheDocument();
	await expect.element(page.getByText('기록 없음', { exact: true })).not.toBeInTheDocument();
	await page.getByRole('button', { name: '걸음 수 다시 확인' }).click();
	expect(onRetry).toHaveBeenCalledOnce();
});
