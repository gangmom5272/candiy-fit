import { chromium, expect, test, type Page, type Route } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { e2eUser, mockSignedIn, SESSION_API } from '../../lib/testing/e2e-auth';
import {
	homeDailyMetricFixture,
	lifestyleScoreFixture,
	mockHomeApi
} from '../../lib/testing/home-api-fixture';
import {
	CONVERSATION_ID,
	coachingEvents,
	mockCoachingServer
} from '../../lib/testing/e2e-coaching';

// PUBLIC_APP_CONNECTION_GUIDE=true로 빌드한 preview에서 실행한다.
const STATUS_API = '**/api/v1/health/data-status';
const SYNC_API = '**/api/v1/health/sync';
const GUIDE_TITLE = '건강 기록을 보려면 먼저 앱 연동이 필요해요';
const ERROR_TITLE = '앱 연동 상태를 확인하지 못했어요';
const GUIDE_DOCUMENT_TITLE = '앱 연동 안내 | 캔디핏';
const ERROR_DOCUMENT_TITLE = '앱 연동 상태 확인 오류 | 캔디핏';
const LOADING_DOCUMENT_TITLE = '앱 연동 상태 확인 중 | 캔디핏';
const RECHECK = '연동 상태 다시 확인';
const CHECKING = '연동 상태 확인 중';
const CHECKING_NOTICE = '앱 연동 상태를 확인하고 있어요.';
const UNKNOWN_NOTICE =
	'앱 연동 완료 이력은 아직 확인되지 않았어요. 기존 화면은 계속 이용할 수 있어요. 앱에서 연동 상태를 확인할 수 있어요.';
const RETRYING_NOTICE = '연결이 원활하지 않아 앱 연동 상태를 다시 확인하고 있어요.';
const CLOSE_NOTICE = '앱 연동 상태 안내 닫기';
type ConnectionStatus = 'NOT_CONNECTED' | 'CONNECTED' | 'UNKNOWN';

function connectionResponse(status: ConnectionStatus) {
	return {
		data: {
			appConnection: {
				status,
				connectedAt: status === 'CONNECTED' ? '2026-10-26T02:00:00Z' : null
			},
			// 기록·집계의 준비 여부는 앱 설정 완료 이력과 별개다.
			firstRecord: { status: 'UNCONFIRMED', firstArrivedAt: null },
			metrics: [{ metric: 'steps', ready: false, lastSyncStatus: null }]
		}
	};
}

function fulfillConnection(route: Route, status: ConnectionStatus) {
	return route.fulfill({ json: connectionResponse(status) });
}

function preparationConnectionResponse(confirmed = false) {
	const arrivedAt = '2026-10-26T02:30:00Z';
	return {
		data: {
			...connectionResponse('CONNECTED').data,
			firstRecord: {
				status: confirmed ? 'CONFIRMED' : 'UNCONFIRMED',
				firstArrivedAt: confirmed ? arrivedAt : null
			},
			metrics: ['steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercises'].map(
				(metric) => ({
					metric,
					ready: confirmed ? true : null,
					lastSyncStatus: confirmed ? 'success' : null,
					lastDataAt: confirmed ? arrivedAt : null,
					lastSuccessAt: confirmed ? arrivedAt : null
				})
			)
		}
	};
}

function fulfillPreparing(route: Route) {
	return route.fulfill({
		status: 503,
		json: {
			error: {
				code: 'HEALTH_DATA_UNAVAILABLE',
				message: '건강데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.',
				context: { reason: 'DATA_NOT_READY', metrics: [] }
			}
		}
	});
}

function deferred() {
	let resolve!: () => void;
	const promise = new Promise<void>((done) => (resolve = done));
	return { promise, resolve };
}

function trackRequests(page: Page) {
	const calls = {
		connection: [] as string[],
		posts: [] as string[],
		healthReads: [] as string[],
		coaching: [] as string[],
		webSockets: [] as string[]
	};
	page.on('websocket', (socket) => calls.webSockets.push(socket.url()));
	page.on('request', (request) => {
		const { pathname } = new URL(request.url());
		if (pathname === '/api/v1/health/data-status') calls.connection.push(request.url());
		else if (pathname === '/api/v1/health/sync') calls.posts.push(request.url());
		else if (request.method() === 'GET' && pathname.startsWith('/api/v1/health/'))
			calls.healthReads.push(request.url());
		else if (pathname.startsWith('/api/v1/coaching/')) calls.coaching.push(request.url());
	});
	return calls;
}

async function documentTitle(page: Page, title: string) {
	await expect(page).toHaveTitle(title);
	await expect(page.locator('head > title')).toHaveCount(1);
}

async function homeLoaded(page: Page) {
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	await expect(
		page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
	).toHaveText(['3,200보', '18분', '7시간 0분', '64.5 bpm']);
	await expect(
		page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
	).toBeVisible();
}

async function resumeEvents(page: Page) {
	// 같은 복귀에서 연속으로 발생하는 두 브라우저 이벤트를 검증한다.
	await page.evaluate(() => {
		window.dispatchEvent(new Event('focus'));
		document.dispatchEvent(new Event('visibilitychange'));
	});
}

async function expectGuideActionReachable(page: Page) {
	const button = page.getByRole('button', { name: RECHECK, exact: true });
	await button.evaluate((element) => element.scrollIntoView({ block: 'center' }));
	await expect(button).toBeInViewport({ ratio: 1 });
	const position = await button.evaluate((element) => {
		const bounds = element.getBoundingClientRect();
		const header = document.querySelector('header')?.getBoundingClientRect();
		const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
		const navigationTop =
			navigation && getComputedStyle(navigation).position === 'fixed'
				? navigation.getBoundingClientRect().top
				: innerHeight;
		const point = document.elementFromPoint(
			bounds.left + bounds.width / 2,
			bounds.top + bounds.height / 2
		);
		return {
			top: bounds.top,
			bottom: bounds.bottom,
			headerBottom: header?.bottom,
			navigationTop,
			uncovered: point === element || (point !== null && element.contains(point))
		};
	});
	expect(position.headerBottom).toBeDefined();
	expect(position.top).toBeGreaterThanOrEqual(position.headerBottom ?? Infinity);
	expect(position.bottom).toBeLessThanOrEqual(position.navigationTop);
	expect(position.uncovered).toBe(true);
	return button;
}

test.beforeEach(async ({ page }) => {
	await page.setViewportSize({ width: 1366, height: 768 });
	await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const path of ['/home', '/health', '/coaching', `/coaching/${CONVERSATION_ID}`]) {
	test(`${path}: 신규 회원 NOT_CONNECTED는 공통 안내를 표시하고 서비스 요청을 시작하지 않는다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		await page.route(STATUS_API, (route) => {
			const request = route.request();
			expect(request.method()).toBe('GET');
			expect(request.postData()).toBeNull();
			expect(new URL(request.url()).search).toBe('');
			expect(request.headers().authorization).toBeUndefined();
			return fulfillConnection(route, 'NOT_CONNECTED');
		});
		await page.goto(path);
		await expect(page.getByRole('heading', { level: 1, name: GUIDE_TITLE })).toBeVisible();
		await documentTitle(page, GUIDE_DOCUMENT_TITLE);
		await expect(page.getByRole('banner')).toHaveCount(1);
		await expect(page.getByRole('navigation', { name: '주 메뉴' })).toBeVisible();
		await expect(page.getByRole('button', { name: RECHECK, exact: true })).toBeEnabled();
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await expect(page.getByRole('menuitem', { name: '마이페이지' })).toHaveAttribute('href', '/my');
		await expect(page.getByRole('menuitem', { name: '로그아웃', exact: true })).toBeEnabled();
		expect(calls.connection).toHaveLength(1);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		expect(calls.coaching).toHaveLength(0);
		expect(calls.webSockets).toHaveLength(0);
	});
}

for (const [width, height] of [
	[1920, 1080],
	[1440, 900],
	[1366, 768],
	[390, 844],
	[375, 667],
	[320, 568],
	[1366, 480]
]) {
	test(`${width}×${height}: 원본 단계 카드와 하단 안내·재확인을 유지한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		const calls = trackRequests(page);
		await page.route(STATUS_API, (route) => fulfillConnection(route, 'NOT_CONNECTED'));
		await page.goto('/home');
		const panel = page.getByRole('region', { name: GUIDE_TITLE, exact: true });
		const button = panel.getByRole('button', { name: RECHECK, exact: true });
		await expect(button).toBeEnabled();
		await page.evaluate(async () => {
			await document.fonts.ready;
			await Promise.all(
				Array.from(document.querySelectorAll('main img')).map((img) =>
					img instanceof HTMLImageElement ? img.decode() : undefined
				)
			);
		});
		const steps = panel.getByRole('list', { name: '앱 연동 3단계' }).getByRole('listitem');
		await expect(steps).toHaveCount(3);
		for (const [index, label] of ['STEP 01', 'STEP 02', 'STEP 03'].entries())
			await expect(steps.nth(index).getByText(label, { exact: true })).toBeVisible();
		await expect(steps.getByRole('button')).toHaveCount(0);
		const lastStep = await steps.last().boundingBox();
		const note = panel.getByText('첫 건강 기록이 표시되기까지는 시간이 조금 걸릴 수 있어요.', {
			exact: true
		});
		await expect(
			note.locator('..').getByRole('button', { name: RECHECK, exact: true })
		).toHaveCount(1);
		const noteBounds = await note.boundingBox();
		const actionBounds = await button.boundingBox();
		expect(lastStep).not.toBeNull();
		expect(noteBounds).not.toBeNull();
		expect(actionBounds).not.toBeNull();
		expect(await button.evaluate((element) => element.closest('ol'))).toBeNull();
		expect(
			await note.evaluate((element) => {
				const action = element.parentElement?.querySelector('button');
				return (
					action !== null &&
					action !== undefined &&
					Boolean(element.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING)
				);
			})
		).toBe(true);
		expect(noteBounds?.y).toBeGreaterThanOrEqual(
			(lastStep?.y ?? Infinity) + (lastStep?.height ?? 0)
		);
		expect(actionBounds?.height).toBeGreaterThanOrEqual(44);
		if (width >= 1024) {
			expect(actionBounds?.x).toBeGreaterThanOrEqual(
				(noteBounds?.x ?? Infinity) + (noteBounds?.width ?? 0)
			);
			expect((actionBounds?.y ?? 0) + (actionBounds?.height ?? 0) / 2).toBeCloseTo(
				(noteBounds?.y ?? Infinity) + (noteBounds?.height ?? 0) / 2,
				1
			);
			const cards = await steps.evaluateAll((elements) =>
				elements.map((element) => {
					const bounds = element.getBoundingClientRect();
					const icon = element.querySelector('img');
					const paragraphs = element.querySelectorAll(':scope > p');
					const heading = element.querySelector('h2');
					if (!icon || !paragraphs[0] || !paragraphs[1] || !heading)
						throw new Error('단계 카드의 아이콘·번호·제목·설명이 필요합니다.');
					return {
						width: bounds.width,
						height: bounds.height,
						iconTop: icon.getBoundingClientRect().top,
						stepTop: paragraphs[0].getBoundingClientRect().top,
						headingTop: heading.getBoundingClientRect().top,
						descriptionTop: paragraphs[1].getBoundingClientRect().top
					};
				})
			);
			for (const card of cards.slice(1)) {
				// 동일한 fr 열도 브라우저의 소수 픽셀 배분에 따라 1/64px 차이가 날 수 있다.
				expect(card.width).toBeCloseTo(cards[0].width, 1);
				expect(card.height).toBe(cards[0].height);
				for (const position of ['iconTop', 'stepTop', 'headingTop', 'descriptionTop'] as const)
					expect(card[position]).toBe(cards[0][position]);
			}
		} else {
			expect(actionBounds?.y).toBeGreaterThanOrEqual(
				(noteBounds?.y ?? Infinity) + (noteBounds?.height ?? 0)
			);
		}
		const introduction = await panel
			.getByText(
				'모바일 앱에서 워치와 건강 데이터를 연동하면 웹에서도 건강 기록을 확인할 수 있어요.',
				{
					exact: true
				}
			)
			.boundingBox();
		const illustration = panel.locator('img').first();
		const illustrationBounds = await illustration.boundingBox();
		const panelBounds = await panel.boundingBox();
		const firstStep = await steps.first().boundingBox();
		expect(introduction).not.toBeNull();
		expect(illustrationBounds).not.toBeNull();
		expect(panelBounds).not.toBeNull();
		expect(firstStep).not.toBeNull();
		expect(illustrationBounds?.y).toBeGreaterThanOrEqual(
			(introduction?.y ?? Infinity) + (introduction?.height ?? 0)
		);
		expect(
			(illustrationBounds?.y ?? Infinity) + (illustrationBounds?.height ?? 0)
		).toBeLessThanOrEqual(firstStep?.y ?? -Infinity);
		expect((illustrationBounds?.x ?? 0) + (illustrationBounds?.width ?? 0) / 2).toBeCloseTo(
			(panelBounds?.x ?? Infinity) + (panelBounds?.width ?? 0) / 2,
			2
		);
		expect((illustrationBounds?.width ?? 0) / (illustrationBounds?.height ?? 1)).toBeCloseTo(
			2400 / 1020,
			2
		);
		await expect(panel.getByRole('heading', { level: 1, name: GUIDE_TITLE })).toHaveCSS(
			'font-size',
			'30px'
		);
		const dimensions = await page.evaluate(() => ({
			width: innerWidth,
			height: innerHeight,
			documentWidth: document.documentElement.scrollWidth,
			documentHeight: document.documentElement.scrollHeight,
			scrollY
		}));
		expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.width);
		if (width >= 1024 && height >= 768) {
			expect(dimensions.scrollY).toBe(0);
			expect(dimensions.documentHeight).toBeLessThanOrEqual(dimensions.height);
			const originalImageWidths: Record<number, number> = { 1366: 448, 1440: 580, 1920: 600 };
			expect(illustrationBounds?.width).toBeCloseTo(originalImageWidths[width], 2);
			expect((actionBounds?.y ?? Infinity) + (actionBounds?.height ?? 0)).toBeLessThanOrEqual(
				height
			);
		} else {
			expect(dimensions.documentHeight).toBeGreaterThan(dimensions.height);
		}
		for (const element of await panel.locator('h1, h2, p, li').all()) {
			const content = await element.evaluate((node) => ({
				width: node.clientWidth,
				scrollWidth: node.scrollWidth,
				height: node.clientHeight,
				scrollHeight: node.scrollHeight
			}));
			expect(content.scrollWidth).toBeLessThanOrEqual(content.width);
			expect(content.scrollHeight).toBeLessThanOrEqual(content.height);
		}
		await expectGuideActionReachable(page);
		expect(calls.connection).toHaveLength(1);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		await testInfo.attach('guide-layout', {
			body: JSON.stringify(
				{ dimensions, actionBounds, lastStep, noteBounds, illustrationBounds },
				null,
				2
			),
			contentType: 'application/json'
		});
		await page.screenshot({
			path: testInfo.outputPath(`423-guide-${width}-${height}.png`),
			fullPage: true
		});
	});
}

test('미연동 카드의 재확인은 대기 중 중복 요청을 막고 실패 뒤 다시 확인하여 정상 홈으로 전환한다', async ({
	page
}) => {
	const calls = trackRequests(page);
	const recheck = deferred();
	let attempt = 0;
	await page.route(STATUS_API, async (route) => {
		const current = ++attempt;
		if (current === 1) return fulfillConnection(route, 'NOT_CONNECTED');
		if (current === 2) {
			await recheck.promise;
			return route.fulfill({
				status: 500,
				json: { error: { code: 'INTERNAL_ERROR', message: 'synthetic' } }
			});
		}
		return fulfillConnection(route, 'CONNECTED');
	});
	try {
		await page.goto('/home');
		const panel = page.getByRole('region', { name: GUIDE_TITLE, exact: true });
		const button = panel.getByRole('button', { name: RECHECK, exact: true });
		await expect(button).toBeEnabled();
		await button.focus();
		await page.keyboard.press('Enter');
		await expect.poll(() => calls.connection.length).toBe(2);
		const checking = panel.getByRole('button', { name: CHECKING, exact: true });
		await expect(checking).toBeDisabled();
		await checking.evaluate((node) => {
			if (!(node instanceof HTMLButtonElement)) throw new Error('재확인 버튼이 없습니다.');
			node.click();
			node.click();
		});
		await page.keyboard.press('Enter');
		await page.keyboard.press('Space');
		expect(calls.connection).toHaveLength(2);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		recheck.resolve();
		await expect(panel.getByRole('alert')).toHaveText(
			'앱 연동 상태를 확인하지 못했어요. 마지막으로 확인한 상태를 유지하고 있어요.'
		);
		await expect(button).toBeEnabled();
		await documentTitle(page, GUIDE_DOCUMENT_TITLE);
		expect(calls.connection).toHaveLength(2);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		await button.focus();
		await page.keyboard.press('Space');
		await homeLoaded(page);
		await expect(panel).toHaveCount(0);
		await documentTitle(page, '홈 | 캔디핏');
		expect(calls.connection).toHaveLength(3);
		expect(calls.posts).toHaveLength(1);
		expect(calls.healthReads).toHaveLength(5);
	} finally {
		recheck.resolve();
	}
});

test('실제 Chromium 200% 확대에서도 미연동 재확인 버튼에 접근하여 요청할 수 있다', async ({
	baseURL
}, testInfo) => {
	const extensionPath = testInfo.outputPath('zoom-extension');
	mkdirSync(extensionPath, { recursive: true });
	writeFileSync(
		`${extensionPath}/manifest.json`,
		JSON.stringify({
			manifest_version: 3,
			name: 'App connection guide zoom regression',
			version: '1.0',
			permissions: ['tabs'],
			background: { service_worker: 'worker.js' }
		})
	);
	writeFileSync(`${extensionPath}/worker.js`, 'chrome.runtime.onInstalled.addListener(() => {});');
	const context = await chromium.launchPersistentContext(testInfo.outputPath('browser-profile'), {
		channel: 'chromium',
		headless: true,
		viewport: { width: 1366, height: 768 },
		args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]
	});
	try {
		const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
		const page = await context.newPage();
		await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
		await mockSignedIn(page);
		await mockHomeApi(page);
		const calls = trackRequests(page);
		await page.route(STATUS_API, (route) => fulfillConnection(route, 'NOT_CONNECTED'));
		await page.goto(new URL('/home', baseURL).href);
		const button = page.getByRole('button', { name: RECHECK, exact: true });
		await expect(button).toBeEnabled();
		await page.evaluate(() => document.fonts.ready);
		const before = await button.evaluate((node) => ({
			dpr: devicePixelRatio,
			fontSize: parseFloat(getComputedStyle(node).fontSize),
			height: node.getBoundingClientRect().height
		}));
		const zoom = await worker.evaluate(async (url) => {
			// 실제 Chromium 페이지 확대를 사용하며 CSS zoom이나 핀치 확대와 구분한다.
			const { chrome } = globalThis as typeof globalThis & {
				chrome: {
					tabs: {
						query(query: Record<string, never>): Promise<{ id?: number; url?: string }[]>;
						setZoom(id: number, factor: number): Promise<void>;
						getZoom(id: number): Promise<number>;
					};
				};
			};
			const tab = (await chrome.tabs.query({})).find((tab) => tab.url === url);
			if (tab?.id === undefined) throw new Error('확대할 Chromium 탭이 없습니다.');
			await chrome.tabs.setZoom(tab.id, 2);
			return chrome.tabs.getZoom(tab.id);
		}, page.url());
		expect(zoom).toBe(2);
		await expect.poll(() => page.evaluate(() => devicePixelRatio)).toBe(before.dpr * 2);
		const after = await button.evaluate((node) => ({
			dpr: devicePixelRatio,
			fontSize: parseFloat(getComputedStyle(node).fontSize),
			height: node.getBoundingClientRect().height,
			width: innerWidth,
			viewportHeight: innerHeight,
			documentWidth: document.documentElement.scrollWidth,
			documentHeight: document.documentElement.scrollHeight,
			cssZoom: getComputedStyle(document.documentElement).zoom,
			visualScale: visualViewport?.scale
		}));
		expect(after.cssZoom).toBe('1');
		expect(after.visualScale).toBe(1);
		expect(after.width).toBe(683);
		expect(after.documentWidth).toBeLessThanOrEqual(after.width);
		expect(after.documentHeight).toBeGreaterThan(after.viewportHeight);
		expect(after.fontSize * after.dpr).toBeGreaterThanOrEqual(before.fontSize * before.dpr * 2);
		expect(after.height * after.dpr).toBeGreaterThanOrEqual(before.height * before.dpr * 2);
		await expectGuideActionReachable(page);
		await testInfo.attach('real-page-zoom', {
			body: JSON.stringify({ before, zoom, after }, null, 2),
			contentType: 'application/json'
		});
		await page.screenshot({
			path: testInfo.outputPath('423-guide-200-percent.png'),
			fullPage: true
		});
		await button.focus();
		await page.keyboard.press('Enter');
		await expect.poll(() => calls.connection.length).toBe(2);
		await expect(button).toBeEnabled();
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
	} finally {
		await context.close();
	}
});

for (const status of ['CONNECTED', 'UNKNOWN'] as const) {
	for (const path of ['/home', '/health']) {
		test(`${path} ${status}: 인증 → 최초 연결 상태 → POST 응답 → 건강 GET 순서를 보존한다`, async ({
			page
		}) => {
			const connection = deferred();
			const sync = deferred();
			const events: string[] = [];
			const calls = trackRequests(page);
			page.on('request', (request) => {
				const { pathname } = new URL(request.url());
				if (pathname.endsWith('/auth/session')) events.push('auth');
				else if (pathname.endsWith('/health/data-status')) events.push('connection');
				else if (pathname.endsWith('/health/sync')) events.push('post');
				else if (request.method() === 'GET' && pathname.startsWith('/api/v1/health/'))
					events.push('get');
			});
			await page.route(STATUS_API, async (route) => {
				await connection.promise;
				await fulfillConnection(route, status);
			});
			await page.route(SYNC_API, async (route) => {
				await sync.promise;
				await route.fulfill({ json: { data: { status: 'SYNCED' } } });
			});
			try {
				await page.goto(path);
				await expect(
					page.getByText('앱 연동 상태를 확인하고 있어요.', { exact: true })
				).toBeVisible();
				await documentTitle(page, LOADING_DOCUMENT_TITLE);
				expect(events).toEqual(['auth', 'connection']);
				expect(calls.posts).toHaveLength(0);
				expect(calls.healthReads).toHaveLength(0);
				connection.resolve();
				await expect.poll(() => calls.posts.length).toBe(1);
				expect(events).toEqual(['auth', 'connection', 'post']);
				expect(calls.healthReads).toHaveLength(0);
				await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
				if (status === 'UNKNOWN') await expect(page.getByText(UNKNOWN_NOTICE)).toBeVisible();
				sync.resolve();
				if (path === '/home') await homeLoaded(page);
				else
					await expect(page.locator('.health-summary-card .health-summary-link[href]')).toHaveCount(
						4
					);
				await documentTitle(page, path === '/home' ? '홈 | 캔디핏' : '건강 데이터 | 캔디핏');
				await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toHaveCount(0);
				expect(events.slice(0, 4)).toEqual(['auth', 'connection', 'post', 'get']);
				expect(calls.posts).toHaveLength(1);
			} finally {
				connection.resolve();
				sync.resolve();
			}
		});
	}
}

test('CONNECTED + UNSYNCED 집계 대기는 앱 미연동 안내로 되돌리지 않고 기존 결측을 표시한다', async ({
	page
}) => {
	await page.route(STATUS_API, (route) => fulfillConnection(route, 'CONNECTED'));
	const score = lifestyleScoreFixture();
	Object.assign(score, {
		reason: 'UNSYNCED',
		computedAt: null,
		total: { score: null, missingAreas: ['activity', 'sleep', 'heart'] },
		areas: {
			activity: {
				score: null,
				grade: null,
				steps: { score: null, value: null },
				exercise: { score: null, minutes: null }
			},
			sleep: { score: null, grade: null, sleepMinutes: null },
			heart: {
				score: null,
				grade: null,
				sleepHrAvg: null,
				baselineHrAvg: null,
				baselineNights: null,
				checkWear: null
			}
		}
	});
	await page.route('**/api/v1/health/lifestyle-score?*', (route) =>
		route.fulfill({ json: { data: score } })
	);
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const metric = new URL(route.request().url()).pathname.split('/').at(-1);
		if (metric !== 'steps' && metric !== 'exercises' && metric !== 'sleeps')
			return route.fallback();
		const data = homeDailyMetricFixture(metric);
		for (const day of data.days)
			Object.assign(day, { value: null, missing: true, reason: 'UNSYNCED' });
		Object.assign(data.summary, {
			avg: null,
			total: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			unsyncedDays: data.days.map((day) => day.date)
		});
		Object.assign(data.detail, { nights: [], dailyRanges: [], sessions: [] });
		if (data.inProgress) Object.assign(data.inProgress, { value: null, missing: true, asOf: null });
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	await expect(page.getByLabel('오늘의 점수 동기화 대기', { exact: true })).toBeVisible();
	await expect(
		page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
	).toHaveText(['동기화 대기', '동기화 대기', '동기화 대기', '동기화 대기']);
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
});

test('CONNECTED + NO_RECORD는 앱 미연동 안내나 동기화 대기로 바꾸지 않고 기록 없음을 표시한다', async ({
	page
}) => {
	const calls = trackRequests(page);
	await page.route(STATUS_API, (route) =>
		route.fulfill({
			json: {
				data: {
					...connectionResponse('CONNECTED').data,
					firstRecord: { status: 'CONFIRMED', firstArrivedAt: '2026-10-25T02:00:00Z' },
					metrics: [{ metric: 'steps', ready: true, lastSyncStatus: 'success' }]
				}
			}
		})
	);
	const score = lifestyleScoreFixture();
	Object.assign(score, {
		reason: 'NO_RECORD',
		computedAt: null,
		total: { score: null, missingAreas: ['activity', 'sleep', 'heart'] },
		areas: {
			activity: {
				score: null,
				grade: null,
				steps: { score: null, value: null },
				exercise: { score: null, minutes: null }
			},
			sleep: { score: null, grade: null, sleepMinutes: null },
			heart: {
				score: null,
				grade: null,
				sleepHrAvg: null,
				baselineHrAvg: null,
				baselineNights: null,
				checkWear: null
			}
		}
	});
	await page.route('**/api/v1/health/lifestyle-score?*', (route) =>
		route.fulfill({ json: { data: score } })
	);
	await page.route('**/api/v1/health/metrics/*?*', (route) => {
		const metric = new URL(route.request().url()).pathname.split('/').at(-1);
		if (metric !== 'steps' && metric !== 'exercises' && metric !== 'sleeps')
			return route.fallback();
		const data = homeDailyMetricFixture(metric);
		for (const day of data.days)
			Object.assign(day, { value: null, missing: true, reason: 'NO_RECORD' });
		Object.assign(data.dataStatus, { ready: true });
		Object.assign(data.summary, {
			avg: null,
			total: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			missingDays: data.days.map((day) => day.date),
			unsyncedDays: []
		});
		Object.assign(data.detail, { nights: [], dailyRanges: [], sessions: [] });
		if (data.inProgress) Object.assign(data.inProgress, { value: null, missing: true, asOf: null });
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	await expect(page.getByLabel('오늘의 점수 기록 없음', { exact: true })).toBeVisible();
	await expect(
		page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
	).toHaveText(['기록 없음', '기록 없음', '기록 없음', '기록 없음']);
	await expect(
		page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
	).toBeVisible();
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
	await expect(page.getByRole('alert')).toHaveCount(0);
	expect(calls.connection).toHaveLength(1);
	expect(calls.posts).toHaveLength(1);
	expect(calls.healthReads).toHaveLength(5);
});

for (const status of ['CONNECTED', 'UNKNOWN'] as const) {
	test(`${status}: AI 코치 질문과 답변은 내부 대화 이동 후에도 같은 연결 상태를 유지한다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		const completion = deferred();
		const questionText = '오늘은 어떤 활동을 하면 좋을까?';
		const partialAnswer = '오늘 건강 기록을 확인했어요.';
		const answer = `${partialAnswer} 가볍게 걸어 보세요.`;
		await page.route(STATUS_API, (route) => fulfillConnection(route, status));
		const questions = await mockCoachingServer(page, async (question, { send }) => {
			const events = coachingEvents(question);
			send(events.accepted());
			send(events.status('fetching_health_data'));
			send(events.delta(partialAnswer));
			await completion.promise;
			send(events.completed(answer));
		});
		try {
			await page.goto('/coaching', { waitUntil: 'networkidle' });
			if (status === 'UNKNOWN') await expect(page.getByText(UNKNOWN_NOTICE)).toBeVisible();
			await page.getByRole('textbox', { name: '질문 입력' }).fill(questionText);
			await page.getByRole('button', { name: '질문 보내기' }).click();
			await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
			const log = page.getByRole('log', { name: '대화 내용' });
			await expect(log.getByText(questionText, { exact: true })).toBeVisible();
			await expect(log.getByText(partialAnswer, { exact: true })).toBeVisible();
			await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeDisabled();
			completion.resolve();
			await expect(log.getByText(answer, { exact: true })).toBeVisible();
			await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeEnabled();
			await expect(page).toHaveTitle('AI 코치 | 캔디핏');
			await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
			expect(questions).toHaveLength(1);
			expect(questions[0]).toMatchObject({ conversationId: null, content: questionText });
			expect(calls.connection).toHaveLength(1);
			expect(calls.posts).toHaveLength(0);
			expect(calls.healthReads).toHaveLength(0);
		} finally {
			completion.resolve();
		}
	});
}

for (const code of ['HEALTH_DATA_UNAVAILABLE', 'CANDIY_USER_NOT_LINKED']) {
	test(`CONNECTED + 기존 건강 GET ${code}는 연결 완료 이력을 지우지 않는다`, async ({ page }) => {
		await page.route('**/api/v1/health/**', (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			return route.fulfill({
				status: code === 'CANDIY_USER_NOT_LINKED' ? 409 : 503,
				json: {
					error: {
						code,
						message: 'synthetic',
						context: { reason: 'DATA_NOT_READY', metrics: [] }
					}
				}
			});
		});
		await page.route(STATUS_API, (route) => fulfillConnection(route, 'CONNECTED'));
		await page.goto('/home');
		if (code === 'HEALTH_DATA_UNAVAILABLE') {
			await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toBeVisible();
			await expect(page.getByText('첫 건강데이터가 도착하기를 기다리고 있어요.')).toBeVisible();
			await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
			await expect(
				page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
			).toHaveText(['준비 중', '준비 중', '준비 중', '준비 중']);
			await expect(page.getByRole('alert')).toHaveCount(0);
			await expect(page.locator('main')).not.toContainText('조회 실패');
		} else {
			await expect(page.getByLabel('오늘의 점수 조회 실패', { exact: true })).toBeVisible();
			await expect(
				page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
			).toHaveText(['조회 실패', '조회 실패', '조회 실패', '조회 실패']);
			await expect(
				page.getByRole('alert').filter({ hasText: '이미 연동했다면 첫 데이터가 준비될 때까지' })
			).toHaveCount(4);
		}
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
	});
}

for (const width of [390, 1366]) {
	test(`${width}px: 연동 완료·첫 기록 대기에서 수동 재확인하면 실제 첫 데이터로 전환한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: 844 });
		const calls = trackRequests(page);
		const recheck = deferred();
		let arrived = false;
		await page.route('**/api/v1/health/**', (route) =>
			route.request().method() === 'GET' && !arrived ? fulfillPreparing(route) : route.fallback()
		);
		await page.route(STATUS_API, async (route) => {
			if (calls.connection.length === 2) await recheck.promise;
			await route.fulfill({ json: preparationConnectionResponse(arrived) });
		});
		try {
			await page.goto('/home');
			await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toBeVisible();
			await expect(page.getByText('첫 건강데이터가 도착하기를 기다리고 있어요.')).toBeVisible();
			await expect(
				page.getByText('앱에서 건강 기록 수집 상태를 확인하고 잠시 후 다시 확인해 주세요.')
			).toBeVisible();
			await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
			await expect(
				page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
			).toHaveText(['준비 중', '준비 중', '준비 중', '준비 중']);
			await expect(page.locator('main')).not.toContainText('조회 실패');
			await expect(page.locator('.score-denominator')).toHaveCount(0);
			await expect(page.getByRole('alert')).toHaveCount(0);
			expect(calls.connection).toHaveLength(1);
			expect(calls.posts).toHaveLength(1);
			expect(calls.healthReads).toHaveLength(5);
			const button = page.getByRole('button', { name: '건강데이터 다시 확인', exact: true });
			await expect(button).toBeEnabled();
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			await page.screenshot({
				path: testInfo.outputPath(`422-preparing-${width}.png`),
				fullPage: true
			});
			arrived = true;
			await button.click();
			await expect.poll(() => calls.connection.length).toBe(2);
			await expect(button).toBeDisabled();
			await button.evaluate((node) => {
				if (!(node instanceof HTMLButtonElement)) throw new Error('재확인 버튼이 없습니다.');
				node.click();
				node.click();
			});
			expect(calls.connection).toHaveLength(2);
			expect(calls.posts).toHaveLength(1);
			expect(calls.healthReads).toHaveLength(5);
			recheck.resolve();
			await homeLoaded(page);
			await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toHaveCount(0);
			await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toHaveCount(
				0
			);
			expect(calls.connection).toHaveLength(2);
			expect(calls.posts).toHaveLength(2);
			expect(calls.healthReads).toHaveLength(10);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			await page.screenshot({
				path: testInfo.outputPath(`422-confirmed-${width}.png`),
				fullPage: true
			});
		} finally {
			recheck.resolve();
		}
	});
}

test('첫 기록 대기는 새로고침·건강 화면 이동·홈 재진입마다 실제 응답을 확인한다', async ({
	page
}) => {
	const calls = trackRequests(page);
	await page.route('**/api/v1/health/**', (route) =>
		route.request().method() === 'GET' ? fulfillPreparing(route) : route.fallback()
	);
	await page.route(STATUS_API, (route) => route.fulfill({ json: preparationConnectionResponse() }));
	await page.goto('/home');
	await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
	await page.reload();
	await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '건강 데이터', exact: true })
		.click();
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toBeVisible();
	await expect(page.locator('main')).not.toContainText('조회 실패');
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '홈', exact: true })
		.click();
	await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
	expect(calls.healthReads.filter((path) => path.includes('/lifestyle-score?'))).toHaveLength(3);
});

for (const firstRecord of ['CONFIRMED', 'UNCONFIRMED']) {
	test(`${firstRecord} + 일부 집계 준비 중에도 실제 지표가 있으면 첫 데이터 미도착으로 단정하지 않는다`, async ({
		page
	}) => {
		await page.route(STATUS_API, (route) =>
			route.fulfill({ json: preparationConnectionResponse(firstRecord === 'CONFIRMED') })
		);
		await page.route('**/api/v1/health/lifestyle-score?*', fulfillPreparing);
		await page.goto('/home');
		await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toBeVisible();
		await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toHaveCount(0);
		await expect(page.getByText('첫 건강데이터가 도착하기를 기다리고 있어요.')).toHaveCount(0);
		await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: '운동 시간 18분', exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: '수면 7시간 0분', exact: true })).toBeVisible();
		await expect(page.getByRole('link', { name: /안정 시 심박수 준비 중/ })).toBeVisible();
	});
}

test('총점이 없어도 실제 심박이 도착했으면 첫 기록 미도착 안내를 표시하지 않는다', async ({
	page
}) => {
	await page.route(STATUS_API, (route) => route.fulfill({ json: preparationConnectionResponse() }));
	await page.route('**/api/v1/health/metrics/steps?*', fulfillPreparing);
	await page.route('**/api/v1/health/metrics/sleeps?*', fulfillPreparing);
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const score = lifestyleScoreFixture();
		score.total = { score: null, missingAreas: ['activity', 'sleep', 'heart'] };
		score.areas.activity = {
			score: null,
			grade: null,
			steps: { score: null, value: null },
			exercise: { score: null, minutes: null }
		};
		score.areas.sleep = { score: null, grade: null, sleepMinutes: null };
		score.areas.heart = {
			score: null,
			grade: null,
			sleepHrAvg: 72,
			baselineHrAvg: null,
			baselineNights: 0,
			checkWear: false
		};
		return route.fulfill({ json: { data: score } });
	});
	await page.goto('/home');
	await expect(
		page.getByRole('link', { name: '안정 시 심박수 72 bpm', exact: true })
	).toBeVisible();
	await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toBeVisible();
	await expect(page.getByText('첫 건강데이터가 도착하기를 기다리고 있어요.')).toHaveCount(0);
});

for (const failure of ['LOOKUP_FAILED', '503', 'network']) {
	test(`CONNECTED + UNCONFIRMED의 ${failure}를 첫 기록 준비 상태로 바꾸지 않는다`, async ({
		page
	}) => {
		await page.route('**/api/v1/health/**', (route) => {
			if (route.request().method() !== 'GET') return route.fallback();
			return failure === 'network'
				? route.abort('failed')
				: route.fulfill({
						status: 503,
						json: {
							error: {
								code: 'HEALTH_DATA_UNAVAILABLE',
								message: '조회할 수 없습니다.',
								context: failure === 'LOOKUP_FAILED' ? { reason: failure, metrics: [] } : null
							}
						}
					});
		});
		await page.route(STATUS_API, (route) =>
			route.fulfill({ json: preparationConnectionResponse() })
		);
		await page.goto('/home');
		await expect(page.getByLabel('오늘의 점수 조회 실패', { exact: true })).toBeVisible();
		await expect(
			page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
		).toHaveText(['조회 실패', '조회 실패', '조회 실패', '조회 실패']);
		await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toHaveCount(0);
		await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toHaveCount(0);
		await expect(
			page.getByRole('button', { name: '건강데이터 다시 확인', exact: true })
		).toHaveCount(0);
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
	});
}

for (const failure of ['503', 'missing', 'invalid', 'network'] as const) {
	test(`최초 연결 조회 ${failure}는 미연동으로 오판하지 않고 명시적으로 재시도한다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		const failedAttempts = failure === '503' || failure === 'network' ? 2 : 1;
		let attempt = 0;
		await page.route(STATUS_API, (route) => {
			if (++attempt > failedAttempts) return fulfillConnection(route, 'NOT_CONNECTED');
			if (failure === 'network') return route.abort();
			if (failure === 'missing') return route.fulfill({ json: { data: {} } });
			if (failure === 'invalid')
				return route.fulfill({
					json: { data: { appConnection: { status: 'NOT_CONNECTED', connectedAt: 'invalid' } } }
				});
			return route.fulfill({
				status: 503,
				json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
			});
		});
		await page.goto('/home');
		await expect(page.getByRole('heading', { level: 1, name: ERROR_TITLE })).toBeVisible();
		await documentTitle(page, ERROR_DOCUMENT_TITLE);
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
		expect(calls.connection).toHaveLength(failedAttempts);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		await page.getByRole('button', { name: RECHECK, exact: true }).click();
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toBeVisible();
		await documentTitle(page, GUIDE_DOCUMENT_TITLE);
		expect(calls.connection).toHaveLength(failedAttempts + 1);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
	});
}

for (const failure of ['503', 'network'] as const) {
	test(`최초 ${failure}는 한 번만 자동 재시도하여 CONNECTED 기존 흐름으로 복구한다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		const retry = deferred();
		let attempt = 0;
		await page.route(STATUS_API, async (route) => {
			if (++attempt === 1) {
				if (failure === 'network') return route.abort();
				return route.fulfill({
					status: 503,
					json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
				});
			}
			await retry.promise;
			await fulfillConnection(route, 'CONNECTED');
		});
		try {
			await page.goto('/home');
			await expect(page.getByRole('status').filter({ hasText: RETRYING_NOTICE })).toBeVisible();
			await documentTitle(page, LOADING_DOCUMENT_TITLE);
			await expect(page.getByRole('heading', { name: ERROR_TITLE })).toHaveCount(0);
			await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
			await expect.poll(() => calls.connection.length).toBe(2);
			expect(calls.posts).toHaveLength(0);
			expect(calls.healthReads).toHaveLength(0);
			retry.resolve();
			await homeLoaded(page);
			await documentTitle(page, '홈 | 캔디핏');
			await expect(page.getByText(RETRYING_NOTICE)).toHaveCount(0);
			expect(calls.connection).toHaveLength(2);
			expect(calls.posts).toHaveLength(1);
			expect(calls.healthReads).toHaveLength(5);
		} finally {
			retry.resolve();
		}
	});
}

test('403은 자동 재시도하지 않고 오류와 프로필·로그아웃을 유지한다', async ({ page }) => {
	await page.clock.install();
	const calls = trackRequests(page);
	await page.route(STATUS_API, (route) =>
		route.fulfill({
			status: 403,
			json: { error: { code: 'FORBIDDEN', message: 'synthetic' } }
		})
	);
	await page.goto('/home');
	await expect(page.getByRole('heading', { level: 1, name: ERROR_TITLE })).toBeVisible();
	await documentTitle(page, ERROR_DOCUMENT_TITLE);
	await page.clock.runFor(1000);
	await expect(page.getByRole('button', { name: RECHECK, exact: true })).toBeEnabled();
	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await expect(page.getByRole('menuitem', { name: '마이페이지' })).toHaveAttribute('href', '/my');
	await expect(page.getByRole('menuitem', { name: '로그아웃', exact: true })).toBeEnabled();
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
	expect(calls.connection).toHaveLength(1);
	expect(calls.posts).toHaveLength(0);
	expect(calls.healthReads).toHaveLength(0);
});

for (const code of ['AUTH_REQUIRED', 'AUTH_EXPIRED']) {
	test(`연결 조회 401 ${code}는 기존 로그인 흐름으로 돌아가고 건강 요청을 보내지 않는다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		await page.route(STATUS_API, (route) =>
			route.fulfill({ status: 401, json: { error: { code, message: 'synthetic' } } })
		);
		await page.goto('/health');
		await expect(page).toHaveURL('/login');
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
		expect(calls.connection).toHaveLength(1);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
	});
}

for (const initial of ['CONNECTED', 'UNKNOWN'] as const) {
	test(`${initial}: focus·visibility 재검사는 화면과 기존 건강 요청 수를 유지한다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		const refresh = deferred();
		const next = initial === 'CONNECTED' ? 'UNKNOWN' : 'CONNECTED';
		let attempt = 0;
		await page.route(STATUS_API, async (route) => {
			if (++attempt === 1) return fulfillConnection(route, initial);
			await refresh.promise;
			await fulfillConnection(route, next);
		});
		try {
			await page.goto('/home');
			await homeLoaded(page);
			expect(calls.posts).toHaveLength(1);
			expect(calls.healthReads).toHaveLength(5);
			await page.locator('main[data-health-ui]').evaluate((main) => {
				main.setAttribute('data-connection-probe', 'retained');
			});
			await resumeEvents(page);
			await expect.poll(() => calls.connection.length).toBe(2);
			await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
				'data-connection-probe',
				'retained'
			);
			await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
			refresh.resolve();
			if (next === 'UNKNOWN') await expect(page.getByText(UNKNOWN_NOTICE)).toBeVisible();
			else await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
			await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
				'data-connection-probe',
				'retained'
			);
			expect(calls.connection).toHaveLength(2);
			expect(calls.posts).toHaveLength(1);
			expect(calls.healthReads).toHaveLength(5);
		} finally {
			refresh.resolve();
		}
	});
}

test('기존 건강 화면의 재검사 실패는 마지막 화면을 유지하고 오류만 알린다', async ({ page }) => {
	const calls = trackRequests(page);
	let attempt = 0;
	await page.route(STATUS_API, (route) =>
		++attempt === 1
			? fulfillConnection(route, 'CONNECTED')
			: route.fulfill({ status: 503, json: { error: { code: 'HEALTH_DATA_UNAVAILABLE' } } })
	);
	await page.goto('/home');
	await homeLoaded(page);
	await page.locator('main[data-health-ui]').evaluate((main) => {
		main.setAttribute('data-connection-probe', 'retained');
	});
	await resumeEvents(page);
	await expect(
		page.getByRole('alert').filter({ hasText: '마지막으로 확인한 상태를 유지하고 있어요.' })
	).toBeVisible();
	await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
		'data-connection-probe',
		'retained'
	);
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
	expect(calls.connection).toHaveLength(3);
	expect(calls.posts).toHaveLength(1);
	expect(calls.healthReads).toHaveLength(5);
});

test('UNKNOWN 안내 닫기는 같은 로그인 세션의 홈·건강·AI 경로에서 유지된다', async ({ page }) => {
	const calls = trackRequests(page);
	await page.route(STATUS_API, (route) => fulfillConnection(route, 'UNKNOWN'));
	await mockCoachingServer(page, () => {});
	await page.goto('/home');
	await homeLoaded(page);
	await expect(page.getByText(UNKNOWN_NOTICE)).toBeVisible();
	await page.locator('main[data-health-ui]').evaluate((main) => {
		main.setAttribute('data-connection-probe', 'retained');
	});
	await page.getByRole('button', { name: CLOSE_NOTICE, exact: true }).click();
	await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
	await expect(page.getByRole('button', { name: CLOSE_NOTICE, exact: true })).toHaveCount(0);
	await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
		'data-connection-probe',
		'retained'
	);
	expect(calls.connection).toHaveLength(1);
	expect(calls.posts).toHaveLength(1);
	expect(calls.healthReads).toHaveLength(5);
	const navigation = page.getByRole('navigation', { name: '주 메뉴' });
	await navigation.getByRole('link', { name: '건강 데이터', exact: true }).click();
	await expect(page).toHaveURL('/health');
	await expect(page.locator('.health-summary-card .health-summary-link[href]')).toHaveCount(4);
	await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
	await expect(page.getByRole('button', { name: CLOSE_NOTICE, exact: true })).toHaveCount(0);
	const postsAfterHealth = calls.posts.length;
	const readsAfterHealth = calls.healthReads.length;
	expect(postsAfterHealth).toBe(2);
	expect(readsAfterHealth).toBe(9);
	await navigation.getByRole('link', { name: 'AI 코치', exact: true }).click();
	await expect(page).toHaveURL('/coaching');
	await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeVisible();
	await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
	await expect(page.getByRole('button', { name: CLOSE_NOTICE, exact: true })).toHaveCount(0);
	expect(calls.connection).toHaveLength(1);
	expect(calls.posts).toHaveLength(postsAfterHealth);
	expect(calls.healthReads).toHaveLength(readsAfterHealth);
});

test('UNKNOWN 안내를 닫아도 재검사 오류와 필수 미연동 안내는 숨기지 않는다', async ({ page }) => {
	const calls = trackRequests(page);
	let status: ConnectionStatus | 'error' = 'UNKNOWN';
	await page.route(STATUS_API, (route) =>
		status === 'error'
			? route.fulfill({
					status: 503,
					json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
				})
			: fulfillConnection(route, status)
	);
	await page.goto('/home');
	await homeLoaded(page);
	await page.locator('main[data-health-ui]').evaluate((main) => {
		main.setAttribute('data-connection-probe', 'retained');
	});
	await page.getByRole('button', { name: CLOSE_NOTICE, exact: true }).click();
	await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
	status = 'error';
	await resumeEvents(page);
	await expect(
		page.getByRole('alert').filter({ hasText: '마지막으로 확인한 상태를 유지하고 있어요.' })
	).toBeVisible();
	await homeLoaded(page);
	await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
		'data-connection-probe',
		'retained'
	);
	await expect(page.getByRole('button', { name: CLOSE_NOTICE, exact: true })).toHaveCount(0);
	expect(calls.connection).toHaveLength(3);
	expect(calls.posts).toHaveLength(1);
	expect(calls.healthReads).toHaveLength(5);
	status = 'NOT_CONNECTED';
	await page.getByRole('button', { name: RECHECK, exact: true }).click();
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toBeVisible();
	await expect(page.getByRole('button', { name: RECHECK, exact: true })).toBeEnabled();
	await expect(page.getByRole('button', { name: CLOSE_NOTICE, exact: true })).toHaveCount(0);
	expect(calls.connection).toHaveLength(4);
	expect(calls.posts).toHaveLength(1);
	expect(calls.healthReads).toHaveLength(5);
});

test('UNKNOWN 안내 닫힘은 새 문서의 인증 상태 복원에서 초기화된다', async ({ page }) => {
	const calls = trackRequests(page);
	await page.route(STATUS_API, (route) => fulfillConnection(route, 'UNKNOWN'));
	await page.goto('/home');
	await homeLoaded(page);
	await page.getByRole('button', { name: CLOSE_NOTICE, exact: true }).click();
	await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
	await page.reload();
	await homeLoaded(page);
	await expect(page.getByText(UNKNOWN_NOTICE)).toBeVisible();
	await expect(page.getByRole('button', { name: CLOSE_NOTICE, exact: true })).toBeEnabled();
	expect(calls.connection).toHaveLength(2);
	expect(calls.posts).toHaveLength(2);
	expect(calls.healthReads).toHaveLength(10);
});

for (const trigger of ['manual', 'reload', 'resume'] as const) {
	test(`NOT_CONNECTED → CONNECTED: ${trigger} 확인 후 기존 건강 흐름을 한 번 시작한다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		let status: ConnectionStatus = 'NOT_CONNECTED';
		await page.route(STATUS_API, (route) => fulfillConnection(route, status));
		await page.goto('/home');
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toBeVisible();
		await documentTitle(page, GUIDE_DOCUMENT_TITLE);
		expect(calls.posts).toHaveLength(0);
		status = 'CONNECTED';
		if (trigger === 'manual')
			await page.getByRole('button', { name: RECHECK, exact: true }).click();
		else if (trigger === 'reload') await page.reload();
		else await resumeEvents(page);
		await homeLoaded(page);
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
		await documentTitle(page, '홈 | 캔디핏');
		expect(calls.connection).toHaveLength(2);
		expect(calls.posts).toHaveLength(1);
		expect(calls.healthReads).toHaveLength(5);
	});
}

test('건강 기록 수동 재확인은 최신 NOT_CONNECTED를 확인한 뒤 추가 POST를 보내지 않는다', async ({
	page
}) => {
	const calls = trackRequests(page);
	let status: ConnectionStatus = 'CONNECTED';
	await page.route(STATUS_API, (route) => fulfillConnection(route, status));
	await page.route(SYNC_API, (route) =>
		route.fulfill({
			status: 503,
			json: {
				error: {
					code: 'HEALTH_SYNC_UNAVAILABLE',
					context: { reason: 'METRIC_FAILED', metrics: [] }
				}
			}
		})
	);
	await page.goto('/home');
	await homeLoaded(page);
	expect(calls.posts).toHaveLength(1);
	status = 'NOT_CONNECTED';
	await page.getByRole('button', { name: '건강 기록 다시 확인', exact: true }).click();
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toBeVisible();
	expect(calls.connection).toHaveLength(2);
	expect(calls.posts).toHaveLength(1);
	expect(calls.healthReads).toHaveLength(5);
});

test('안내 상태에서도 프로필 조회·수정과 로그아웃을 유지하고 같은 계정 상태를 초기화하지 않는다', async ({
	page
}) => {
	const calls = trackRequests(page);
	let loggedOut = false;
	await page.route(STATUS_API, (route) => fulfillConnection(route, 'NOT_CONNECTED'));
	await page.route('**/api/v1/users/me', (route) =>
		route.fulfill({
			json: {
				data: {
					...e2eUser,
					...(route.request().method() === 'PATCH' ? route.request().postDataJSON() : {})
				}
			}
		})
	);
	await page.route('**/api/v1/auth/logout', (route) => {
		loggedOut = true;
		return route.fulfill({ status: 204 });
	});
	await page.route(SESSION_API, (route) =>
		loggedOut
			? route.fulfill({ status: 401, json: { error: { code: 'AUTH_REQUIRED' } } })
			: route.fallback()
	);
	await page.goto('/home');
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toBeVisible();
	await documentTitle(page, GUIDE_DOCUMENT_TITLE);
	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await page.getByRole('menuitem', { name: '마이페이지' }).click();
	await expect(page).toHaveURL('/my');
	await expect(page.getByRole('heading', { level: 1, name: '기본 정보 수정' })).toBeVisible();
	await documentTitle(page, '마이페이지 | 캔디핏');
	await expect(page.getByLabel('닉네임')).toHaveValue('테스트');
	await page.getByLabel('닉네임').fill('연동 테스트');
	await page.getByRole('button', { name: '저장하기', exact: true }).click();
	await expect(
		page.getByRole('status').filter({ hasText: '기본 정보를 저장했어요.' })
	).toBeVisible();
	await page.getByRole('link', { name: 'CANDiY-FiT 홈', exact: true }).click();
	await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toBeVisible();
	await documentTitle(page, GUIDE_DOCUMENT_TITLE);
	expect(calls.connection).toHaveLength(1);
	expect(calls.posts).toHaveLength(0);
	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	await page.getByRole('menuitem', { name: '로그아웃', exact: true }).click();
	await expect(page).toHaveURL('/login');
	expect(loggedOut).toBe(true);
	await page.goto('/home');
	await expect(page).toHaveURL('/login');
	expect(calls.connection).toHaveLength(1);
});

test('최초 연결 조회가 대기·실패해도 마이페이지 직접 진입을 차단하지 않는다', async ({ page }) => {
	const connection = deferred();
	const failedTwice = deferred();
	let attempt = 0;
	await page.route(STATUS_API, async (route) => {
		const current = ++attempt;
		await connection.promise;
		await route.fulfill({ status: 503, json: { error: { code: 'HEALTH_DATA_UNAVAILABLE' } } });
		if (current === 2) failedTwice.resolve();
	});
	await page.route('**/api/v1/users/me', (route) => route.fulfill({ json: { data: e2eUser } }));
	try {
		await page.goto('/my');
		await expect(page.getByRole('heading', { level: 1, name: '기본 정보 수정' })).toBeVisible();
		await documentTitle(page, '마이페이지 | 캔디핏');
		await expect(page.getByLabel('닉네임')).toHaveValue('테스트');
		const failed = page.waitForResponse('**/api/v1/health/data-status');
		connection.resolve();
		await failed;
		await failedTwice.promise;
		await expect(page.getByRole('heading', { level: 1, name: '기본 정보 수정' })).toBeVisible();
		await expect(page.getByRole('heading', { name: ERROR_TITLE })).toHaveCount(0);
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
		await documentTitle(page, '마이페이지 | 캔디핏');
	} finally {
		connection.resolve();
	}
});

for (const initial of ['CONNECTED', 'UNKNOWN'] as const) {
	for (const outcome of ['CONNECTED', 'UNKNOWN', 'error'] as const) {
		test(`${initial} → 조회 오류 → 수동 ${outcome}: 재조회 중 상태와 버튼·기존 화면을 유지한다`, async ({
			page
		}) => {
			await page.clock.install({ time: new Date('2026-10-26T03:00:00Z') });
			const calls = trackRequests(page);
			const retry = deferred();
			let attempt = 0;
			await page.route(STATUS_API, async (route) => {
				const current = ++attempt;
				if (current === 1) return fulfillConnection(route, initial);
				if (current === 3) {
					await retry.promise;
					if (outcome !== 'error') return fulfillConnection(route, outcome);
				}
				return route.fulfill({
					status: 500,
					json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
				});
			});
			try {
				await page.goto('/home');
				await homeLoaded(page);
				if (initial === 'UNKNOWN')
					await page.getByRole('button', { name: CLOSE_NOTICE, exact: true }).click();
				await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
				await expect(page.getByRole('button', { name: RECHECK, exact: true })).toHaveCount(0);
				await page.locator('main[data-health-ui]').evaluate((main) => {
					main.setAttribute('data-manual-recheck-probe', 'retained');
				});
				await page.clock.pauseAt(new Date('2026-10-26T03:01:00Z'));
				await resumeEvents(page);
				await page.clock.runFor(100);
				const error = page
					.getByRole('alert')
					.filter({ hasText: '마지막으로 확인한 상태를 유지하고 있어요.' });
				await expect(error).toBeVisible();
				expect(calls.connection).toHaveLength(2);
				expect(calls.posts).toHaveLength(1);
				expect(calls.healthReads).toHaveLength(5);

				await page.getByRole('button', { name: RECHECK, exact: true }).click();
				await expect.poll(() => calls.connection.length).toBe(3);
				await expect(page.getByRole('status').filter({ hasText: CHECKING_NOTICE })).toBeVisible();
				await expect(page.getByRole('button', { name: CHECKING, exact: true })).toBeDisabled();
				await expect(error).toHaveCount(0);
				await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
				await homeLoaded(page);
				await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
					'data-manual-recheck-probe',
					'retained'
				);
				await documentTitle(page, '홈 | 캔디핏');
				// 진행 중 수동 요청에 이어지는 탭 복귀 debounce도 같은 GET에 합쳐져야 한다.
				await resumeEvents(page);
				await page.clock.runFor(100);
				expect(calls.connection).toHaveLength(3);
				expect(calls.posts).toHaveLength(1);
				expect(calls.healthReads).toHaveLength(5);

				retry.resolve();
				if (outcome === 'error') {
					await expect(error).toBeVisible();
					await expect(page.getByRole('button', { name: RECHECK, exact: true })).toBeEnabled();
				} else if (outcome === 'UNKNOWN' && initial === 'CONNECTED') {
					await expect(page.getByText(UNKNOWN_NOTICE)).toBeVisible();
					await expect(page.getByRole('button', { name: RECHECK, exact: true })).toBeEnabled();
				} else {
					await expect(page.getByRole('button', { name: RECHECK, exact: true })).toHaveCount(0);
				}
				await expect(page.getByRole('status').filter({ hasText: CHECKING_NOTICE })).toHaveCount(0);
				await expect(page.getByRole('button', { name: CHECKING, exact: true })).toHaveCount(0);
				if (outcome !== 'error') await expect(error).toHaveCount(0);
				await homeLoaded(page);
				await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
					'data-manual-recheck-probe',
					'retained'
				);
				await documentTitle(page, '홈 | 캔디핏');
				expect(calls.connection).toHaveLength(3);
				expect(calls.posts).toHaveLength(1);
				expect(calls.healthReads).toHaveLength(5);
			} finally {
				retry.resolve();
			}
		});
	}
}

for (const initial of ['CONNECTED', 'UNKNOWN'] as const) {
	test(`${initial}: 정상 배경 재조회는 완료 전후에 수동 조회 배너를 만들지 않는다`, async ({
		page
	}) => {
		const calls = trackRequests(page);
		const refresh = deferred();
		let attempt = 0;
		await page.route(STATUS_API, async (route) => {
			if (++attempt === 1) return fulfillConnection(route, initial);
			await refresh.promise;
			return fulfillConnection(route, initial);
		});
		try {
			await page.goto('/home');
			await homeLoaded(page);
			if (initial === 'UNKNOWN')
				await page.getByRole('button', { name: CLOSE_NOTICE, exact: true }).click();
			await page.locator('main[data-health-ui]').evaluate((main) => {
				main.setAttribute('data-background-recheck-probe', 'retained');
			});
			const refreshed = page.waitForResponse(STATUS_API);
			await resumeEvents(page);
			await expect.poll(() => calls.connection.length).toBe(2);
			await expect(page.getByText(CHECKING_NOTICE, { exact: true })).toHaveCount(0);
			await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
			await expect(page.getByRole('button', { name: RECHECK, exact: true })).toHaveCount(0);
			await expect(page.getByRole('button', { name: CHECKING, exact: true })).toHaveCount(0);
			await expect(page.getByRole('alert')).toHaveCount(0);
			await homeLoaded(page);
			await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
				'data-background-recheck-probe',
				'retained'
			);
			refresh.resolve();
			await (await refreshed).finished();
			await homeLoaded(page);
			await expect(page.getByText(CHECKING_NOTICE, { exact: true })).toHaveCount(0);
			await expect(page.getByText(UNKNOWN_NOTICE)).toHaveCount(0);
			await expect(page.getByRole('button', { name: RECHECK, exact: true })).toHaveCount(0);
			await expect(page.getByRole('button', { name: CHECKING, exact: true })).toHaveCount(0);
			await expect(page.getByRole('alert')).toHaveCount(0);
			await expect(page.locator('main[data-health-ui]')).toHaveAttribute(
				'data-background-recheck-probe',
				'retained'
			);
			await documentTitle(page, '홈 | 캔디핏');
			expect(calls.connection).toHaveLength(2);
			expect(calls.posts).toHaveLength(1);
			expect(calls.healthReads).toHaveLength(5);
		} finally {
			refresh.resolve();
		}
	});
}

test('최초 오류의 수동 재조회는 확인 중 제목을 거쳐 실제 홈 제목으로 복구한다', async ({
	page
}) => {
	const calls = trackRequests(page);
	const retry = deferred();
	let attempt = 0;
	await page.route(STATUS_API, async (route) => {
		if (++attempt === 1)
			return route.fulfill({
				status: 500,
				json: { error: { code: 'HEALTH_DATA_UNAVAILABLE', message: 'synthetic' } }
			});
		await retry.promise;
		return fulfillConnection(route, 'CONNECTED');
	});
	try {
		await page.goto('/home');
		await expect(page.getByRole('heading', { level: 1, name: ERROR_TITLE })).toBeVisible();
		await documentTitle(page, ERROR_DOCUMENT_TITLE);
		expect(calls.connection).toHaveLength(1);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		await page.getByRole('button', { name: RECHECK, exact: true }).click();
		await expect(page.getByRole('status').filter({ hasText: CHECKING_NOTICE })).toBeVisible();
		await documentTitle(page, LOADING_DOCUMENT_TITLE);
		await expect(page.getByRole('heading', { name: ERROR_TITLE })).toHaveCount(0);
		await expect(page.getByRole('heading', { name: GUIDE_TITLE })).toHaveCount(0);
		expect(calls.connection).toHaveLength(2);
		expect(calls.posts).toHaveLength(0);
		expect(calls.healthReads).toHaveLength(0);
		retry.resolve();
		await homeLoaded(page);
		await documentTitle(page, '홈 | 캔디핏');
		await expect(page.getByText(CHECKING_NOTICE, { exact: true })).toHaveCount(0);
		expect(calls.connection).toHaveLength(2);
		expect(calls.posts).toHaveLength(1);
		expect(calls.healthReads).toHaveLength(5);
	} finally {
		retry.resolve();
	}
});
