import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tick } from 'svelte';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import type {
	afterNavigate,
	beforeNavigate,
	goto,
	AfterNavigate,
	BeforeNavigate,
	NavigationTarget,
	NavigationType
} from '$app/navigation';
import { authSession } from '#lib/features/auth/session.svelte.js';
import ErrorPage from './+error.svelte';
import './layout.css';

const appState = vi.hoisted(() => ({
	page: {
		status: 500,
		error: { message: '요청을 처리할 수 없어요.' },
		url: new URL('http://localhost/error')
	},
	navigating: { type: null as NavigationType | null }
}));
vi.mock('$app/state', () => appState);

const navigation = vi.hoisted(() => ({
	afterNavigate: vi.fn<typeof afterNavigate>(),
	beforeNavigate: vi.fn<typeof beforeNavigate>(),
	goto: vi.fn<typeof goto>()
}));
vi.mock('$app/navigation', () => navigation);

beforeEach(() => {
	appState.page.status = 500;
	appState.page.url = new URL('http://localhost/error');
	appState.navigating.type = null;
	navigation.afterNavigate.mockReset();
	navigation.beforeNavigate.mockReset();
	navigation.goto.mockReset().mockResolvedValue(undefined);
	vi.spyOn(authSession, 'restore').mockResolvedValue('no-session');
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

function backButton() {
	const button = page.getByRole('button', { name: '이전 페이지', exact: true }).element();
	if (!(button instanceof HTMLButtonElement)) throw new Error('이전 페이지 버튼이 없습니다.');
	return button;
}

function target(url: string): NavigationTarget {
	return {
		params: {},
		route: { id: '/(app)/health' },
		url: new URL(url),
		scroll: null
	};
}

function finishNavigation(from = 'http://localhost/health') {
	const callback = navigation.afterNavigate.mock.calls.at(-1)?.[0];
	if (!callback) throw new Error('afterNavigate가 등록되지 않았습니다.');
	const event: AfterNavigate = {
		type: 'enter',
		from: target(from),
		to: null,
		shallow: false,
		willUnload: false,
		complete: Promise.resolve()
	};
	callback(event);
}

function startNavigation(complete: Promise<void>) {
	const callback = navigation.beforeNavigate.mock.calls.at(-1)?.[0];
	if (!callback) throw new Error('beforeNavigate가 등록되지 않았습니다.');
	const event: BeforeNavigate = {
		type: 'popstate',
		delta: -1,
		event: new PopStateEvent('popstate'),
		from: null,
		to: target('http://localhost/health'),
		shallow: false,
		willUnload: false,
		complete,
		cancel: vi.fn()
	};
	callback(event);
}

function mountBack(previousUrl = 'http://localhost/health', entryUrl = previousUrl) {
	appState.page.status = 404;
	vi.stubGlobal('navigation', {
		currentEntry: { index: 1 },
		entries: () => [{ url: entryUrl }, { url: appState.page.url.href }]
	});
	const back = vi.spyOn(window.history, 'back').mockImplementation(() => {});
	const view = render(ErrorPage);
	finishNavigation(previousUrl);
	return { back, view };
}

describe('루트 오류 화면', () => {
	it.each([401, 403, 500])('%i 오류를 404 화면으로 바꾸지 않는다', async (status) => {
		appState.page.status = status;
		render(ErrorPage);
		await expect.element(page.getByRole('heading', { name: '오류가 발생했어요' })).toBeVisible();
		await expect.element(page.getByText(String(status), { exact: true })).toBeVisible();
		await expect.element(page.getByText('요청을 처리할 수 없어요.')).toBeVisible();
		await expect.element(page.getByText('PAGE NOT FOUND')).not.toBeInTheDocument();
		// 404 전용 CSS가 일반 오류의 본문과 복구 동작을 축소하지 않아야 한다.
		expect(
			parseFloat(getComputedStyle(page.getByText('요청을 처리할 수 없어요.').element()).fontSize)
		).toBeGreaterThanOrEqual(16);
		for (const control of [
			page.getByRole('link', { name: '홈으로 가기', exact: true }),
			page.getByRole('button', { name: '이전 페이지', exact: true })
		]) {
			expect(control.element().getBoundingClientRect().height).toBeGreaterThanOrEqual(60);
		}
	});
});

describe('이전 페이지의 중복 이동 방지', () => {
	it('한 번 클릭은 한 번만 이동하고 실제 탐색 완료 뒤 다시 이동할 수 있다', async () => {
		const { back, view } = mountBack();
		const complete = Promise.withResolvers<void>();
		backButton().click();
		expect(back).toHaveBeenCalledTimes(1);
		startNavigation(complete.promise);
		backButton().click();
		expect(back).toHaveBeenCalledTimes(1);
		complete.resolve();
		await complete.promise;
		backButton().click();
		expect(back).toHaveBeenCalledTimes(2);
		expect(navigation.goto).not.toHaveBeenCalled();

		// 오류 화면을 다시 마운트해도 이전 화면의 진행 상태를 가져오지 않는다.
		await view.unmount();
		render(ErrorPage);
		finishNavigation();
		backButton().click();
		expect(back).toHaveBeenCalledTimes(3);
	});

	it('같은 이벤트의 세 클릭은 한 번만 이동하고 취소·실패한 탐색 뒤 재시도한다', async () => {
		const { back } = mountBack();
		const complete = Promise.withResolvers<void>();
		for (let index = 0; index < 3; index++) backButton().click();
		expect(back).toHaveBeenCalledTimes(1);
		startNavigation(complete.promise);
		backButton().click();
		expect(back).toHaveBeenCalledTimes(1);
		complete.reject(new Error('navigation cancelled'));
		await complete.promise.catch(() => {});
		backButton().click();
		expect(back).toHaveBeenCalledTimes(2);
		expect(navigation.goto).not.toHaveBeenCalled();
	});

	it.each(['{Enter}', ' '])(
		'%j 키의 반복 입력은 포커스를 유지하며 한 번만 이동한다',
		async (key) => {
			const { back } = mountBack();
			const button = backButton();
			button.focus();
			await userEvent.keyboard(key);
			await userEvent.keyboard(key);
			expect(back).toHaveBeenCalledTimes(1);
			expect(navigation.goto).not.toHaveBeenCalled();
			expect(document.activeElement).toBe(button);
			expect(button.disabled).toBe(false);
			expect(button.getAttribute('aria-busy')).toBe('true');
		}
	);

	it('다른 앱 탐색이 진행 중이면 이동하지 않고 종료 뒤 첫 입력을 받는다', () => {
		const { back } = mountBack();
		appState.navigating.type = 'link';
		backButton().click();
		expect(back).not.toHaveBeenCalled();
		expect(navigation.goto).not.toHaveBeenCalled();
		appState.navigating.type = null;
		backButton().click();
		expect(back).toHaveBeenCalledTimes(1);
	});

	it('history.back이 동기 오류를 던져도 다음 클릭을 받을 수 있다', () => {
		const { back } = mountBack();
		back.mockImplementationOnce(() => {
			throw new Error('synthetic history failure');
		});
		backButton().click();
		backButton().click();
		expect(back).toHaveBeenCalledTimes(2);
		expect(navigation.goto).not.toHaveBeenCalled();
	});

	it.each(['완료', '거절'])(
		'홈 이동이 %s될 때까지 세 클릭을 하나로 묶고 다시 시도한다',
		async (outcome) => {
			const { back } = mountBack();
			vi.stubGlobal('navigation', undefined);
			const complete = Promise.withResolvers<void>();
			navigation.goto.mockReturnValueOnce(complete.promise);
			for (let index = 0; index < 3; index++) backButton().click();
			expect(navigation.goto).toHaveBeenCalledExactlyOnceWith('/home');
			expect(back).not.toHaveBeenCalled();
			if (outcome === '완료') complete.resolve();
			else complete.reject(new Error('synthetic goto rejection'));
			await complete.promise.catch(() => {});
			backButton().click();
			expect(navigation.goto).toHaveBeenCalledTimes(2);
			expect(back).not.toHaveBeenCalled();
		}
	);

	it('홈 이동이 동기 오류를 던져도 다음 클릭을 받을 수 있다', () => {
		const { back } = mountBack();
		vi.stubGlobal('navigation', undefined);
		navigation.goto.mockImplementationOnce(() => {
			throw new Error('synthetic goto failure');
		});
		backButton().click();
		backButton().click();
		expect(navigation.goto).toHaveBeenCalledTimes(2);
		expect(back).not.toHaveBeenCalled();
	});

	it('이전 이동의 늦은 완료가 새 이동의 잠금을 해제하지 않는다', async () => {
		const { back } = mountBack();
		vi.stubGlobal('navigation', undefined);
		const old = Promise.withResolvers<void>();
		const current = Promise.withResolvers<void>();
		navigation.goto.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
		backButton().click();
		finishNavigation();
		backButton().click();
		expect(navigation.goto).toHaveBeenCalledTimes(2);
		old.resolve();
		await old.promise;
		backButton().click();
		expect(navigation.goto).toHaveBeenCalledTimes(2);
		current.resolve();
		await current.promise;
		backButton().click();
		expect(navigation.goto).toHaveBeenCalledTimes(3);
		expect(back).not.toHaveBeenCalled();
	});

	it('일반 pageshow는 중복 입력을 막고 BFCache 복원은 다시 입력을 받는다', async () => {
		const { back } = mountBack();
		backButton().click();
		window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false }));
		backButton().click();
		expect(back).toHaveBeenCalledTimes(1);
		window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
		await tick();
		backButton().click();
		expect(back).toHaveBeenCalledTimes(2);
		expect(navigation.goto).not.toHaveBeenCalled();
	});

	it('외부·같은 경로·이전 항목 불일치는 history.back 대신 홈으로 한 번 이동한다', async () => {
		for (const [previousUrl, entryUrl] of [
			['https://external.test/start', 'https://external.test/start'],
			['http://localhost/error?before=1', 'http://localhost/error?before=1'],
			['http://localhost/health', 'http://localhost/home']
		]) {
			const { back, view } = mountBack(previousUrl, entryUrl);
			const complete = Promise.withResolvers<void>();
			navigation.goto.mockReturnValueOnce(complete.promise);
			for (let index = 0; index < 3; index++) backButton().click();
			expect(navigation.goto).toHaveBeenCalledExactlyOnceWith('/home');
			expect(back).not.toHaveBeenCalled();
			await view.unmount();
			navigation.goto.mockClear();
			back.mockRestore();
		}
	});
});
