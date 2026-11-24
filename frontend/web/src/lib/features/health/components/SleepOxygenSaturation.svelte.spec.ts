import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import '../../../../routes/layout.css';
import SleepOxygenSaturation from './SleepOxygenSaturation.svelte';
import { completedOxygenResult } from '../sleep-mock';

function fixture() {
	const result = structuredClone(completedOxygenResult);
	result.period = { start: '2026-10-07', end: '2026-10-11' };
	result.days = [
		{ date: '2026-10-07', value: 96, missing: false, reason: null, isInProgress: false },
		{ date: '2026-10-08', value: null, missing: true, reason: 'NULL_VALUE', isInProgress: false },
		{ date: '2026-10-09', value: 98, missing: true, reason: 'NO_RECORD', isInProgress: false },
		{ date: '2026-10-10', value: 97, missing: false, reason: null, isInProgress: false },
		{ date: '2026-10-11', value: 95, missing: false, reason: null, isInProgress: false }
	];
	result.detail.measuredMin = { date: '2026-10-07', value: 80 };
	result.detail.measuredMax = { date: '2026-10-10', value: 100 };
	// 배열 순서가 아닌 날짜로 매칭하고 기간 전체 측정값과 구분한다.
	result.detail.dailyRanges = [
		{ date: '2026-10-10', min: null, max: 99 },
		{ date: '2026-10-07', min: 94, max: 97 }
	];
	return result;
}

it('hover로 같은 날짜의 평균·최저·최고를 표시하고 기간 전체 값과 구분한다', async () => {
	render(SleepOxygenSaturation, { result: fixture() });
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
	await page.getByRole('button', { name: '10월 7일 산소포화도 상세' }).hover();
	const tooltip = page.getByRole('tooltip');
	await expect.element(tooltip).toHaveTextContent('10월 7일');
	await expect.element(tooltip).toHaveTextContent('평균 산소포화도 96%');
	await expect.element(tooltip).toHaveTextContent('최저 산소포화도 94%');
	await expect.element(tooltip).toHaveTextContent('최고 산소포화도 97%');
	await expect.element(tooltip).not.toHaveTextContent('80%');
	await expect.element(tooltip).not.toHaveTextContent('100%');
});

it('focus와 클릭으로 선택을 바꾸고 범위 null이나 누락을 기록 없음으로 표시한다', async () => {
	render(SleepOxygenSaturation, { result: fixture() });
	page.getByRole('button', { name: '10월 7일 산소포화도 상세' }).element().focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('10월 7일');
	await page.getByRole('button', { name: '10월 10일 산소포화도 상세' }).click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('평균 산소포화도 97%');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('최저 산소포화도 기록 없음');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('최고 산소포화도 99%');
	await page.getByRole('button', { name: '10월 11일 산소포화도 상세' }).click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('최저 산소포화도 기록 없음');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('최고 산소포화도 기록 없음');
});

it('결측 날짜에는 버튼이나 가짜 0%를 만들지 않는다', () => {
	const { container } = render(SleepOxygenSaturation, { result: fixture() });
	expect(container.querySelectorAll('button')).toHaveLength(3);
	expect(container.querySelectorAll('[data-oxygen-point]')).toHaveLength(3);
	expect(container.querySelector('[aria-label="10월 8일 산소포화도 상세"]')).toBeNull();
	expect(container.querySelector('[aria-label="10월 9일 산소포화도 상세"]')).toBeNull();
});

it('기간이 바뀌면 이전 날짜의 툴팁을 표시하지 않는다', async () => {
	const { rerender } = render(SleepOxygenSaturation, { result: fixture() });
	await page.getByRole('button', { name: '10월 7일 산소포화도 상세' }).click();
	await expect.element(page.getByRole('tooltip')).toBeVisible();
	await rerender({ result: completedOxygenResult });
	await expect.element(page.getByRole('tooltip')).not.toBeInTheDocument();
});

it('7px 정원과 32px 조작 영역의 중심이 일치한다', async () => {
	const { container } = render(SleepOxygenSaturation, { result: fixture() });
	await expect
		.poll(() => container.querySelector('[data-oxygen-point]')?.getBoundingClientRect().width)
		.toBe(7);
	for (const point of container.querySelectorAll<HTMLElement>('[data-oxygen-point]')) {
		const button = point.nextElementSibling!;
		const p = point.getBoundingClientRect(),
			b = button.getBoundingClientRect();
		expect(p.height).toBe(7);
		expect(b.width).toBe(32);
		expect(b.height).toBe(32);
		expect(p.x + p.width / 2).toBeCloseTo(b.x + b.width / 2, 1);
		expect(p.y + p.height / 2).toBeCloseTo(b.y + b.height / 2, 1);
	}
});
