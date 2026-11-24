import { expect, test } from '@playwright/test';
import { e2eSession, mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';

const endpoint = '**/api/v1/health/sync';
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
});

test('최초 동기화 중 기간을 바꾸면 취소된 대기 요청 없이 마지막 기간만 조회한다', async ({
	page
}) => {
	let release = () => {};
	let posts = 0;
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	const metrics: URL[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (url.pathname.startsWith('/api/v1/health/metrics/')) metrics.push(url);
	});
	await page.route(endpoint, async (route) => {
		posts++;
		await gate;
		await route.fulfill({ json: { data: { status: 'SYNCED' } } });
	});
	try {
		await page.goto('/health');
		await expect.poll(() => posts).toBe(1);
		await expect(page.locator('.health-summary-card[aria-busy="true"]')).toHaveCount(4);
		const period = page.getByRole('group', { name: '주간 기간 선택' });
		await period.getByRole('button', { name: '이전 주', exact: true }).click();
		await period.getByRole('button', { name: '다음 주', exact: true }).click();
		await period.getByRole('button', { name: '이전 주', exact: true }).click();
		await expect(period.getByRole('status')).toHaveText('2026.10.13 ~ 10.19');
		expect(metrics).toHaveLength(0);
		release();
		await expect(page.getByRole('figure')).toHaveCount(4);
		expect(metrics).toHaveLength(4);
		for (const url of metrics) expect(url.search).toBe('?start=2026-10-13&end=2026-10-19');
		expect(posts).toBe(1);
	} finally {
		release();
	}
});

test('POST의 세션 만료(AUTH_EXPIRED)는 재전송 없이 로그인으로 이동한다', async ({ page }) => {
	let posts = 0;
	await page.route(endpoint, (route) => {
		posts++;
		return route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_EXPIRED', message: 'synthetic' } }
		});
	});
	await page.goto('/home');
	await expect(page).toHaveURL('/login');
	expect(posts).toBe(1);
});

test('POST AUTH_REQUIRED는 로그인으로 이동하며 건강 GET을 시작하지 않는다', async ({ page }) => {
	let gets = 0;
	page.on('request', (r) => {
		if (r.method() === 'GET' && r.url().includes('/api/v1/health/')) gets++;
	});
	await page.route(endpoint, (route) =>
		route.fulfill({ status: 401, json: { error: { code: 'AUTH_REQUIRED', message: 'synthetic' } } })
	);
	await page.goto('/health');
	await expect(page).toHaveURL('/login');
	expect(gets).toBe(0);
});
for (const path of [
	'/home',
	'/health',
	'/health/activity',
	'/health/heart',
	'/health/sleep',
	'/health/day/2026-10-26',
	'/health/sleep/2026-10-26'
]) {
	test(`${path}: 직접 진입은 인증 복원 → POST 응답 → GET 순서다`, async ({ page }) => {
		let restore = () => {},
			sync = () => {};
		const authGate = new Promise<void>((resolve) => (restore = resolve)),
			syncGate = new Promise<void>((resolve) => (sync = resolve));
		const events: string[] = [];
		page.on('request', (request) => {
			const url = new URL(request.url());
			if (url.pathname.endsWith('/auth/session')) events.push('auth');
			else if (url.pathname.endsWith('/health/sync')) events.push('post');
			else if (request.method() === 'GET' && url.pathname.startsWith('/api/v1/health/'))
				events.push('get');
		});
		await page.route('**/api/v1/auth/session', async (route) => {
			await authGate;
			await route.fulfill({ json: { data: e2eSession } });
		});
		await page.route(endpoint, async (route) => {
			expect(route.request().method()).toBe('POST');
			expect(route.request().postData()).toBeNull();
			expect(new URL(route.request().url()).search).toBe('');
			await syncGate;
			await route.fulfill({ json: { data: { status: 'SYNCED' } } });
		});
		try {
			await page.goto(path);
			await expect.poll(() => events).toEqual(['auth']);
			restore();
			await expect.poll(() => events).toEqual(['auth', 'post']);
			await expect(page.getByRole('main')).toBeVisible();
			await expect.poll(() => page.locator('.health-skeleton').count()).toBeGreaterThan(0);
			await expect(page.locator('img[src*="character-loading"]')).toHaveCount(0);
			sync();
			await expect.poll(() => events.filter((e) => e === 'get').length).toBeGreaterThan(0);
			expect(events.slice(0, 3)).toEqual(['auth', 'post', 'get']);
			expect(events.filter((e) => e === 'post')).toHaveLength(1);
		} finally {
			restore();
			sync();
		}
	});
	test(`${path}: DISABLED 안내 없이 POST 이후 GET을 실행한다`, async ({ page }) => {
		let posts = 0;
		const events: string[] = [];
		page.on('request', (request) => {
			if (request.url().endsWith('/health/sync')) events.push('post');
			else if (request.method() === 'GET' && request.url().includes('/api/v1/health/'))
				events.push('get');
		});
		await page.route(endpoint, (route) => {
			posts++;
			return route.fulfill({ json: { data: { status: 'DISABLED' } } });
		});
		await page.goto(path);
		const notice = page.getByRole('status').filter({ hasText: '지금은 새 기록을 가져오지 않아요' });
		await expect.poll(() => events.includes('get')).toBe(true);
		await expect(
			page.getByRole('status').filter({ hasText: '기존 건강 기록을 조회하고 있어요.' })
		).toHaveCount(0);
		await expect(notice).toHaveCount(0);
		await expect(page.getByRole('status').filter({ hasText: /^\s*$/ })).toHaveCount(0);
		expect(events.slice(0, 2)).toEqual(['post', 'get']);
		await expect(page.locator('main')).toBeVisible();
		expect(posts).toBe(1);
	});
}
for (const [status, text] of [
	['SKIPPED_RECENT', '최근 갱신 시도가 있어'],
	['SYNTHETIC', '샘플 계정이므로'],
	['DISABLED', '지금은 새 기록을 가져오지 않아요']
]) {
	test(`${status}는 성공으로 오인하지 않고 기존 GET 결과를 표시한다`, async ({ page }) => {
		await page.route(endpoint, (route) => route.fulfill({ json: { data: { status } } }));
		await page.goto('/health');
		if (status === 'DISABLED')
			await expect(page.getByRole('status').filter({ hasText: text })).toHaveCount(0);
		else await expect(page.getByRole('status').filter({ hasText: text })).toBeVisible();
		await expect(page.getByRole('figure')).toHaveCount(4);
		await expect(page.getByText('건강 기록을 갱신했어요.', { exact: true })).toHaveCount(0);
	});
}

for (const width of [360, 1366]) {
	for (const status of ['SYNCED', 'SKIPPED_RECENT', 'BUSY', 'TIMEOUT', 'DISABLED']) {
		test(`${width}px 홈: ${status}의 내부 안내 없이 점수·기록을 표시한다`, async ({ page }) => {
			await page.setViewportSize({ width, height: 800 });
			let posts = 0;
			await page.route(endpoint, (route) => {
				posts++;
				return status === 'TIMEOUT'
					? route.fulfill({
							status: 503,
							json: { error: { code: 'HEALTH_SYNC_UNAVAILABLE', context: { reason: 'TIMEOUT' } } }
						})
					: route.fulfill({ json: { data: { status } } });
			});
			await page.goto('/home');
			await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
			await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
			await expect(
				page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
			).toBeVisible();
			await expect(
				page.getByText(
					/최근 갱신 시도가 있어|샘플 계정이므로|지금은 새 기록을 가져오지 않아요|건강 기록을 갱신했어요\.|다른 갱신 작업|서버에서 작업이 계속될|건강 데이터를 갱신하고 있어요/
				)
			).toHaveCount(0);
			await expect(page.getByRole('button', { name: '건강 기록 다시 확인' })).toHaveCount(0);
			expect(posts).toBe(1);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			await page.context().setOffline(true);
			await expect(page.getByText('인터넷 연결이 끊겼어요', { exact: true })).toBeVisible();
			await page.context().setOffline(false);
		});
	}
	test(`${width}px 홈: 합성 계정 안내는 내부 상태와 구분해 유지한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 800 });
		await page.route(endpoint, (route) =>
			route.fulfill({ json: { data: { status: 'SYNTHETIC' } } })
		);
		await page.goto('/home');
		await expect(page.getByRole('status').filter({ hasText: '샘플 계정이므로' })).toBeVisible();
		await expect(page.getByRole('button', { name: '건강 기록 다시 확인' })).toHaveCount(0);
		await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	});
}

test('홈: 생략된 동기화 뒤 GET 오류는 숨기지 않는다', async ({ page }) => {
	await page.route(endpoint, (route) =>
		route.fulfill({ json: { data: { status: 'SKIPPED_RECENT' } } })
	);
	await page.route('**/api/v1/health/metrics/*?*', (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: '합성 조회 오류' } }
		})
	);
	await page.goto('/home');
	await expect(page.getByRole('alert')).toHaveText(
		Array(3).fill('건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.')
	);
	await expect(page.getByText(/최근 갱신 시도가 있어/)).toHaveCount(0);
});

test('홈: 필요한 오류에서 재확인하고 내부 진행 상태로 끝나면 안내만 숨긴다', async ({ page }) => {
	let posts = 0;
	let release = () => {};
	const gate = new Promise<void>((resolve) => (release = resolve));
	await page.route(endpoint, async (route) => {
		if (++posts === 1)
			return route.fulfill({
				status: 409,
				json: { error: { code: 'CANDIY_USER_NOT_LINKED', message: 'synthetic' } }
			});
		await gate;
		return route.fulfill({ json: { data: { status: 'BUSY' } } });
	});
	await page.goto('/home');
	await expect(
		page.getByRole('status').filter({ hasText: '앱 연동을 확인해 주세요.' })
	).toBeVisible();
	const retry = page.getByRole('button', { name: '건강 기록 다시 확인' });
	try {
		await retry.focus();
		await page.keyboard.press('Enter');
		await expect.poll(() => posts).toBe(2);
		await expect(page.getByRole('button', { name: '확인하는 중' })).toBeDisabled();
		await expect(page.getByText('건강 데이터를 갱신하고 있어요.', { exact: true })).toHaveCount(0);
		await expect(
			page.getByRole('status').filter({ hasText: '앱 연동을 확인해 주세요.' })
		).toBeVisible();
		release();
		await expect(page.getByRole('button', { name: /건강 기록 다시 확인|확인하는 중/ })).toHaveCount(
			0
		);
		await expect(page.getByText(/최근 갱신 시도가 있어|다른 갱신 작업/)).toHaveCount(0);
		await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	} finally {
		release();
	}
});

for (const state of ['BUSY', 'TIMEOUT']) {
	test(`홈 ${state}: 내부 배너 없이 두 번의 자동 재조회는 유지한다`, async ({ page }) => {
		await page.clock.install({ time: new Date('2026-10-26T03:00:00Z') });
		let posts = 0,
			gets = 0;
		page.on('request', (request) => {
			if (request.url().includes('/metrics/steps?')) gets++;
		});
		await page.route(endpoint, (route) => {
			posts++;
			return state === 'BUSY'
				? route.fulfill({ json: { data: { status: state } } })
				: route.fulfill({
						status: 503,
						json: { error: { code: 'HEALTH_SYNC_UNAVAILABLE', context: { reason: 'TIMEOUT' } } }
					});
		});
		await page.goto('/home');
		await expect.poll(() => gets).toBe(1);
		await page.clock.fastForward(2000);
		await expect.poll(() => gets).toBe(2);
		await page.clock.fastForward(3000);
		await expect.poll(() => gets).toBe(3);
		await page.clock.fastForward(60000);
		expect(gets).toBe(3);
		expect(posts).toBe(1);
		await expect(
			page.getByText(/다른 갱신 작업|서버에서 작업이 계속될|건강 데이터를 갱신하고 있어요/)
		).toHaveCount(0);
		await expect(page.getByRole('button', { name: '건강 기록 다시 확인' })).toHaveCount(0);
	});
}
for (const state of ['BUSY', 'TIMEOUT']) {
	test(`${state}: POST 자동 반복 없이 GET은 2회만 재조회하고 수동 재시도한다`, async ({ page }) => {
		await page.clock.install({ time: new Date('2026-10-26T03:00:00Z') });
		let posts = 0,
			gets = 0;
		page.on('request', (request) => {
			if (request.url().includes('/metrics/steps?')) gets++;
		});
		await page.route(endpoint, (route) => {
			posts++;
			return state === 'BUSY'
				? route.fulfill({ json: { data: { status: state } } })
				: route.fulfill({
						status: 503,
						json: {
							error: {
								code: 'HEALTH_SYNC_UNAVAILABLE',
								message: 'synthetic',
								context: { reason: 'TIMEOUT', metrics: [] }
							}
						}
					});
		});
		await page.goto('/health');
		await expect.poll(() => gets).toBe(1);
		await page.clock.fastForward(2000);
		await expect.poll(() => gets).toBe(2);
		await page.clock.fastForward(3000);
		await expect.poll(() => gets).toBe(3);
		await page.clock.fastForward(60000);
		expect(gets).toBe(3);
		expect(posts).toBe(1);
		const retry = page.getByRole('button', { name: '건강 기록 다시 확인' });
		await retry.focus();
		await expect(retry).toBeFocused();
		await page.keyboard.press('Enter');
		await expect.poll(() => posts).toBe(2);
		await expect.poll(() => gets).toBe(4);
	});
}
for (const state of ['network', 'invalid', 'not-linked', 'candiy-auth', 'partial']) {
	test(`POST ${state} 후에도 GET을 조회하고 세션을 보존한다`, async ({ page }) => {
		await page.route(endpoint, (route) => {
			if (state === 'network') return route.abort();
			if (state === 'invalid') return route.fulfill({ json: { data: { status: 'UNKNOWN' } } });
			return route.fulfill({
				status: state === 'not-linked' ? 409 : 503,
				json: {
					error: {
						code: state === 'not-linked' ? 'CANDIY_USER_NOT_LINKED' : 'HEALTH_SYNC_UNAVAILABLE',
						message: 'synthetic',
						context: {
							reason: state === 'candiy-auth' ? 'CANDIY_AUTH_FAILED' : 'METRIC_FAILED',
							metrics: ['steps']
						}
					}
				}
			});
		});
		await page.goto('/home');
		await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
		await expect(page.getByRole('button', { name: '건강 기록 다시 확인' })).toBeVisible();
		await expect(page).toHaveURL('/home');
	});
}
for (const post of ['FAIL', 'DISABLED']) {
	for (const code of ['CANDIY_USER_NOT_LINKED', 'HEALTH_DATA_UNAVAILABLE']) {
		test(`POST ${post} 후 GET ${code}는 기존 오류 안내를 유지한다`, async ({ page }) => {
			await page.route(endpoint, (route) =>
				post === 'DISABLED'
					? route.fulfill({ json: { data: { status: post } } })
					: route.fulfill({
							status: 503,
							json: { error: { code: 'HEALTH_SYNC_UNAVAILABLE', message: 'synthetic' } }
						})
			);
			await page.route('**/api/v1/health/metrics/*?*', (route) =>
				route.fulfill({
					status: code === 'CANDIY_USER_NOT_LINKED' ? 409 : 503,
					json: { error: { code, message: '합성 기존 조회 오류' } }
				})
			);
			await page.goto('/health');
			await expect(page.getByRole('alert').filter({ hasText: '합성 기존 조회 오류' })).toHaveCount(
				4
			);
			await expect(page.getByText('지금은 새 기록을 가져오지 않아요', { exact: true })).toHaveCount(
				0
			);
		});
	}
}
test('기간·날짜 선택은 GET만 수행하고 다른 화면 재진입은 새 POST를 수행한다', async ({ page }) => {
	let posts = 0;
	page.on('request', (r) => {
		if (r.url().endsWith('/health/sync')) posts++;
	});
	await page.goto('/health/heart');
	await expect(page.getByRole('figure')).toBeVisible();
	await page.getByRole('button', { name: '이전 주' }).click();
	await expect(page.getByRole('group', { name: '주간 기간 선택' })).toContainText('10.19');
	expect(posts).toBe(1);
	await page
		.getByRole('navigation', { name: '건강 데이터', exact: true })
		.getByRole('link', { name: '홈', exact: true })
		.click();
	await expect(page.locator('.health-summary-card')).toHaveCount(4);
	expect(posts).toBe(2);
});
test('POST 중 이탈한 화면의 늦은 응답은 새 화면 GET을 실행하지 않는다', async ({ page }) => {
	let release = () => {},
		acknowledge = () => {},
		posts = 0,
		gets = 0,
		homeGets = 0;
	const gate = new Promise<void>((resolve) => (release = resolve));
	const handled = new Promise<void>((resolve) => (acknowledge = resolve));
	await page.route(endpoint, async (route) => {
		const first = ++posts === 1;
		if (first) await gate;
		await route.fulfill({ json: { data: { status: 'SYNCED' } } });
		if (first) acknowledge();
	});
	page.on('request', (r) => {
		const url = new URL(r.url());
		if (r.method() !== 'GET' || !url.pathname.startsWith('/api/v1/health/')) return;
		gets++;
		if (
			url.pathname.endsWith('/daily-message') ||
			url.pathname.endsWith('/lifestyle-score') ||
			(url.pathname.startsWith('/api/v1/health/metrics/') &&
				url.searchParams.get('start') === '2026-10-26' &&
				url.searchParams.get('end') === '2026-10-26')
		)
			homeGets++;
	});
	try {
		await page.goto('/home');
		await expect.poll(() => posts).toBe(1);
		const canceled = page.waitForEvent('requestfailed', (request) =>
			request.url().endsWith('/health/sync')
		);
		const healthResponses = Promise.all(
			['steps', 'exercises', 'sleeps', 'heart_rates'].map((metric) =>
				page.waitForResponse((response) => {
					const url = new URL(response.url());
					return (
						response.request().method() === 'GET' &&
						response.status() === 200 &&
						url.pathname === `/api/v1/health/metrics/${metric}` &&
						url.searchParams.get('start') === '2026-10-20' &&
						url.searchParams.get('end') === '2026-10-26'
					);
				})
			)
		);
		await page
			.getByRole('navigation', { name: '주 메뉴' })
			.getByRole('link', { name: '건강 데이터' })
			.click();
		await canceled;
		await Promise.all((await healthResponses).map((response) => response.finished()));
		await expect(page.locator('.health-summary-card')).toHaveCount(4);
		await expect(page.locator('.health-summary-card[aria-busy="false"]')).toHaveCount(4);
		expect(gets).toBe(4);
		expect(homeGets).toBe(0);
		const count = gets;
		release();
		await handled;
		await expect(page.getByRole('heading', { level: 1, name: '건강 데이터' })).toBeVisible();
		expect(gets).toBe(count);
		expect(posts).toBe(2);
		expect(homeGets).toBe(0);
	} finally {
		release();
	}
});

test('동기화 중 로그아웃과 다른 계정 인증 복원 이후 이전 응답을 적용하지 않는다', async ({
	page
}) => {
	let release = () => {},
		acknowledge = () => {},
		posts = 0,
		gets = 0;
	const gate = new Promise<void>((resolve) => (release = resolve));
	const handled = new Promise<void>((resolve) => (acknowledge = resolve));
	await page.route(endpoint, async (route) => {
		const first = ++posts === 1;
		if (first) await gate;
		await route.fulfill({ json: { data: { status: 'SYNCED' } } });
		if (first) acknowledge();
	});
	await page.route('**/api/v1/auth/logout', (route) => route.fulfill({ status: 204 }));
	page.on('request', (r) => {
		if (r.method() === 'GET' && r.url().includes('/api/v1/health/')) gets++;
	});
	try {
		await page.goto('/home');
		await expect.poll(() => posts).toBe(1);
		const canceled = page.waitForEvent('requestfailed', (request) =>
			request.url().endsWith('/health/sync')
		);
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await page.getByRole('menuitem', { name: '로그아웃' }).click();
		await canceled;
		await expect(page).toHaveURL('/login');
		expect(gets).toBe(0);
		await page.route('**/api/v1/auth/session', (route) =>
			route.fulfill({
				json: {
					data: {
						...e2eSession,
						user: {
							...e2eSession.user,
							id: '00000000-0000-4000-8000-000000000002',
							displayName: '다른 합성 계정'
						}
					}
				}
			})
		);
		await page.goto('/home');
		await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
		release();
		await handled;
		await expect(page).toHaveURL('/home');
		expect(posts).toBe(2);
		expect(gets).toBe(5);
	} finally {
		release();
	}
});

test('후속 GET 무응답도 제한 시간에 기존 조회 오류와 재시도를 표시한다', async ({ page }) => {
	await page.clock.install({ time: new Date('2026-10-26T03:00:00Z') });
	let release = () => {},
		gets = 0;
	const gate = new Promise<void>((resolve) => (release = resolve));
	await page.route('**/api/v1/health/metrics/*?*', async (route) => {
		gets++;
		await gate;
		await route.abort();
	});
	try {
		await page.goto('/health');
		await expect.poll(() => gets).toBe(4);
		await page.clock.fastForward(15000);
		await expect(page.getByRole('alert')).toHaveCount(4);
		await expect(page.getByRole('button', { name: /다시 시도/ })).toHaveCount(4);
		await expect(page.getByText('기록을 불러오는 중이에요.')).toHaveCount(0);
	} finally {
		release();
	}
});
