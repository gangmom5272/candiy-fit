import { page } from 'vitest/browser';
import { expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import AppHeader from './AppHeader.svelte';

const appState = vi.hoisted(() => ({ page: { url: new URL('http://localhost/home') } }));
vi.mock('$app/state', () => appState);

it.each([
	['/home', '홈', 'page'],
	['/health', '건강 데이터', 'page'],
	['/health/activity', '건강 데이터', 'true'],
	['/health/sleep/2026-10-23', '건강 데이터', 'true'],
	['/coaching', 'AI 코치', 'page'],
	['/coaching/c0000000-0000-4000-8000-000000000001', 'AI 코치', 'true'],
	['/my', null, undefined],
	['/healthcare', null, undefined],
	['/coaching-other', null, undefined]
])('%s에서 해당 메뉴만 활성화한다', async (pathname, activeName, current) => {
	appState.page.url = new URL(pathname, 'http://localhost');
	render(AppHeader);
	const navigation = page.getByRole('navigation', { name: '주 메뉴' });
	for (const name of ['홈', '건강 데이터', 'AI 코치']) {
		const link = navigation.getByRole('link', { name, exact: true });
		if (name === activeName) {
			await expect.element(link).toHaveAttribute('aria-current', current);
		} else {
			await expect.element(link).not.toHaveAttribute('aria-current');
		}
	}
});
