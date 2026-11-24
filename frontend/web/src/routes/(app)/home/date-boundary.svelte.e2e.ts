import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import { dailyMessageFixture } from '../../../lib/testing/daily-message-fixture';

const previous = '2026-10-26';
const next = '2026-10-27';

async function prepareHome(page: Page) {
	await mockSignedIn(page);
	await mockHomeApi(page, [previous, next]);
	await page.route('**/api/v1/health/daily-message', async (route) => {
		const date = await page.evaluate(() =>
			new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 10)
		);
		const data = dailyMessageFixture(date);
		data.title = `기준일 ${date}`;
		await route.fulfill({ json: { data } });
	});
}

function trackDates(page: Page) {
	const requests: { path: string; date: string | null }[] = [];
	page.on('request', (request) => {
		const url = new URL(request.url());
		if (url.pathname.includes('/health/metrics/') || url.pathname.endsWith('/lifestyle-score'))
			requests.push({
				path: url.pathname.split('/').at(-1) ?? '',
				date: url.searchParams.get('date') ?? url.searchParams.get('start')
			});
	});
	return requests;
}

async function expectDate(page: Page, requests: ReturnType<typeof trackDates>, date: string) {
	await expect(
		page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
	).toHaveText(`기준일 ${date}`);
	await expect
		.poll(() =>
			requests
				.filter((request) => request.date === date)
				.map((request) => request.path)
				.sort()
		)
		.toEqual(['exercises', 'lifestyle-score', 'sleeps', 'steps']);
	await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
}

test('KST 23:59 → 00:00 → 00:01에 한마디·점수·건강 카드의 기준일을 함께 변경한다', async ({
	page
}) => {
	await prepareHome(page);
	await page.clock.install({ time: new Date('2026-10-26T14:00:00Z') });
	await page.clock.pauseAt(new Date('2026-10-26T14:59:00Z'));
	const requests = trackDates(page);
	await page.goto('/home');
	await expectDate(page, requests, previous);
	await page.clock.fastForward(60000);
	await expectDate(page, requests, next);
	const count = requests.length;
	await page.clock.fastForward(60000);
	expect(requests).toHaveLength(count);
	await page.clock.resume();
	await page.reload();
	await expect(page.getByRole('region', { name: '오늘의 한마디', exact: true })).toContainText(
		`기준일 ${next}`
	);
	await expect.poll(() => requests.filter((request) => request.date === next).length).toBe(8);
});

test('백그라운드 복귀 및 재진입은 UTC와 다른 새 KST 날짜를 사용한다', async ({ page }) => {
	await prepareHome(page);
	await page.clock.setFixedTime(new Date('2026-10-26T14:59:00Z'));
	const requests = trackDates(page);
	await page.goto('/home');
	await expectDate(page, requests, previous);
	await page.clock.setFixedTime(new Date('2026-10-26T15:01:00Z'));
	await page.evaluate(() => window.dispatchEvent(new Event('focus')));
	await expectDate(page, requests, next);
	await page.goto('/health');
	requests.length = 0;
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '홈', exact: true })
		.click();
	await expectDate(page, requests, next);
});

test('취소를 무시하고 늦게 도착한 전날 점수·카드 응답은 새 날짜를 덮어쓰지 않는다', async ({
	page
}) => {
	await prepareHome(page);
	await page.clock.install({ time: new Date('2026-10-26T14:00:00Z') });
	await page.clock.pauseAt(new Date('2026-10-26T14:59:59Z'));
	await page.addInitScript(() => {
		const original = window.fetch;
		window.fetch = async (input, options) => {
			const url = String(input);
			const oldDate =
				url.includes('2026-10-26') &&
				(url.includes('/metrics/') || url.includes('/lifestyle-score'));
			const response = await original(input, oldDate ? { ...options, signal: undefined } : options);
			if (oldDate) {
				await new Promise<void>((resolve) =>
					window.addEventListener('release-old-home', () => resolve(), { once: true })
				);
				const body = await response.json();
				if (url.includes('/lifestyle-score')) body.data.total.score = 99;
				else body.data.days[0].value = 9999;
				return new Response(JSON.stringify(body), {
					status: 200,
					headers: { 'content-type': 'application/json' }
				});
			}
			return response;
		};
	});
	const requests = trackDates(page);
	await page.goto('/home');
	await expect.poll(() => requests.length).toBe(4);
	await page.clock.fastForward(1100);
	await expectDate(page, requests, next);
	await page.evaluate(() => window.dispatchEvent(new Event('release-old-home')));
	await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점', { exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: /9,999/ })).toHaveCount(0);
});

test('자정에 한마디 API가 실패해도 점수·건강 카드의 기준일은 갱신한다', async ({ page }) => {
	await prepareHome(page);
	await page.clock.install({ time: new Date('2026-10-26T14:00:00Z') });
	await page.clock.pauseAt(new Date('2026-10-26T14:59:59Z'));
	const requests = trackDates(page);
	await page.goto('/home');
	await expectDate(page, requests, previous);
	await page.route('**/api/v1/health/daily-message', (route) =>
		route.fulfill({ status: 503, json: { error: { code: 'HEALTH_DATA_UNAVAILABLE' } } })
	);
	await page.clock.fastForward(1100);
	const message = page.getByRole('region', { name: '오늘의 한마디', exact: true });
	await expect(message.getByRole('alert')).toBeVisible();
	await expect(message).not.toContainText(`기준일 ${previous}`);
	await expect.poll(() => requests.filter((request) => request.date === next).length).toBe(4);
	await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
});
