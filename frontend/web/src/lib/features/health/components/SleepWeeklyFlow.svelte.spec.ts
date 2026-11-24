import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import SleepWeeklyFlow from './SleepWeeklyFlow.svelte';
import { completedSleepResult } from '../sleep-mock';

it.each([
	[54, '+54분 증가', 'text-text-primary'],
	[-54, '-54분 감소', 'text-text-primary'],
	[0, '변화 없음', 'text-text-primary'],
	[null, '비교할 기록 없음', 'text-text-primary']
] as const)('증감 %s의 문구를 유지하고 검정 텍스트로 표시한다', async (delta, text, token) => {
	const result = structuredClone(completedSleepResult);
	result.summary.avg = 400;
	const previous = structuredClone(result);
	previous.summary.avg = delta === null ? null : 400 - delta;
	render(SleepWeeklyFlow, { result, previous });
	await expect.element(page.getByText(text, { exact: true })).toHaveClass(token);
});

it('비교 조회 실패는 검정 텍스트이고 날짜와 막대 링크는 같은 상세 이동 안내를 사용한다', async () => {
	render(SleepWeeklyFlow, { result: completedSleepResult, previous: null });
	await expect.element(page.getByText('이전 기간 조회 실패')).toHaveClass('text-text-primary');
	const date = completedSleepResult.detail.nights[0].date;
	for (const name of [`${date} 수면 상세`, `${date} 수면 그래프 상세`]) {
		const link = page.getByRole('link', { name, exact: true });
		expect(link.element().getAttribute('href')).toBe(`/health/sleep/${date}`);
		link.element().focus();
		await expect.element(page.getByRole('tooltip')).toHaveTextContent('클릭하여 수면 상세 보기');
		expect(document.querySelectorAll('[role="tooltip"]')).toHaveLength(1);
		link.element().blur();
	}
});
