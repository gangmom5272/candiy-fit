import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { tick } from 'svelte';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import { buttonVariants } from '#lib/components/ui/button/index.js';
import AppConnectionExample from '#lib/testing/AppConnectionExample.svelte';
import { e2eUser } from '#lib/testing/e2e-auth.js';
import { authSession } from '#lib/features/auth/session.svelte.js';
import type { User } from '#lib/features/auth/types.js';
import type { AppConnectionStatus } from '../app-connection-api';
import '../../../../routes/layout.css';

vi.mock('$app/env/public', () => ({ PUBLIC_APP_CONNECTION_GUIDE: false }));
vi.mock('$app/state', () => ({ page: { url: new URL('http://localhost/home') } }));

const first: User = { ...e2eUser, socialType: 'GOOGLE', gender: 'FEMALE' };
const second: User = {
	...first,
	id: '00000000-0000-4000-8000-000000000002',
	endUserId: '00000000-0000-4000-8000-000000000002',
	displayName: '합성 두 번째 계정'
};
const cleanups: (() => void | Promise<void>)[] = [];
type Call = { route: 'status' | 'sync' | 'record'; owner?: string; signal?: AbortSignal };

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((yes) => (resolve = yes));
	return { promise, resolve };
}

function statusResponse(status: AppConnectionStatus): Response {
	return Response.json({
		data: {
			appConnection: {
				status,
				connectedAt: status === 'CONNECTED' ? '2026-11-11T07:00:00Z' : null
			},
			firstRecord: { status: 'UNCONFIRMED', firstArrivedAt: null },
			metrics: [{ metric: 'steps', ready: null }]
		}
	});
}

function failure(status: number, code: string): Response {
	return Response.json({ error: { code, message: '합성 응답 실패' } }, { status });
}

function mockApi(
	status: (call: Call, number: number) => Response | Promise<Response>,
	syncStatus = 'SYNCED'
) {
	const calls: Call[] = [];
	vi.stubGlobal(
		'fetch',
		vi.fn<typeof fetch>(async (input, options) => {
			const url = new URL(String(input), location.origin);
			const route = url.pathname.endsWith('/health/data-status')
				? 'status'
				: url.pathname.endsWith('/health/sync')
					? 'sync'
					: url.pathname.endsWith('/testing/health-record')
						? 'record'
						: undefined;
			if (!route) throw new Error(`예상하지 않은 요청: ${url.pathname}`);
			const call: Call = {
				route,
				owner: authSession.user?.id,
				signal: options?.signal ?? undefined
			};
			calls.push(call);
			if (route === 'status')
				return status(call, calls.filter((item) => item.route === route).length);
			if (route === 'sync') return Response.json({ data: { status: syncStatus } });
			return Response.json({ data: { owner: call.owner } });
		})
	);
	return calls;
}

function mount(props: { enabled?: boolean; protectedRoute?: boolean; withSync?: boolean } = {}) {
	const view = render(AppConnectionExample, { first, second, ...props });
	cleanups.push(async () => {
		await view.unmount();
	});
	return view;
}

const child = () => page.getByRole('region', { name: '기존 화면', exact: true });
const guide = () =>
	page.getByRole('heading', { name: '건강 기록을 보려면 먼저 앱 연동이 필요해요' });
const state = () => page.getByLabelText('연동 상태', { exact: true });
const routes = (calls: Call[]) => calls.map((call) => call.route);
const retryNotice = '연결이 원활하지 않아 앱 연동 상태를 다시 확인하고 있어요.';
const checkingNotice = '앱 연동 상태를 확인하고 있어요.';
const closeNotice = () => page.getByRole('button', { name: '앱 연동 상태 안내 닫기', exact: true });
let previousDocumentTitle: string;

beforeEach(() => {
	previousDocumentTitle = document.title;
});

function clickWithoutAdvancingClock(element: Element) {
	if (!(element instanceof HTMLButtonElement)) throw new Error('실제 button 요소가 필요합니다.');
	element.click();
}

afterEach(async () => {
	for (const cleanup of cleanups.splice(0).reverse()) await cleanup();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
	vi.useRealTimers();
	document.title = previousDocumentTitle;
});

it('NOT_CONNECTED는 안내를 표시하고 동기화·건강 조회 자식을 만들지 않는다', async () => {
	const calls = mockApi(() => statusResponse('NOT_CONNECTED'));
	mount({ enabled: true });
	await expect.element(guide()).toBeVisible();
	expect(routes(calls)).toEqual(['status']);
	await expect.element(child()).not.toBeInTheDocument();
	await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('0');
});

it.each(['CONNECTED', 'UNKNOWN'] as const)(
	'%s는 연동 GET → 기존 POST → 기존 건강 GET 순서를 유지한다',
	async (status) => {
		const calls = mockApi(() => statusResponse(status));
		mount({ enabled: true });
		await expect.element(child()).toHaveTextContent('기록 표시');
		expect(routes(calls)).toEqual(['status', 'sync', 'record']);
		await expect.element(guide()).not.toBeInTheDocument();
		if (status === 'UNKNOWN')
			await expect
				.element(
					page.getByText(
						'앱 연동 완료 이력은 아직 확인되지 않았어요. 기존 화면은 계속 이용할 수 있어요. 앱에서 연동 상태를 확인할 수 있어요.'
					)
				)
				.toBeVisible();
	}
);

it('최초 상태 응답을 기다리는 동안에는 POST와 건강 자식 생성이 없다', async () => {
	const held = deferred<Response>();
	const calls = mockApi(() => held.promise);
	mount({ enabled: true });
	await vi.waitFor(() => expect(routes(calls)).toEqual(['status']));
	await expect.element(page.getByText('앱 연동 상태를 확인하고 있어요.')).toBeVisible();
	await expect.element(child()).not.toBeInTheDocument();
	held.resolve(statusResponse('CONNECTED'));
	await expect.element(child()).toHaveTextContent('기록 표시');
	expect(routes(calls)).toEqual(['status', 'sync', 'record']);
});

it.each(['503', 'invalid', 'network'] as const)(
	'최초 %s 오류를 미연동으로 위장하지 않고 오류·재시도를 제공한다',
	async (kind) => {
		const calls = mockApi(() => {
			if (kind === 'network') throw new TypeError('offline');
			return kind === '503' ? failure(503, 'HEALTH_DATA_UNAVAILABLE') : Response.json({ data: {} });
		});
		mount({ enabled: true });
		await expect
			.element(page.getByRole('heading', { name: '앱 연동 상태를 확인하지 못했어요' }))
			.toBeVisible();
		await expect
			.element(page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }))
			.toBeEnabled();
		await expect.element(guide()).not.toBeInTheDocument();
		await expect.element(child()).not.toBeInTheDocument();
		expect(routes(calls)).toEqual(kind === 'invalid' ? ['status'] : ['status', 'status']);
		expect(authSession.user?.id).toBe(first.id);
	}
);

it.each(['AUTH_REQUIRED', 'AUTH_EXPIRED'])(
	'현재 계정의 401 %s는 로컬 인증만 정리하고 건강 자식을 실행하지 않는다',
	async (code) => {
		const calls = mockApi(() => failure(401, code));
		mount({ enabled: true });
		await expect.element(page.getByText('하네스 로그아웃 상태')).toBeVisible();
		expect(authSession.status).toBe('unauthenticated');
		expect(authSession.user).toBeNull();
		expect(routes(calls)).toEqual(['status']);
	}
);

it.each([
	['CONNECTED', 'UNKNOWN'],
	['UNKNOWN', 'CONNECTED']
] as const)('%s → %s 배경 재조회는 자식 DOM과 기존 POST 횟수를 유지한다', async (from, to) => {
	const held = deferred<Response>();
	const calls = mockApi((_, number) => (number === 1 ? statusResponse(from) : held.promise));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	await page.getByRole('button', { name: '하네스 재조회' }).click();
	await vi.waitFor(() => expect(calls.filter((call) => call.route === 'status')).toHaveLength(2));
	expect(child().element()).toBe(original);
	held.resolve(statusResponse(to));
	await expect.element(state()).toHaveTextContent(to);
	expect(child().element()).toBe(original);
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status']);
	await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('1');
});

it('배경 503은 마지막 CONNECTED 화면을 유지하고 오류 안내를 표시한다', async () => {
	const calls = mockApi((_, number) =>
		number === 1 ? statusResponse('CONNECTED') : failure(503, 'HEALTH_DATA_UNAVAILABLE')
	);
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	await page.getByRole('button', { name: '하네스 재조회' }).click();
	await expect.element(page.getByRole('alert')).toHaveTextContent('마지막으로 확인한 상태');
	expect(child().element()).toBe(original);
	await expect.element(state()).toHaveTextContent('CONNECTED');
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status', 'status']);
});

it('NOT_CONNECTED에서 수동 재확인이 503이면 안내·마지막 상태·재시도를 유지한다', async () => {
	const calls = mockApi((_, number) =>
		number === 1 ? statusResponse('NOT_CONNECTED') : failure(503, 'HEALTH_DATA_UNAVAILABLE')
	);
	mount({ enabled: true });
	await expect.element(guide()).toBeVisible();
	await page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }).click();
	await expect.element(page.getByRole('alert')).toHaveTextContent('마지막으로 확인한 상태');
	await expect.element(guide()).toBeVisible();
	await expect.element(state()).toHaveTextContent('NOT_CONNECTED');
	await expect
		.element(page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }))
		.toBeEnabled();
	await expect.element(child()).not.toBeInTheDocument();
	await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('0');
	expect(routes(calls)).toEqual(['status', 'status', 'status']);
});

it.each(['manual', 'foreground'] as const)(
	'NOT_CONNECTED → CONNECTED %s 재확인은 한 번만 자식과 POST를 시작한다',
	async (trigger) => {
		const calls = mockApi((_, number) =>
			statusResponse(number === 1 ? 'NOT_CONNECTED' : 'CONNECTED')
		);
		mount({ enabled: true });
		await expect.element(guide()).toBeVisible();
		if (trigger === 'manual')
			await page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }).click();
		else window.dispatchEvent(new Event('focus'));
		await expect.element(child()).toHaveTextContent('기록 표시');
		expect(routes(calls)).toEqual(['status', 'status', 'sync', 'record']);
		await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('1');
	}
);

it('focus·visibility는 50ms로 합치며 숨은 탭과 진행 중 중복 조회를 건너뛴다', async () => {
	const held = deferred<Response>();
	const calls = mockApi((_, number) => (number === 1 ? statusResponse('CONNECTED') : held.promise));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	vi.useFakeTimers();
	const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
	window.dispatchEvent(new Event('focus'));
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(100);
	expect(calls.filter((call) => call.route === 'status')).toHaveLength(1);
	visibility.mockReturnValue('visible');
	window.dispatchEvent(new Event('focus'));
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(49);
	expect(calls.filter((call) => call.route === 'status')).toHaveLength(1);
	await vi.advanceTimersByTimeAsync(1);
	expect(calls.filter((call) => call.route === 'status')).toHaveLength(2);
	window.dispatchEvent(new Event('focus'));
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(50);
	expect(calls.filter((call) => call.route === 'status')).toHaveLength(2);
	held.resolve(statusResponse('CONNECTED'));
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	expect(calls.filter((call) => call.route === 'sync')).toHaveLength(1);
});

it('보이는 탭의 복귀 예약 후 50ms 전에 숨겨지면 추가 상태 GET을 보내지 않는다', async () => {
	const calls = mockApi(() => statusResponse('CONNECTED'));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	vi.useFakeTimers();
	const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
	window.dispatchEvent(new Event('focus'));
	await vi.advanceTimersByTimeAsync(49);
	expect(routes(calls)).toEqual(['status', 'sync', 'record']);
	visibility.mockReturnValue('hidden');
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(100);
	await tick();
	expect(routes(calls)).toEqual(['status', 'sync', 'record']);
	expect(child().element()).toBe(original);
	expect(state().element().textContent).toBe('CONNECTED');
});

it('탭 복귀 예약 뒤 수동 재확인이 먼저 끝나면 예약이 추가 GET을 만들지 않는다', async () => {
	const calls = mockApi((_, number) => statusResponse(number === 1 ? 'CONNECTED' : 'UNKNOWN'));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	vi.useFakeTimers();
	vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
	window.dispatchEvent(new Event('focus'));
	(page.getByRole('button', { name: '하네스 재조회' }).element() as HTMLButtonElement).click();
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	expect(calls.filter((call) => call.route === 'status')).toHaveLength(2);
	expect(state().element().textContent).toBe('UNKNOWN');
	await vi.advanceTimersByTimeAsync(50);
	await tick();
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status']);
});

it('동일 ID 프로필 객체 갱신은 Provider·캐시·자식 수명을 바꾸지 않는다', async () => {
	const calls = mockApi(() => statusResponse('CONNECTED'));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	const version = authSession.sessionVersion;
	await page.getByRole('button', { name: '프로필 객체 갱신' }).click();
	await tick();
	expect(authSession.user?.displayName).toBe('프로필 갱신');
	expect(authSession.sessionVersion).toBe(version);
	expect(child().element()).toBe(original);
	expect(routes(calls)).toEqual(['status', 'sync', 'record']);
});

it('같은 tick의 로그아웃·동일 ID 재로그인은 이전 NOT_CONNECTED 캐시를 버린다', async () => {
	const calls = mockApi((_, number) =>
		statusResponse(number === 1 ? 'NOT_CONNECTED' : 'CONNECTED')
	);
	mount({ enabled: true });
	await expect.element(guide()).toBeVisible();
	const version = authSession.sessionVersion;
	await page.getByRole('button', { name: '합성 같은 계정 새 세션' }).click();
	await expect.element(child()).toHaveTextContent('기록 표시');
	expect(authSession.user?.id).toBe(first.id);
	expect(authSession.sessionVersion).toBeGreaterThan(version);
	expect(routes(calls)).toEqual(['status', 'status', 'sync', 'record']);
});

for (const next of ['different-account', 'same-id-new-session'] as const) {
	it.each(['success', '401'] as const)(
		`${next}: 이전 요청의 늦은 %s가 새 세션을 오염시키지 않는다`,
		async (late) => {
			const held = deferred<Response>();
			let delivered = false;
			const calls = mockApi(async (_, number) => {
				if (number > 1) return statusResponse('CONNECTED');
				const response = await held.promise;
				delivered = true;
				return response;
			});
			mount({ enabled: true });
			await vi.waitFor(() => expect(calls).toHaveLength(1));
			const oldSignal = calls[0].signal;
			await page
				.getByRole('button', {
					name: next === 'different-account' ? '합성 계정 전환' : '합성 같은 계정 새 세션'
				})
				.click();
			await expect.element(child()).toHaveTextContent('기록 표시');
			expect(oldSignal?.aborted).toBe(true);
			const original = child().element();
			held.resolve(late === '401' ? failure(401, 'AUTH_EXPIRED') : statusResponse('NOT_CONNECTED'));
			await vi.waitFor(() => expect(delivered).toBe(true));
			await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
			expect(authSession.status).toBe('authenticated');
			expect(authSession.user?.id).toBe(next === 'different-account' ? second.id : first.id);
			expect(child().element()).toBe(original);
			await expect.element(state()).toHaveTextContent('CONNECTED');
			expect(calls.filter((call) => call.route === 'sync')).toHaveLength(1);
		}
	);
}

it('로그아웃은 Provider를 해제하고 재로그인은 연결 상태를 다시 조회한다', async () => {
	const calls = mockApi((_, number) =>
		statusResponse(number === 1 ? 'CONNECTED' : 'NOT_CONNECTED')
	);
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	await page.getByRole('button', { name: '합성 로그아웃' }).click();
	await expect.element(child()).not.toBeInTheDocument();
	await expect.element(page.getByLabelText('자식 해제 횟수')).toHaveTextContent('1');
	await page.getByRole('button', { name: '합성 다시 로그인' }).click();
	await expect.element(guide()).toBeVisible();
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status']);
});

it('프로필 경로는 NOT_CONNECTED여도 프로필 자식을 표시한다', async () => {
	const calls = mockApi(() => statusResponse('NOT_CONNECTED'));
	mount({ enabled: true, protectedRoute: false, withSync: false });
	await expect.element(child()).toHaveTextContent('프로필 표시');
	await expect.element(state()).toHaveTextContent('NOT_CONNECTED');
	await expect.element(guide()).not.toBeInTheDocument();
	expect(routes(calls)).toEqual(['status']);
});

it('기본 비활성 설정은 상태 GET 없이 기존 POST·GET 흐름을 유지한다', async () => {
	const calls = mockApi(() => {
		throw new Error('비활성 상태에서 연동 API 호출');
	});
	mount();
	await expect.element(child()).toHaveTextContent('기록 표시');
	expect(routes(calls)).toEqual(['sync', 'record']);
});

it.each(['CONNECTED', 'UNKNOWN', 'NOT_CONNECTED', 'ERROR'] as const)(
	'기존 수동 동기화는 새 %s 판정을 GET한 뒤 POST 허용 여부를 결정한다',
	async (next) => {
		const calls = mockApi(
			(_, number) =>
				number === 1
					? statusResponse('CONNECTED')
					: next === 'ERROR'
						? failure(503, 'HEALTH_DATA_UNAVAILABLE')
						: statusResponse(next),
			'SKIPPED_RECENT'
		);
		mount({ enabled: true });
		await expect.element(child()).toHaveTextContent('기록 표시');
		const original = child().element();
		await page.getByRole('button', { name: '건강 기록 다시 확인', exact: true }).click();
		if (next === 'NOT_CONNECTED') await expect.element(guide()).toBeVisible();
		else if (next === 'ERROR')
			await expect.element(page.getByRole('alert')).toHaveTextContent('마지막으로 확인한 상태');
		else
			await vi.waitFor(() =>
				expect(calls.filter((call) => call.route === 'record')).toHaveLength(2)
			);
		const allowed = next === 'CONNECTED' || next === 'UNKNOWN';
		expect(routes(calls)).toEqual(
			allowed
				? ['status', 'sync', 'record', 'status', 'sync', 'record']
				: next === 'ERROR'
					? ['status', 'sync', 'record', 'status', 'status']
					: ['status', 'sync', 'record', 'status']
		);
		if (next !== 'NOT_CONNECTED') expect(child().element()).toBe(original);
	}
);

it.each(['503', 'network'] as const)(
	'최초 %s 재시도는 진행 문구를 표시하고 성공 전까지 자식과 POST를 만들지 않는다',
	async (kind) => {
		vi.useFakeTimers();
		const held = deferred<Response>();
		const calls = mockApi((_, number) => {
			if (number > 1) return held.promise;
			if (kind === 'network') throw new TypeError('offline');
			return failure(503, 'HEALTH_DATA_UNAVAILABLE');
		});
		mount({ enabled: true });
		await vi.advanceTimersByTimeAsync(0);
		await tick();
		await expect.element(page.getByText(retryNotice, { exact: true })).toBeVisible();
		await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
		await expect.element(closeNotice()).not.toBeInTheDocument();
		await expect.element(child()).not.toBeInTheDocument();
		expect(routes(calls)).toEqual(['status']);
		await vi.advanceTimersByTimeAsync(499);
		expect(routes(calls)).toEqual(['status']);
		await vi.advanceTimersByTimeAsync(1);
		expect(routes(calls)).toEqual(['status', 'status']);
		held.resolve(statusResponse('CONNECTED'));
		await vi.advanceTimersByTimeAsync(0);
		await tick();
		await expect.element(child()).toHaveTextContent('기록 표시');
		await expect.element(page.getByText(retryNotice, { exact: true })).not.toBeInTheDocument();
		expect(routes(calls)).toEqual(['status', 'status', 'sync', 'record']);
		await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('1');
	}
);

it.each(['CONNECTED', 'UNKNOWN', 'NOT_CONNECTED'] as const)(
	'%s의 자동 재시도 중에는 마지막 화면과 자식 DOM을 유지하고 안내를 닫지 못한다',
	async (previous) => {
		const held = deferred<Response>();
		const calls = mockApi((_, number) =>
			number === 1
				? statusResponse(previous)
				: number === 2
					? failure(503, 'HEALTH_DATA_UNAVAILABLE')
					: held.promise
		);
		mount({ enabled: true });
		if (previous === 'NOT_CONNECTED') await expect.element(guide()).toBeVisible();
		else await expect.element(child()).toHaveTextContent('기록 표시');
		const original = previous === 'NOT_CONNECTED' ? undefined : child().element();
		vi.useFakeTimers();
		clickWithoutAdvancingClock(page.getByRole('button', { name: '하네스 재조회' }).element());
		await vi.advanceTimersByTimeAsync(0);
		await tick();
		await expect.element(page.getByText(retryNotice, { exact: true })).toBeVisible();
		await expect.element(closeNotice()).not.toBeInTheDocument();
		await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
		await expect.element(page.getByRole('button', { name: '연동 상태 확인 중' })).toBeDisabled();
		await expect.element(state()).toHaveTextContent(previous);
		if (original) expect(child().element()).toBe(original);
		else await expect.element(child()).not.toBeInTheDocument();
		await vi.advanceTimersByTimeAsync(499);
		expect(calls.filter((call) => call.route === 'status')).toHaveLength(2);
		await vi.advanceTimersByTimeAsync(1);
		expect(calls.filter((call) => call.route === 'status')).toHaveLength(3);
		held.resolve(statusResponse(previous));
		await vi.advanceTimersByTimeAsync(0);
		await tick();
		await expect.element(page.getByText(retryNotice, { exact: true })).not.toBeInTheDocument();
		if (original) expect(child().element()).toBe(original);
		expect(calls.filter((call) => call.route === 'sync')).toHaveLength(
			previous === 'NOT_CONNECTED' ? 0 : 1
		);
	}
);

it('UNKNOWN의 최종 조회 오류는 닫기 상태와 무관하게 남고 자동 재시도도 두 요청에서 끝난다', async () => {
	const calls = mockApi((_, number) =>
		number === 1 ? statusResponse('UNKNOWN') : failure(503, 'HEALTH_DATA_UNAVAILABLE')
	);
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	await closeNotice().click();
	vi.useFakeTimers();
	clickWithoutAdvancingClock(page.getByRole('button', { name: '하네스 재조회' }).element());
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	await expect.element(page.getByText(retryNotice, { exact: true })).toBeVisible();
	await expect.element(closeNotice()).not.toBeInTheDocument();
	await vi.advanceTimersByTimeAsync(500);
	await tick();
	await expect.element(page.getByRole('alert')).toHaveTextContent('마지막으로 확인한 상태');
	await expect.element(closeNotice()).not.toBeInTheDocument();
	await expect.element(page.getByRole('button', { name: '연동 상태 다시 확인' })).toBeEnabled();
	await expect.element(state()).toHaveTextContent('UNKNOWN');
	expect(child().element()).toBe(original);
	await vi.advanceTimersByTimeAsync(15000);
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status', 'status']);
});

it.each(['initial-error', 'NOT_CONNECTED', 'UNKNOWN'] as const)(
	'%s의 재확인은 상태에 맞는 공통 Button을 사용한다',
	async (screen) => {
		mockApi(() =>
			screen === 'initial-error' ? failure(500, 'INTERNAL_ERROR') : statusResponse(screen)
		);
		mount({ enabled: true, withSync: false });
		const button = page.getByRole('button', { name: '연동 상태 다시 확인', exact: true });
		await expect.element(button).toBeVisible();
		await expect.element(button).toHaveAttribute('data-slot', 'button');
		await expect.element(button).toHaveAttribute('type', 'button');
		const appearance =
			screen === 'NOT_CONNECTED'
				? buttonVariants({ variant: 'outline', size: 'default' })
				: buttonVariants({ variant: 'outline', size: 'sm' });
		for (const className of appearance.split(' '))
			expect(button.element().classList.contains(className)).toBe(true);
		expect(getComputedStyle(button.element()).height).toBe(
			screen === 'NOT_CONNECTED' ? '44px' : '36px'
		);
		if (screen === 'UNKNOWN') {
			await expect.element(closeNotice()).toHaveTextContent('안내 닫기');
			await expect.element(closeNotice()).toHaveAttribute('data-slot', 'button');
			for (const className of buttonVariants({ variant: 'ghost', size: 'sm' }).split(' '))
				expect(closeNotice().element().classList.contains(className)).toBe(true);
		} else await expect.element(closeNotice()).not.toBeInTheDocument();
	}
);

it('UNKNOWN 안내는 키보드로 닫고 같은 세션 재확인·탭 복귀에도 자식을 다시 만들지 않는다', async () => {
	const calls = mockApi(() => statusResponse('UNKNOWN'));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	const closeButton = closeNotice().element();
	if (!(closeButton instanceof HTMLButtonElement))
		throw new Error('키보드로 조작할 버튼이 필요합니다.');
	closeButton.focus();
	expect(document.activeElement).toBe(closeButton);
	await userEvent.keyboard('{Enter}');
	await expect.element(closeNotice()).not.toBeInTheDocument();
	expect(child().element()).toBe(original);
	expect(routes(calls)).toEqual(['status', 'sync', 'record']);
	await page.getByRole('button', { name: '하네스 재조회' }).click();
	await vi.waitFor(() => expect(calls.filter((call) => call.route === 'status')).toHaveLength(2));
	await expect.element(closeNotice()).not.toBeInTheDocument();
	vi.useFakeTimers();
	vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
	window.dispatchEvent(new Event('focus'));
	document.dispatchEvent(new Event('visibilitychange'));
	await vi.advanceTimersByTimeAsync(50);
	await tick();
	await expect.element(closeNotice()).not.toBeInTheDocument();
	expect(child().element()).toBe(original);
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status', 'status']);
	await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('1');
});

it('UNKNOWN 닫기 상태는 같은 Provider의 페이지 전환에도 공유한다', async () => {
	const calls = mockApi(() => statusResponse('UNKNOWN'));
	const view = mount({ enabled: true, withSync: false });
	await expect.element(closeNotice()).toBeVisible();
	const version = authSession.sessionVersion;
	await closeNotice().click();
	await view.rerender({ protectedRoute: false });
	await expect.element(child()).toHaveTextContent('프로필 표시');
	await view.rerender({ protectedRoute: true });
	await expect.element(child()).toHaveTextContent('프로필 표시');
	await expect.element(closeNotice()).not.toBeInTheDocument();
	await expect.element(state()).toHaveTextContent('UNKNOWN');
	expect(authSession.sessionVersion).toBe(version);
	expect(routes(calls)).toEqual(['status']);
});

it.each(['different-account', 'same-id-new-session'] as const)(
	'%s는 이전 세션의 UNKNOWN 닫기 상태를 버린다',
	async (next) => {
		const calls = mockApi(() => statusResponse('UNKNOWN'));
		mount({ enabled: true });
		await expect.element(child()).toHaveTextContent('기록 표시');
		await closeNotice().click();
		await page
			.getByRole('button', {
				name: next === 'different-account' ? '합성 계정 전환' : '합성 같은 계정 새 세션'
			})
			.click();
		await expect.element(closeNotice()).toBeVisible();
		await expect.element(child()).toHaveTextContent('기록 표시');
		expect(authSession.user?.id).toBe(next === 'different-account' ? second.id : first.id);
		expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status', 'sync', 'record']);
	}
);

it('재시도 요청 중 계정 전환 뒤 도착한 이전 401은 새 세션을 지우지 않는다', async () => {
	vi.useFakeTimers();
	const held = deferred<Response>();
	let delivered = false;
	const calls = mockApi(async (call, number) => {
		if (call.owner !== first.id) return statusResponse('CONNECTED');
		if (number === 1) return failure(503, 'HEALTH_DATA_UNAVAILABLE');
		const response = await held.promise;
		delivered = true;
		return response;
	});
	mount({ enabled: true });
	await vi.advanceTimersByTimeAsync(0);
	await vi.advanceTimersByTimeAsync(500);
	expect(calls.filter((call) => call.route === 'status')).toHaveLength(2);
	clickWithoutAdvancingClock(page.getByRole('button', { name: '합성 계정 전환' }).element());
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	await expect.element(child()).toHaveTextContent('기록 표시');
	expect(calls[0].signal?.aborted).toBe(true);
	expect(calls[1].signal?.aborted).toBe(true);
	held.resolve(failure(401, 'AUTH_EXPIRED'));
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	expect(delivered).toBe(true);
	expect(authSession.status).toBe('authenticated');
	expect(authSession.user?.id).toBe(second.id);
	await expect.element(state()).toHaveTextContent('CONNECTED');
	expect(routes(calls)).toEqual(['status', 'status', 'status', 'sync', 'record']);
});

for (const previous of ['CONNECTED', 'UNKNOWN'] as const) {
	it.each(['success', 'failure'] as const)(
		`${previous}: 오류 후 수동 재확인의 첫 GET 대기부터 진행 안내와 한 요청을 유지하고 %s를 정리한다`,
		async (outcome) => {
			const held = deferred<Response>();
			const calls = mockApi((_, number) => {
				if (number === 1 || number > 3) return statusResponse(previous);
				if (number === 2) return failure(500, 'INTERNAL_ERROR');
				return held.promise;
			});
			mount({ enabled: true });
			await expect.element(child()).toHaveTextContent('기록 표시');
			const original = child().element();
			if (previous === 'UNKNOWN') await closeNotice().click();
			await page.getByRole('button', { name: '하네스 재조회' }).click();
			await expect.element(page.getByRole('alert')).toHaveTextContent('마지막으로 확인한 상태');
			vi.useFakeTimers();
			const button = page
				.getByRole('button', { name: '연동 상태 다시 확인', exact: true })
				.element();
			for (let i = 0; i < 3; i++) clickWithoutAdvancingClock(button);
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
			await expect
				.element(page.getByText(checkingNotice, { exact: true }))
				.toHaveAttribute('role', 'status');
			await expect.element(page.getByRole('button', { name: '연동 상태 확인 중' })).toBeDisabled();
			await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
			await expect.element(closeNotice()).not.toBeInTheDocument();
			await expect.element(state()).toHaveTextContent(previous);
			expect(child().element()).toBe(original);
			expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status', 'status']);
			await vi.advanceTimersByTimeAsync(500);
			await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
			expect(calls.filter((call) => call.route === 'status')).toHaveLength(3);
			held.resolve(
				outcome === 'success' ? statusResponse(previous) : failure(500, 'INTERNAL_ERROR')
			);
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(page.getByText(checkingNotice, { exact: true })).not.toBeInTheDocument();
			if (outcome === 'failure') {
				await expect.element(page.getByRole('alert')).toHaveTextContent('마지막으로 확인한 상태');
				const next = page.getByRole('button', { name: '연동 상태 다시 확인', exact: true });
				await expect.element(next).toBeEnabled();
				clickWithoutAdvancingClock(next.element());
				await vi.advanceTimersByTimeAsync(0);
				await tick();
			}
			await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
			await expect.element(page.getByText(checkingNotice, { exact: true })).not.toBeInTheDocument();
			await expect
				.element(page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }))
				.not.toBeInTheDocument();
			await expect.element(closeNotice()).not.toBeInTheDocument();
			expect(child().element()).toBe(original);
			await expect.element(page.getByLabelText('자식 마운트 횟수')).toHaveTextContent('1');
			expect(calls.filter((call) => call.route === 'status')).toHaveLength(
				outcome === 'failure' ? 4 : 3
			);
			expect(calls.filter((call) => call.route === 'sync')).toHaveLength(1);
			expect(calls.filter((call) => call.route === 'record')).toHaveLength(1);
		}
	);
}

it('닫지 않은 UNKNOWN도 수동 재확인 중에는 진행 안내만 표시하고 완료 후 닫기를 복원한다', async () => {
	const held = deferred<Response>();
	const calls = mockApi((_, number) => (number === 1 ? statusResponse('UNKNOWN') : held.promise));
	mount({ enabled: true });
	await expect.element(child()).toHaveTextContent('기록 표시');
	const original = child().element();
	await expect.element(closeNotice()).toBeVisible();
	vi.useFakeTimers();
	clickWithoutAdvancingClock(
		page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }).element()
	);
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
	await expect.element(closeNotice()).not.toBeInTheDocument();
	await expect.element(page.getByRole('button', { name: '연동 상태 확인 중' })).toBeDisabled();
	held.resolve(statusResponse('UNKNOWN'));
	await vi.advanceTimersByTimeAsync(0);
	await tick();
	await expect.element(page.getByText(checkingNotice, { exact: true })).not.toBeInTheDocument();
	await expect.element(closeNotice()).toBeVisible();
	await expect
		.element(page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }))
		.toBeEnabled();
	expect(child().element()).toBe(original);
	expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status']);
});

for (const trigger of ['different-account', 'logout-login'] as const) {
	it.each(['401', 'NOT_CONNECTED'] as const)(
		`${trigger}: 이전 수동 재확인 취소와 늦은 %s는 새 세션의 진행 안내를 지우지 않는다`,
		async (late) => {
			const held = { old: deferred<Response>(), next: deferred<Response>() };
			const attempts = { old: 0, next: 0 };
			let session: keyof typeof held = 'old';
			let oldDelivered = false;
			const calls = mockApi(async () => {
				const current = session;
				const number = ++attempts[current];
				if (number === 1) return statusResponse('CONNECTED');
				if (number === 2) return failure(500, 'INTERNAL_ERROR');
				const response = await held[current].promise;
				if (current === 'old') oldDelivered = true;
				return response;
			});
			mount({ enabled: true });
			await expect.element(child()).toHaveTextContent('기록 표시');
			await page.getByRole('button', { name: '하네스 재조회' }).click();
			await expect.element(page.getByRole('alert')).toBeVisible();
			vi.useFakeTimers();
			clickWithoutAdvancingClock(
				page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }).element()
			);
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
			const oldSignal = calls.filter((call) => call.route === 'status')[2].signal;
			session = 'next';
			if (trigger === 'different-account') {
				clickWithoutAdvancingClock(page.getByRole('button', { name: '합성 계정 전환' }).element());
			} else {
				clickWithoutAdvancingClock(page.getByRole('button', { name: '합성 로그아웃' }).element());
				await vi.advanceTimersByTimeAsync(0);
				await tick();
				await expect.element(child()).not.toBeInTheDocument();
				await expect
					.element(page.getByText(checkingNotice, { exact: true }))
					.not.toBeInTheDocument();
				clickWithoutAdvancingClock(
					page.getByRole('button', { name: '합성 다시 로그인' }).element()
				);
			}
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(child()).toHaveTextContent('기록 표시');
			expect(oldSignal?.aborted).toBe(true);
			const original = child().element();
			clickWithoutAdvancingClock(page.getByRole('button', { name: '하네스 재조회' }).element());
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(page.getByRole('alert')).toBeVisible();
			clickWithoutAdvancingClock(
				page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }).element()
			);
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
			held.old.resolve(
				late === '401' ? failure(401, 'AUTH_EXPIRED') : statusResponse('NOT_CONNECTED')
			);
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			expect(oldDelivered).toBe(true);
			expect(authSession.status).toBe('authenticated');
			expect(authSession.user?.id).toBe(trigger === 'different-account' ? second.id : first.id);
			await expect.element(state()).toHaveTextContent('CONNECTED');
			await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
			await expect.element(page.getByRole('button', { name: '연동 상태 확인 중' })).toBeDisabled();
			await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
			expect(child().element()).toBe(original);
			expect(attempts).toEqual({ old: 3, next: 3 });
			held.next.resolve(statusResponse('CONNECTED'));
			await vi.advanceTimersByTimeAsync(0);
			await tick();
			await expect.element(page.getByText(checkingNotice, { exact: true })).not.toBeInTheDocument();
			await expect.element(guide()).not.toBeInTheDocument();
			expect(child().element()).toBe(original);
			expect(calls.filter((call) => call.route === 'sync')).toHaveLength(2);
			expect(calls.filter((call) => call.route === 'record')).toHaveLength(2);
		}
	);
}

it.each(['CONNECTED', 'UNKNOWN'] as const)(
	'%s 정상 배경 재조회는 수동 재확인 배너나 버튼을 만들지 않는다',
	async (previous) => {
		const held = deferred<Response>();
		const calls = mockApi((_, number) => (number === 1 ? statusResponse(previous) : held.promise));
		mount({ enabled: true });
		await expect.element(child()).toHaveTextContent('기록 표시');
		const original = child().element();
		if (previous === 'UNKNOWN') await closeNotice().click();
		vi.useFakeTimers();
		vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
		window.dispatchEvent(new Event('focus'));
		document.dispatchEvent(new Event('visibilitychange'));
		await vi.advanceTimersByTimeAsync(50);
		await tick();
		await expect.element(page.getByLabelText('연동 조회 중')).toHaveTextContent('true');
		await expect.element(page.getByText(checkingNotice, { exact: true })).not.toBeInTheDocument();
		await expect.element(page.getByText(retryNotice, { exact: true })).not.toBeInTheDocument();
		await expect.element(page.getByRole('alert')).not.toBeInTheDocument();
		await expect
			.element(page.getByRole('button', { name: '연동 상태 확인 중' }))
			.not.toBeInTheDocument();
		await expect.element(closeNotice()).not.toBeInTheDocument();
		expect(child().element()).toBe(original);
		expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status']);
		held.resolve(statusResponse(previous));
		await vi.advanceTimersByTimeAsync(0);
		await tick();
		await expect.element(page.getByLabelText('연동 조회 중')).toHaveTextContent('false');
		expect(child().element()).toBe(original);
		expect(routes(calls)).toEqual(['status', 'sync', 'record', 'status']);
	}
);

for (const initial of ['NOT_CONNECTED', 'ERROR', 'LOADING'] as const) {
	it.each(['CONNECTED', 'UNKNOWN'] as const)(
		`${initial}의 차단 제목은 맞는 상태를 알리고 %s 전환 후 화면 제목에 개입하지 않는다`,
		async (next) => {
			const held = deferred<Response>();
			const calls = mockApi((_, number) => {
				if (initial === 'LOADING' || number > 1) return held.promise;
				return initial === 'ERROR' ? failure(500, 'INTERNAL_ERROR') : statusResponse(initial);
			});
			const view = mount({ enabled: true, withSync: false });
			if (initial === 'NOT_CONNECTED') await expect.element(guide()).toBeVisible();
			else if (initial === 'ERROR')
				await expect
					.element(page.getByRole('heading', { name: '앱 연동 상태를 확인하지 못했어요' }))
					.toBeVisible();
			else await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
			expect(document.title).toBe(
				initial === 'NOT_CONNECTED'
					? '앱 연동 안내 | 캔디핏'
					: initial === 'ERROR'
						? '앱 연동 상태 확인 오류 | 캔디핏'
						: '앱 연동 상태 확인 중 | 캔디핏'
			);
			if (initial !== 'LOADING') {
				await page.getByRole('button', { name: '연동 상태 다시 확인', exact: true }).click();
				if (initial === 'ERROR') {
					await expect.element(page.getByText(checkingNotice, { exact: true })).toBeVisible();
					expect(document.title).toBe('앱 연동 상태 확인 중 | 캔디핏');
				} else {
					await expect
						.element(page.getByRole('button', { name: '연동 상태 확인 중' }))
						.toBeDisabled();
				}
			}
			held.resolve(statusResponse(next));
			await expect.element(child()).toHaveTextContent('프로필 표시');
			await expect.element(state()).toHaveTextContent(next);
			const routeTitle = `합성 ${next} 화면 | 캔디핏`;
			// 실제 route가 쓰는 제목을 모델링한다. 하네스 자식에는 svelte:head가 없다.
			document.title = routeTitle;
			await view.rerender({ protectedRoute: false });
			await view.rerender({ protectedRoute: true });
			if (next === 'UNKNOWN') await closeNotice().click();
			await tick();
			expect(document.title).toBe(routeTitle);
			await expect.element(guide()).not.toBeInTheDocument();
			expect(routes(calls)).toEqual(initial === 'LOADING' ? ['status'] : ['status', 'status']);
		}
	);
}

it.each(['NOT_CONNECTED', 'ERROR', 'LOADING'] as const)(
	'%s이어도 비보호 경로는 제목을 덮어쓰지 않는다',
	async (connection) => {
		const held = deferred<Response>();
		const calls = mockApi(() =>
			connection === 'LOADING'
				? held.promise
				: connection === 'ERROR'
					? failure(500, 'INTERNAL_ERROR')
					: statusResponse(connection)
		);
		const title = '합성 마이페이지 | 캔디핏';
		document.title = title;
		mount({ enabled: true, protectedRoute: false, withSync: false });
		await expect.element(child()).toHaveTextContent('프로필 표시');
		await vi.waitFor(() => expect(calls).toHaveLength(1));
		if (connection !== 'LOADING')
			await expect.element(page.getByLabelText('연동 조회 중')).toHaveTextContent('false');
		expect(document.title).toBe(title);
		held.resolve(statusResponse('CONNECTED'));
		await tick();
		expect(document.title).toBe(title);
	}
);
