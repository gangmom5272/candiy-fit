import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import {
	homeDailyMetricFixture,
	lifestyleScoreFixture,
	mockHomeApi
} from '../../../lib/testing/home-api-fixture';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

test('KST 오늘 일별 값을 조회하고 기간 summary 대신 진행 중인 오늘 값과 수면 심박을 사용한다', async ({
	page
}) => {
	const requests: URL[] = [];
	page.on('request', (request) => {
		if (new URL(request.url()).pathname.startsWith('/api/v1/health/'))
			requests.push(new URL(request.url()));
	});
	await page.goto('/home');
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	const cards = page.getByRole('region', { name: '최근 건강 기록' });
	await expect(page.getByRole('region', { name: '오늘의 한마디', exact: true })).toContainText(
		'오늘은 나에게 칭찬 한마디 건네볼까요?'
	);
	expect(requests).toHaveLength(6);
	expect(requests[0].pathname).toBe('/api/v1/health/sync');
	for (const url of requests) {
		if (url.pathname.endsWith('/daily-message') || url.pathname.endsWith('/sync'))
			expect(url.search).toBe('');
		else if (url.pathname.endsWith('/lifestyle-score')) expect(url.search).toBe('?date=2026-10-26');
		else expect(url.search).toBe('?start=2026-10-26&end=2026-10-26');
	}
	const heart = cards.getByRole('link', { name: /안정 시 심박수/ });
	await expect(heart.getByText('64.5 bpm', { exact: true })).toBeVisible();
	await expect(cards.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	await expect(cards.getByRole('link', { name: '운동 시간 18분', exact: true })).toBeVisible();
	await expect(cards.getByRole('link', { name: '수면 7시간 0분', exact: true })).toBeVisible();
	await expect(heart.getByText('평균 75.68 bpm', { exact: true })).toHaveCount(0);
	await page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }).click();
	await expect(page.getByRole('region', { name: '왜 이런 상태로 보일까요?' })).toContainText(
		'오늘의 점수는 71점'
	);
});

test('점수 지연 중에도 오늘 지표를 표시하고 심박은 점수 응답을 기다린다', async ({ page }) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route('**/api/v1/health/lifestyle-score?*', async (route) => {
		await gate;
		await route.fallback();
	});
	await page.goto('/home');
	await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	const heart = page.getByRole('link', { name: /안정 시 심박수/ });
	await expect(heart.getByText('불러오는 중', { exact: true })).toBeVisible();
	release();
	await expect(heart.getByText('64.5 bpm', { exact: true })).toBeVisible();
});

test('점수 준비 중과 정상 오늘 기록을 분리하고 새로고침으로 실제 재요청한다', async ({ page }) => {
	let attempts = 0;
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		attempts++;
		return attempts === 1
			? route.fulfill({
					status: 503,
					json: {
						error: {
							code: 'HEALTH_DATA_UNAVAILABLE',
							message: '점수 데이터를 준비 중입니다.',
							context: { reason: 'DATA_NOT_READY', metrics: [] }
						}
					}
				})
			: route.fallback();
	});
	await page.goto('/home');
	await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toBeVisible();
	await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
	await expect(page.getByRole('alert')).toHaveCount(0);
	await expect(page.getByRole('link', { name: /걸음 수 3,200보/ })).toBeVisible();
	await expect(page.getByRole('link', { name: /안정 시 심박수 준비 중/ })).toBeVisible();
	await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toHaveCount(0);
	await page.reload();
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	expect(attempts).toBe(2);
});

test('연동 안내가 꺼져도 정확한 DATA_NOT_READY만 준비 상태로 표시하고 수동 확인으로 다시 조회한다', async ({
	page
}) => {
	let preparing = true;
	let statusReads = 0;
	let posts = 0;
	const reads: string[] = [];
	page.on('request', (request) => {
		const { pathname } = new URL(request.url());
		if (pathname === '/api/v1/health/data-status') statusReads++;
		else if (pathname === '/api/v1/health/sync') posts++;
		else if (request.method() === 'GET' && pathname.startsWith('/api/v1/health/'))
			reads.push(pathname);
	});
	await page.route('**/api/v1/health/**', (route) => {
		if (route.request().method() !== 'GET' || !preparing) return route.fallback();
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
	});
	await page.goto('/home');
	await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toBeVisible();
	await expect(page.getByLabel('오늘의 점수 준비 중', { exact: true })).toBeVisible();
	await expect(
		page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
	).toHaveText(['준비 중', '준비 중', '준비 중', '준비 중']);
	await expect(page.getByRole('heading', { name: '앱 연동을 완료했어요' })).toHaveCount(0);
	await expect(page.locator('main')).not.toContainText('조회 실패');
	await expect(page.getByRole('alert')).toHaveCount(0);
	expect(statusReads).toBe(0);
	expect(posts).toBe(1);
	expect(reads).toHaveLength(5);
	preparing = false;
	await page.getByRole('button', { name: '건강데이터 다시 확인', exact: true }).click();
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	await expect(
		page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
	).toHaveText(['3,200보', '18분', '7시간 0분', '64.5 bpm']);
	await expect(page.getByRole('heading', { name: '건강 기록을 준비하고 있어요' })).toHaveCount(0);
	expect(statusReads).toBe(0);
	expect(posts).toBe(2);
	expect(reads).toHaveLength(10);
});

test('수면 API가 실패해도 오늘 수면 심박과 다른 카드는 유지하고 일반 심박을 요청하지 않는다', async ({
	page
}) => {
	let heartRequests = 0;
	page.on('request', (request) => {
		if (request.url().includes('/metrics/heart_rates?')) heartRequests++;
	});
	await page.route('**/api/v1/health/metrics/sleeps?*', (route) =>
		route.fulfill({
			status: 403,
			json: { error: { code: 'HEALTH_DATA_FORBIDDEN', message: '수면 조회 권한이 없습니다.' } }
		})
	);
	await page.goto('/home');
	await expect(page.getByRole('alert')).toHaveCount(1);
	const heart = page.getByRole('link', { name: /안정 시 심박수 64.5 bpm/ });
	await expect(heart.locator('svg')).toHaveCount(0);
	await expect(page.getByRole('link', { name: /걸음 수 3,200보/ })).toBeVisible();
	await expect(page.getByRole('link', { name: /운동 시간 18분/ })).toBeVisible();
	expect(heartRequests).toBe(0);
});

test('0점은 숫자로 표시하고 점수 null이어도 수면 심박이 있으면 대표값으로 사용한다', async ({
	page
}) => {
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const data = lifestyleScoreFixture();
		data.total = { score: 0, missingAreas: ['sleep', 'heart'] };
		data.areas.activity = {
			score: 0,
			grade: 4,
			steps: { score: 0, value: 800 },
			exercise: { score: 0, minutes: 0 }
		};
		data.areas.sleep = { score: null, grade: null, sleepMinutes: null };
		data.areas.heart = {
			score: null,
			grade: null,
			sleepHrAvg: 64.5,
			baselineHrAvg: null,
			baselineNights: 0,
			checkWear: false
		};
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	await expect(page.getByLabel('오늘의 점수 100점 만점에 0점')).toBeVisible();
	await expect(page.getByRole('link', { name: /안정 시 심박수 64.5 bpm/ })).toBeVisible();
	await page.getByRole('button', { name: '활동 관리 수면 기록 없음 심박 기록 없음' }).click();
	await expect(page.getByText('활동 · 관리', { exact: true })).toHaveCSS(
		'color',
		'rgb(201, 79, 87)'
	);
	await expect(page.getByText('활동 · 관리', { exact: true })).toHaveCSS(
		'background-color',
		'rgb(252, 235, 236)'
	);
	await expect(
		page.getByRole('region', { name: '왜 이런 상태로 보일까요?' }).getByText('0점', { exact: true })
	).toBeVisible();
});

test('대표값이 null이면 일반 심박이 있어도 수면 중 심박을 채우지 않는다', async ({ page }) => {
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const data = lifestyleScoreFixture();
		data.total = { score: 72, missingAreas: ['heart'] };
		data.areas.heart = {
			score: null,
			grade: null,
			sleepHrAvg: null,
			baselineHrAvg: 58,
			baselineNights: 20,
			checkWear: false
		};
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	const heart = page.getByRole('link', { name: /안정 시 심박수 기록 없음/ });
	await expect(heart).toBeVisible();
	await expect(heart.locator('svg')).toHaveCount(0);
});

for (const code of ['AUTH_EXPIRED', 'AUTH_REQUIRED'] as const) {
	test(`${code}는 갱신·재요청 없이 로그인으로 이동한다`, async ({ page }) => {
		await page.route('**/api/v1/health/lifestyle-score?*', (route) =>
			route.fulfill({ status: 401, json: { error: { code, message: '로그인이 필요합니다.' } } })
		);
		await page.goto('/home');
		await expect(page).toHaveURL(/\/login/);
	});
}

test('페이지 이탈 시 요청을 취소하고 재진입하면 오늘 점수를 새로 조회한다', async ({ page }) => {
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	let attempts = 0;
	await page.route('**/api/v1/health/lifestyle-score?*', async (route) => {
		attempts++;
		if (attempts === 1) await gate;
		await route.fallback();
	});
	await page.goto('/home');
	await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	const canceled = page.waitForEvent('requestfailed', (request) =>
		request.url().includes('/lifestyle-score?')
	);
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '건강 데이터', exact: true })
		.click();
	await expect(page).toHaveURL('/health');
	release();
	await canceled;
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '홈', exact: true })
		.click();
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	expect(attempts).toBe(2);
});

for (const [width, height] of [
	[1920, 1080],
	[1366, 768],
	[768, 1024],
	[360, 800]
]) {
	test(`${width}px에서 로딩·동기화 대기·부분 실패·전체 실패의 Home 배치를 유지한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		let normalLayout:
			Record<string, { x: number; y: number; width: number; height: number }> | undefined;
		let normalPanelHeight = 0;
		for (const state of ['normal', 'loading', 'waiting', 'partial', 'error']) {
			let release = () => {};
			const held = new Promise<void>((resolve) => {
				release = resolve;
			});
			await page.route('**/api/v1/health/**', async (route) => {
				if (route.request().method() !== 'GET') return route.fallback();
				const url = new URL(route.request().url());
				const isScore = url.pathname.endsWith('/lifestyle-score');
				if (state === 'loading') await held;
				if (state === 'error' || (state === 'partial' && url.pathname.endsWith('/steps'))) {
					return route.fulfill({
						status: 503,
						headers: { 'X-Request-Id': '00000000-0000-4000-8000-000000000001' },
						json: {
							error: {
								code: 'HEALTH_DATA_UNAVAILABLE',
								message: 'Internal lookup failed. '.repeat(20),
								context: { reason: 'LOOKUP_FAILED', metrics: ['steps'] }
							}
						}
					});
				}
				if (state === 'waiting' && isScore) {
					const data = lifestyleScoreFixture();
					data.reason = 'UNSYNCED';
					data.total.score = null;
					for (const area of Object.values(data.areas)) {
						area.score = null;
						area.grade = null;
					}
					data.areas.heart.sleepHrAvg = null;
					return route.fulfill({ json: { data } });
				}
				if (state === 'waiting' && url.pathname.endsWith('/steps')) {
					const data = homeDailyMetricFixture('steps');
					data.days.forEach((day) => {
						day.value = null;
						day.missing = true;
						day.reason = 'UNSYNCED';
					});
					Object.assign(data.summary, {
						avg: null,
						total: null,
						dailyMin: null,
						dailyMax: null,
						daysWithData: 0,
						daysUsedForSummary: 0,
						completedDays: 0,
						missingDays: [],
						unsyncedDays: data.days.map((day) => day.date)
					});
					return route.fulfill({ json: { data } });
				}
				return route.fallback();
			});
			try {
				await page.mouse.move(0, 0);
				await page.goto('/home', { waitUntil: 'domcontentloaded' });
				const trigger = page.getByRole('group', { name: '건강 상태' }).getByRole('button');
				const records = page.getByRole('region', { name: '최근 건강 기록' });
				const label =
					state === 'loading'
						? '불러오는 중'
						: state === 'waiting'
							? '동기화 대기'
							: state === 'error'
								? '조회 실패'
								: '훌륭';
				await expect(trigger).toContainText(label);
				if (state === 'normal' || state === 'partial')
					await expect(
						records.getByRole('link', { name: '운동 시간 18분', exact: true })
					).toBeVisible();
				await page.evaluate(() => document.fonts.ready);
				await page.evaluate(() => scrollTo(0, 0));
				const spacing = await page.evaluate(() => {
					const records = document.querySelector('.health-records');
					const banners = document.querySelector('.home-highlights');
					if (!records || !banners) throw new Error('Home 영역 간격을 확인할 수 없습니다.');
					return banners.getBoundingClientRect().top - records.getBoundingClientRect().bottom;
				});
				expect(spacing).toBeGreaterThanOrEqual(width >= 768 ? 32 : 24);
				expect(spacing).toBeLessThanOrEqual(width >= 1024 ? 40 : width >= 768 ? 32 : 24);
				if (state === 'normal') {
					for (const card of await records.getByRole('link').all()) {
						const bounds = await card.boundingBox();
						if (!bounds) throw new Error('Home 카드가 표시되지 않았습니다.');
						expect(bounds.height).toBeGreaterThanOrEqual(128);
						expect(bounds.height).toBeLessThanOrEqual(160);
						if (width >= 768) {
							const title = await card.getByRole('heading').locator('..').boundingBox();
							const value = await card.locator('.metric-value').boundingBox();
							if (!title || !value) throw new Error('카드 제목과 수치를 확인할 수 없습니다.');
							expect(
								Math.abs(title.x + title.width / 2 - (value.x + value.width / 2))
							).toBeLessThanOrEqual(1);
						}
					}
				}
				const layout = await page.evaluate(() => {
					function bounds(selector: string) {
						const element = document.querySelector(selector);
						if (!element) throw new Error(`Home 요소가 없습니다: ${selector}`);
						const { x, y, width, height } = element.getBoundingClientRect();
						return { x, y, width, height };
					}
					return {
						title: bounds('#home-title'),
						total: bounds('.hero-total'),
						trigger: bounds('.summary-trigger')
					};
				});
				const scoreAlignmentOffset = await page.locator('.hero-total').evaluate((element) => {
					const bounds = element.getBoundingClientRect();
					const spans = element.querySelectorAll('span');
					const first = spans[0].getBoundingClientRect();
					const last = spans[spans.length - 1].getBoundingClientRect();
					return innerWidth < 1024
						? Math.abs(first.left - bounds.left)
						: Math.abs((first.left + last.right) / 2 - (bounds.left + bounds.right) / 2);
				});
				expect(scoreAlignmentOffset).toBeLessThanOrEqual(1);
				if (state === 'normal') normalLayout = layout;
				else {
					if (!normalLayout) throw new Error('정상 Home 배치 기준이 없습니다.');
					for (const key of ['title', 'total', 'trigger'] as const) {
						for (const dimension of ['x', 'y', 'width', 'height'] as const) {
							expect(
								Math.abs(layout[key][dimension] - normalLayout[key][dimension]),
								`${state} ${key} ${dimension}`
							).toBeLessThanOrEqual(1);
						}
					}
				}
				for (const text of await trigger.locator('.summary-status > span').all()) {
					expect(
						await text.evaluate((element) => {
							const range = document.createRange();
							range.selectNodeContents(element);
							return range.getClientRects().length;
						})
					).toBe(1);
				}
				expect(
					await records.evaluate((element) =>
						Array.from(element.querySelectorAll('.metric-card')).every(
							(card) =>
								card.scrollWidth <= card.clientWidth + 1 &&
								card.scrollHeight <= card.clientHeight + 1
						)
					)
				).toBe(true);
				await expect(page.locator('main')).not.toContainText('Internal lookup failed');
				await expect(page.locator('main')).not.toContainText(
					'00000000-0000-4000-8000-000000000001'
				);
				if (state === 'partial' || state === 'error') {
					await expect(records.getByRole('alert').first()).toHaveText(
						'건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'
					);
				}
				const title = await page.getByRole('heading', { level: 1 }).boundingBox();
				const header = await page.getByRole('banner').boundingBox();
				expect(title && header && title.y >= header.y + header.height).toBeTruthy();
				await trigger.click();
				const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
				await expect(panel).toBeVisible();
				if (state === 'loading' || state === 'waiting' || state === 'error') {
					await expect(panel.locator('strong')).toHaveText([label, label, label]);
				}
				const panelBounds = await panel.boundingBox();
				if (!panelBounds) throw new Error('점수 설명 패널이 없습니다.');
				if (state === 'normal') normalPanelHeight = panelBounds.height;
				else expect(panelBounds.height).toBeLessThanOrEqual(normalPanelHeight + 24);
				expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
					true
				);
				expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
					true
				);
				await page.screenshot({
					path: testInfo.outputPath(`${state}-${width}.png`),
					fullPage: true
				});
				await page.keyboard.press('Escape');
				await expect(panel).toHaveCount(0);
				const banners = await page.locator('.home-highlights').boundingBox();
				const footerText = await page
					.getByRole('contentinfo')
					.getByRole('button')
					.first()
					.boundingBox();
				if (!banners || !footerText) throw new Error('배너와 푸터 간격을 확인할 수 없습니다.');
				expect(footerText.y - banners.y - banners.height).toBeGreaterThanOrEqual(16);
				expect(footerText.y - banners.y - banners.height).toBeLessThanOrEqual(32);
				await page.screenshot({
					path: testInfo.outputPath(`layout-${state}-${width}.png`),
					fullPage: true
				});
			} finally {
				release();
				await page.unroute('**/api/v1/health/**');
			}
		}
		const icon = page.locator('link[rel="icon"]');
		await expect(icon).toHaveAttribute('type', 'image/png');
		await expect(icon).toHaveAttribute('sizes', '64x64');
		const href = await icon.getAttribute('href');
		if (!href) throw new Error('파비콘 경로가 없습니다.');
		const response = await page.request.get(href);
		expect(response.status()).toBe(200);
		expect(response.headers()['content-type']).toContain('image/png');
		expect(
			await page.evaluate(async (src) => {
				const image = new Image();
				image.src = src;
				await image.decode();
				return image.naturalWidth;
			}, href)
		).toBe(64);
	});
}

test('Home에서 결측인 오늘 걸음 0보와 유효한 인정 운동 0분을 구분한다', async ({ page }) => {
	await page.route('**/api/v1/health/metrics/steps?*', (route) => {
		const data = homeDailyMetricFixture('steps');
		data.days.forEach((day) => {
			day.value = 0;
			day.missing = true;
			day.reason = 'UNSYNCED';
		});
		Object.assign(data.summary, {
			avg: null,
			total: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			missingDays: [],
			unsyncedDays: data.days.map((day) => day.date)
		});
		if (data.inProgress) Object.assign(data.inProgress, { value: 0, missing: true });
		return route.fulfill({ json: { data } });
	});
	await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
		const data = homeDailyMetricFixture('exercises');
		data.days.forEach((day) => {
			day.value = 0;
			day.missing = false;
			day.reason = null;
		});
		Object.assign(data.summary, {
			avg: null,
			total: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			completedDays: 0,
			missingDays: [],
			unsyncedDays: []
		});
		data.detail.sessions = [];
		if (data.inProgress) data.inProgress.value = 0;
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	const cards = page.getByRole('region', { name: '최근 건강 기록' });
	await expect(cards.getByRole('link', { name: /^걸음 수/ }).locator('.metric-value')).toHaveText(
		'동기화 대기'
	);
	await expect(cards.getByRole('link', { name: /^운동 시간/ }).locator('.metric-value')).toHaveText(
		'0분'
	);
});
