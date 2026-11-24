import { expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import HealthDataPreparationNotice from './HealthDataPreparationNotice.svelte';

it.each([false, true])(
	'연동 상태를 모르면 첫 기록 대기 값이 %s여도 연동 완료를 단정하지 않는다',
	async (waitingForFirstRecord) => {
		render(HealthDataPreparationNotice, { waitingForFirstRecord, onRecheck: vi.fn() });
		await expect
			.element(page.getByRole('heading', { level: 2, name: '건강 기록을 준비하고 있어요' }))
			.toBeVisible();
		await expect
			.element(page.getByRole('status'))
			.toHaveTextContent('건강 기록을 화면에 표시할 준비를 하고 있어요.');
		await expect.element(page.getByRole('status')).toHaveAttribute('aria-live', 'polite');
		await expect
			.element(page.getByText('잠시 후 다시 확인해 주세요.', { exact: true }))
			.toBeVisible();
		await expect.element(page.getByText(/앱 연동.*완료/)).not.toBeInTheDocument();
		await expect.element(page.getByText(/첫 건강데이터/)).not.toBeInTheDocument();
		await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
	}
);

it('확인된 앱 연동 완료와 첫 건강데이터 대기를 서로 구분해 알린다', async () => {
	render(HealthDataPreparationNotice, {
		connected: true,
		waitingForFirstRecord: true,
		onRecheck: vi.fn()
	});
	await expect
		.element(page.getByRole('heading', { level: 2, name: '앱 연동을 완료했어요' }))
		.toBeVisible();
	await expect
		.element(page.getByText('첫 건강데이터가 도착하기를 기다리고 있어요.', { exact: true }))
		.toBeVisible();
	await expect
		.element(
			page.getByText('앱에서 건강 기록 수집 상태를 확인하고 잠시 후 다시 확인해 주세요.', {
				exact: true
			})
		)
		.toBeVisible();
	await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
	await expect.element(page.getByRole('figure')).not.toBeInTheDocument();
});

it('연동 후 집계 대기는 첫 기록 도착 대기로 단정하지 않는다', async () => {
	render(HealthDataPreparationNotice, { connected: true, onRecheck: vi.fn() });
	await expect
		.element(page.getByRole('heading', { level: 2, name: '건강 기록을 준비하고 있어요' }))
		.toBeVisible();
	await expect
		.element(
			page.getByText('앱 연동은 완료됐어요. 건강 기록을 화면에 표시할 준비를 하고 있어요.', {
				exact: true
			})
		)
		.toBeVisible();
	await expect.element(page.getByText(/첫 건강데이터/)).not.toBeInTheDocument();
});

it('재확인 버튼은 Enter와 Space로 실제 재확인 동작을 실행한다', async () => {
	const onRecheck = vi.fn();
	render(HealthDataPreparationNotice, { onRecheck });
	const button = page.getByRole('button', { name: '건강데이터 다시 확인' });
	button.element().focus();
	await vi.waitFor(() => expect(document.activeElement).toBe(button.element()));
	await userEvent.keyboard('{Enter}');
	expect(onRecheck).toHaveBeenCalledTimes(1);
	await userEvent.keyboard(' ');
	expect(onRecheck).toHaveBeenCalledTimes(2);
});

it('재확인 중에는 진행 상태를 알리고 추가 활성화를 차단한다', async () => {
	const onRecheck = vi.fn();
	render(HealthDataPreparationNotice, { rechecking: true, onRecheck });
	const button = page.getByRole('button', { name: '건강데이터 다시 확인' });
	await expect.element(button).toBeDisabled();
	await expect.element(button).toHaveAttribute('aria-busy', 'true');
	await expect.element(button).toHaveTextContent('확인하는 중');
	const element = button.element();
	if (!(element instanceof HTMLButtonElement)) throw new Error('재확인 버튼이 없습니다.');
	element.click();
	element.click();
	element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	expect(onRecheck).not.toHaveBeenCalled();
});
