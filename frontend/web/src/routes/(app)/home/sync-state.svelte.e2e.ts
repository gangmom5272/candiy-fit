import { expect, test, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import {
	mockHomeApi,
	homeDailyMetricFixture,
	lifestyleScoreFixture
} from '../../../lib/testing/home-api-fixture';
import { dailyMessageFixture } from '../../../lib/testing/daily-message-fixture';

const inlineNotice = '지금은 새 기록을 가져오지 않아요';
async function pageGeometry(page: Page) {
	await expect(
		page.getByRole('status').filter({ hasText: '기존 건강 기록을 조회하고 있어요.' })
	).toHaveCount(0);
	await page.evaluate(() => document.fonts.ready);
	return page.evaluate(() => ({
		documentHeight: document.documentElement.scrollHeight,
		horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
		footerY: document.querySelector('footer')?.getBoundingClientRect().y,
		cards: Array.from(document.querySelectorAll('.metric-card, .health-summary-card')).map(
			(card) => {
				const rect = card.getBoundingClientRect();
				return { width: rect.width, height: rect.height };
			}
		)
	}));
}

for (const path of ['/home', '/health']) {
	for (const [width, height] of [
		[1920, 1080],
		[1440, 900],
		[1366, 768],
		[1280, 720],
		[768, 1024],
		[360, 800]
	]) {
		test(`${path} ${width}×${height}: DISABLED 안내 없이 카드 크기와 페이지 높이를 보존한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height });
			await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
			await page.goto(path);
			await expect(page.locator('.metric-card, .health-summary-card')).toHaveCount(4);
			await expect(page.getByRole('alert')).toHaveCount(0);
			const before = await pageGeometry(page);
			await page.route('**/api/v1/health/sync', (route) =>
				route.fulfill({ json: { data: { status: 'DISABLED' } } })
			);
			await page.reload();
			const titleArea =
				path === '/home' ? page.locator('.hero-copy') : page.locator('main > header');
			const notice = titleArea.getByRole('status').filter({ hasText: inlineNotice });
			await expect(notice).toHaveCount(0);
			await expect(page.locator('.metric-card, .health-summary-card')).toHaveCount(4);
			const after = await pageGeometry(page);
			expect(after.cards).toEqual(before.cards);
			expect(after.horizontalOverflow).toBe(false);
			expect(after.documentHeight).toBe(before.documentHeight);
			expect(after.footerY).toBe(before.footerY);
			await test.info().attach('layout', {
				body: JSON.stringify({ path, width, height, before, after }),
				contentType: 'application/json'
			});
			await test.info().attach('screen', {
				body: await page.screenshot({ fullPage: true }),
				contentType: 'image/png'
			});
			if (path === '/home') {
				// 콘텐츠 높이는 viewport를 넘을 수 있다. 스크롤 후 푸터가 가려지지 않아야 한다.
				const footer = page.getByRole('contentinfo');
				await footer.scrollIntoViewIfNeeded();
				await expect(footer).toBeInViewport({ ratio: 1 });
				for (const name of ['이용약관', '개인정보처리방침']) {
					const item = footer.getByRole('button', { name, exact: true });
					await expect(item).toBeVisible();
					const bounds = await item.evaluate((element) => {
						const rect = element.getBoundingClientRect();
						const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
						const bottom =
							navigation && getComputedStyle(navigation).position === 'fixed'
								? navigation.getBoundingClientRect().top
								: innerHeight;
						return {
							top: rect.top,
							bottom: rect.bottom,
							visibleBottom: bottom,
							unobscured: element.contains(
								document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2)
							)
						};
					});
					expect(bounds.top).toBeGreaterThanOrEqual(0);
					expect(bounds.bottom).toBeLessThanOrEqual(bounds.visibleBottom);
					expect(bounds.unobscured).toBe(true);
				}
			}
		});
	}
	test(`${path}: DISABLED 안내 제거 후 좁은 화면과 200% 확대에서 제목이 잘리지 않는다`, async ({
		page
	}) => {
		await page.clock.setFixedTime(new Date('2026-10-26T03:00:00Z'));
		await page.route('**/api/v1/health/sync', (route) =>
			route.fulfill({ json: { data: { status: 'DISABLED' } } })
		);
		await page.setViewportSize({ width: 360, height: 800 });
		await page.goto(path);
		const notice = page.getByRole('status').filter({ hasText: inlineNotice });
		await expect(notice).toHaveCount(0);
		const title = page.getByRole('heading', { level: 1 });
		await expect(title).toBeVisible();
		for (const zoom of [1, 2]) {
			if (zoom === 2) await page.setViewportSize({ width: 1366, height: 768 });
			await page.evaluate((scale) => {
				document.documentElement.style.zoom = String(scale);
			}, zoom);
			await page.evaluate(() => document.fonts.ready);
			const bounds = await title.evaluate((element) => {
				const rect = element.getBoundingClientRect();
				return {
					left: rect.left,
					right: rect.right,
					viewport: innerWidth,
					clipped: element.scrollHeight > element.clientHeight,
					horizontal: document.documentElement.scrollWidth > innerWidth
				};
			});
			expect(bounds.left).toBeGreaterThanOrEqual(0);
			expect(bounds.right).toBeLessThanOrEqual(bounds.viewport);
			expect(bounds.clipped).toBe(false);
			expect(bounds.horizontal).toBe(false);
		}
	});
}

const artifacts = resolve('../../test-results/sync-227-state-review');
const linkedNotice =
	'앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.';
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

async function emptyHome(page: Page) {
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
		Object.assign(data.days[0], { value: null, missing: true, reason: 'UNSYNCED' });
		Object.assign(data.summary, {
			avg: null,
			total: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			unsyncedDays: [data.days[0].date]
		});
		Object.assign(data.detail, { nights: [], dailyRanges: [], sessions: [] });
		if (data.inProgress) Object.assign(data.inProgress, { value: null, missing: true, asOf: null });
		return route.fulfill({ json: { data } });
	});
	const message = dailyMessageFixture();
	Object.assign(message, {
		status: 'WAITING',
		code: 'PENDING_RECORDS',
		title: '오늘의 기록을 아직 확인하지 못했어요',
		description: '괜찮다면 앱의 동기화 상태를 확인해 주세요.',
		scoreComputedAt: null,
		dataAsOf: { steps: null, exercises: null, sleeps: null }
	});
	await page.route('**/api/v1/health/daily-message', (route) =>
		route.fulfill({ json: { data: message } })
	);
	return message;
}

for (const post of [
	'SYNCED',
	'DISABLED',
	'SYNTHETIC',
	'SKIPPED_RECENT',
	'BUSY',
	'TIMEOUT',
	'FAIL'
]) {
	for (const empty of [false, true]) {
		test(`${post} + GET ${empty ? 'UNSYNCED null' : '저장 데이터'}: POST와 일곱 화면 요소를 독립적으로 표시한다`, async ({
			page
		}) => {
			let posts = 0;
			await page.route('**/api/v1/health/sync', (route) => {
				posts++;
				return post === 'TIMEOUT' || post === 'FAIL'
					? route.fulfill({
							status: 503,
							json: {
								error: {
									code: 'HEALTH_SYNC_UNAVAILABLE',
									message: 'synthetic',
									context: { reason: post === 'TIMEOUT' ? 'TIMEOUT' : 'METRIC_FAILED', metrics: [] }
								}
							}
						})
					: route.fulfill({ json: { data: { status: post } } });
			});
			const message = empty ? await emptyHome(page) : dailyMessageFixture();
			await page.goto('/home');
			const cards = page.getByRole('region', { name: '최근 건강 기록' });
			if (empty) {
				await expect(page.getByLabel('오늘의 점수 동기화 대기', { exact: true })).toBeVisible();
				await expect(
					page.getByRole('group', { name: '건강 상태' }).locator('.status-label')
				).toHaveText(['동기화 대기', '동기화 대기', '동기화 대기']);
				await expect(cards.locator('.metric-value')).toHaveText([
					'동기화 대기',
					'동기화 대기',
					'동기화 대기',
					'동기화 대기'
				]);
			} else {
				await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
				await expect(
					page.getByRole('group', { name: '건강 상태' }).locator('.status-label')
				).toHaveText(['훌륭', '관심', '안정']);
				await expect(cards.locator('.metric-value')).toHaveText([
					'3,200보',
					'18분',
					'7시간 0분',
					'64.5 bpm'
				]);
			}
			await expect(
				page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
			).toHaveText(message.title);
			const notices: Record<string, string> = {
				SYNTHETIC: '샘플 계정이므로 외부 데이터 갱신을 생략했어요.',
				SKIPPED_RECENT: '최근 갱신 시도가 있어',
				BUSY: '다른 갱신 작업의 완료 여부는 확인할 수 없어요.',
				TIMEOUT: '서버에서 작업이 계속될 수 있어요.',
				FAIL: '건강 기록을 갱신하지 못했어요.'
			};
			if (post === 'DISABLED')
				await expect(page.getByText(inlineNotice, { exact: true })).toHaveCount(0);
			else if (post === 'SKIPPED_RECENT' || post === 'BUSY' || post === 'TIMEOUT') {
				await expect(page.getByText(notices[post])).toHaveCount(0);
				await expect(page.getByText('건강 데이터를 갱신하고 있어요.', { exact: true })).toHaveCount(
					0
				);
				await expect(
					page.getByRole('button', { name: '건강 기록 다시 확인', exact: true })
				).toHaveCount(0);
			} else if (post !== 'SYNCED')
				await expect(page.getByRole('status').filter({ hasText: notices[post] })).toBeVisible();
			await expect(page.getByText('건강 기록을 갱신했어요.', { exact: true })).toHaveCount(0);
			expect(posts).toBe(1);
		});
	}
}

for (const width of [1366, 360]) {
	for (const empty of [false, true]) {
		test(`DISABLED ${width}px ${empty ? '빈 데이터' : '저장 데이터'}: 정렬과 문구를 그대로 검증·캡처한다`, async ({
			page
		}) => {
			await page.setViewportSize({ width, height: 800 });
			await page.route('**/api/v1/health/sync', (route) =>
				route.fulfill({ json: { data: { status: 'DISABLED' } } })
			);
			const message = empty ? await emptyHome(page) : dailyMessageFixture();
			await page.goto('/home');
			await expect(
				page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
			).toHaveText(message.title);
			await page.evaluate(() => document.fonts.ready);
			const layout = await page.locator('.metric-card').evaluateAll((cards) =>
				cards.map((card) => {
					const heading = card.querySelector('h2');
					const group = heading?.parentElement;
					const icon = group?.querySelector('span');
					const value = card.querySelector('.metric-value');
					if (!group || !icon || !value) throw new Error('카드 요소가 없습니다.');
					const g = group.getBoundingClientRect(),
						i = icon.getBoundingClientRect(),
						v = value.getBoundingClientRect();
					return {
						titleLeft: Math.abs(i.left - g.left),
						valueCenter: Math.abs(v.left + v.width / 2 - g.left - g.width / 2),
						overflow: card.scrollWidth > card.clientWidth || card.scrollHeight > card.clientHeight
					};
				})
			);
			for (const card of layout) {
				expect(card.titleLeft).toBeLessThanOrEqual(1);
				expect(card.valueCenter).toBeLessThanOrEqual(1);
				expect(card.overflow).toBe(false);
			}
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
			mkdirSync(artifacts, { recursive: true });
			await page.screenshot({
				path: resolve(artifacts, `disabled-${empty ? 'empty' : 'normal'}-${width}.png`),
				fullPage: true
			});
		});
	}
}

for (const post of ['FAIL', 'DISABLED']) {
	for (const code of ['CANDIY_USER_NOT_LINKED', 'HEALTH_DATA_UNAVAILABLE']) {
		test(`POST ${post} + 홈 GET ${code}: 미연동과 조회 장애를 구분한다`, async ({ page }) => {
			await page.route('**/api/v1/health/sync', (route) =>
				post === 'DISABLED'
					? route.fulfill({ json: { data: { status: post } } })
					: route.fulfill({
							status: 503,
							json: {
								error: {
									code: 'HEALTH_SYNC_UNAVAILABLE',
									context: { reason: 'METRIC_FAILED', metrics: [] }
								}
							}
						})
			);
			await page.route('**/api/v1/health/**', (route) =>
				route.request().method() === 'GET'
					? route.fulfill({
							status: code === 'CANDIY_USER_NOT_LINKED' ? 409 : 503,
							json: {
								error: {
									code,
									message: 'Internal diagnostic must not be displayed',
									context: { reason: 'LOOKUP_FAILED', metrics: [] }
								}
							}
						})
					: route.fallback()
			);
			await page.goto('/home');
			await expect(page.getByLabel('오늘의 점수 조회 실패', { exact: true })).toBeVisible();
			await expect(
				page.getByRole('region', { name: '최근 건강 기록' }).locator('.metric-value')
			).toHaveText(['조회 실패', '조회 실패', '조회 실패', '조회 실패']);
			await expect(
				page.getByRole('alert').filter({
					hasText:
						code === 'CANDIY_USER_NOT_LINKED'
							? linkedNotice
							: '건강 기록을 불러오지 못했어요. 새로고침 후 다시 확인해 주세요.'
				})
			).toHaveCount(4);
			await expect(page.locator('main')).not.toContainText('Internal diagnostic');
			await expect(page.getByText(inlineNotice, { exact: true })).toHaveCount(0);
		});
	}
}

for (const nullValue of [false, true]) {
	test(`DISABLED + 운동 ${nullValue ? 'null' : '유효한 0'}: 결측을 0으로 바꾸지 않는다`, async ({
		page
	}) => {
		await page.route('**/api/v1/health/sync', (route) =>
			route.fulfill({ json: { data: { status: 'DISABLED' } } })
		);
		await page.route('**/api/v1/health/metrics/exercises?*', (route) => {
			const data = homeDailyMetricFixture('exercises');
			Object.assign(data.days[0], {
				value: nullValue ? null : 0,
				missing: nullValue,
				reason: nullValue ? 'NULL_VALUE' : null
			});
			if (data.inProgress)
				Object.assign(data.inProgress, { value: nullValue ? null : 0, missing: nullValue });
			return route.fulfill({ json: { data } });
		});
		await page.goto('/home');
		await expect(
			page.getByRole('link', { name: /^운동 시간/ }).locator('.metric-value')
		).toHaveText(nullValue ? '값 없음' : '0분');
	});
}
