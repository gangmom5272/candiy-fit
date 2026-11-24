import { afterEach, expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import OfflineBanner from './OfflineBanner.svelte';

afterEach(() => vi.restoreAllMocks());

it('온라인에서는 비어 있고 오프라인과 복구 이벤트에만 안내를 변경한다', async () => {
	const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
	render(OfflineBanner);
	const banner = page.getByRole('status', { name: '네트워크 연결 상태' });
	await expect.element(banner).not.toBeInTheDocument();

	online.mockReturnValue(false);
	window.dispatchEvent(new Event('offline'));
	await expect.element(banner).toHaveTextContent('인터넷 연결이 끊겼어요');
	await expect.element(banner).toHaveTextContent('네트워크 연결을 확인해 주세요.');
	await expect.element(banner).toHaveAttribute('aria-live', 'polite');
	window.dispatchEvent(new Event('offline'));
	await expect.element(page.getByText('인터넷 연결이 끊겼어요')).toBeVisible();

	online.mockReturnValue(true);
	window.dispatchEvent(new Event('online'));
	await expect.element(banner).not.toBeInTheDocument();
	await expect.element(page.getByLabelText('네트워크 연결 상태')).not.toBeInTheDocument();
});

it('이미 오프라인이면 즉시 표시하고 마운트마다 리스너 한 쌍을 등록하고 해제한다', async () => {
	vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
	const add = vi.spyOn(window, 'addEventListener');
	const remove = vi.spyOn(window, 'removeEventListener');

	for (let mount = 0; mount < 2; mount++) {
		add.mockClear();
		remove.mockClear();
		const { unmount } = await render(OfflineBanner);
		await expect.element(page.getByRole('status', { name: '네트워크 연결 상태' })).toBeVisible();
		const registrations = add.mock.calls.filter(
			([event]) => event === 'online' || event === 'offline'
		);
		expect(registrations.map(([event]) => event).sort()).toEqual(['offline', 'online']);
		await unmount();
		for (const [event, listener] of registrations) {
			expect(
				remove.mock.calls.some(
					([removedEvent, removedListener]) =>
						removedEvent === event && removedListener === listener
				)
			).toBe(true);
		}
	}
});
