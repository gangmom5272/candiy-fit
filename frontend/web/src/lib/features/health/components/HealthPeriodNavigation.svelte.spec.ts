import { page } from 'vitest/browser';
import { expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import HealthPeriodNavigation from './HealthPeriodNavigation.svelte';
import DayDatePicker from './DayDatePicker.svelte';
import '../../../../routes/layout.css';

it('선택 콜백이 없으면 기존 기간 표시와 화살표를 유지한다', async () => {
	const previous = vi.fn();
	render(HealthPeriodNavigation, {
		periodLabel: '2026.10.20 ~ 10.26',
		note: '기간 안내',
		onPrevious: previous,
		onNext: vi.fn(),
		nextDisabled: true
	});
	await expect.element(page.getByRole('status')).toHaveTextContent('2026.10.20 ~ 10.26');
	await page.getByRole('button', { name: '이전 주', exact: true }).click();
	expect(previous).toHaveBeenCalledOnce();
	await expect.element(page.getByRole('button', { name: '다음 주', exact: true })).toBeDisabled();
});

it('기간 캘린더는 미래 날짜를 막고 선택 후 닫으며 상세로 이동하지 않는다', async () => {
	const select = vi.fn();
	render(HealthPeriodNavigation, {
		periodLabel: '2026.10.20 ~ 10.26',
		note: '기간 안내',
		date: '2026-10-26',
		maxDate: '2026-10-26',
		onDateSelect: select,
		onPrevious: vi.fn(),
		onNext: vi.fn()
	});
	const trigger = page.getByRole('button', { name: /^날짜 선택,/ });
	await expect.element(trigger).toHaveStyle({ color: 'rgb(44, 46, 53)' });
	await trigger.click();
	await expect.element(page.getByRole('dialog')).toBeVisible();
	await expect
		.element(page.getByRole('button', { name: '2026-10-27', exact: true }))
		.toBeDisabled();
	await page.getByRole('button', { name: '2026-10-20', exact: true }).click();
	expect(select).toHaveBeenCalledExactlyOnceWith('2026-10-20');
	await expect.element(page.getByRole('dialog', { includeHidden: true })).not.toBeVisible();
});

it('기존 하루 상세 캘린더의 날짜 링크는 그대로 유지한다', async () => {
	render(DayDatePicker, { date: '2026-10-26', maxDate: '2026-10-26' });
	await expect
		.element(page.getByRole('button', { name: /^날짜 선택,/ }))
		.toHaveStyle({ color: 'rgb(109, 114, 128)' });
	await page.getByRole('button', { name: /^날짜 선택,/ }).click();
	await expect
		.element(page.getByRole('link', { name: '2026-10-20', exact: true }))
		.toHaveAttribute('href', '/health/day/2026-10-20');
});
