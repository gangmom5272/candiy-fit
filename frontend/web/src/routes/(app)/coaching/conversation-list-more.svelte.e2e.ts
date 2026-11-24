import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/** 20개씩 3페이지(20 · 20 · 5). 최근 것부터 1분씩 앞선다 */
const TOTAL = 45;
const PAGE_SIZE = 20;
const NOW = Date.now();

function conversation(index: number) {
	const at = new Date(NOW - index * 60_000).toISOString();
	return {
		id:
			index === 0 ? CONVERSATION_ID : `c0000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
		title: `대화 ${index + 1}`,
		summary: null,
		createdAt: at,
		updatedAt: at,
		lastRequestStatus: 'completed'
	};
}

/** cursor 'from-N'이면 N번째부터 limit개. 이어 읽기 요청을 몇 번 실패시킬지, 얼마나 늦게 답할지 정할 수 있다 */
async function mockList(page: Page, { failMore = 0, delayMore = 0 } = {}) {
	const requests: Array<{ cursor: string | null; limit: string | null }> = [];
	let failures = failMore;
	await page.route(/\/api\/v1\/coaching\/conversations(\?.*)?$/, async (route) => {
		const params = new URL(route.request().url()).searchParams;
		const cursor = params.get('cursor');
		requests.push({ cursor, limit: params.get('limit') });
		if (cursor && delayMore) await new Promise((done) => setTimeout(done, delayMore));
		if (cursor && failures > 0) {
			failures -= 1;
			return route.fulfill({ status: 500, json: { error: { code: 'INTERNAL_ERROR' } } });
		}
		const start = cursor ? Number(cursor.replace('from-', '')) : 0;
		const end = Math.min(start + Number(params.get('limit') ?? PAGE_SIZE), TOTAL);
		const items = Array.from({ length: end - start }, (_, k) => conversation(start + k));
		return route.fulfill({
			json: { data: { items, nextCursor: end < TOTAL ? `from-${end}` : null } }
		});
	});
	return requests;
}

test.use({ viewport: { width: 1280, height: 800 } });

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockCoachingServer(page, () => {});
});

function list(page: Page) {
	return page.getByRole('navigation', { name: '대화 목록' });
}

function links(page: Page) {
	return list(page).getByRole('link', { name: /^대화 \d+/ });
}

/** 목록 스크롤 영역을 맨 아래까지 내린다 */
async function scrollToBottom(page: Page) {
	await list(page)
		.getByRole('list')
		.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
}

test('맨 아래에 닿을 때마다 20개씩 한 번만 더 읽고, 연달아 읽지 않는다', async ({ page }) => {
	const requests = await mockList(page, { delayMore: 600 });
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	await expect(links(page)).toHaveCount(20);

	await scrollToBottom(page);
	// 읽는 동안 목록 끝에서 동그라미가 돈다
	await expect(list(page).getByRole('status')).toHaveText('이전 대화를 불러오는 중이에요');
	await expect(links(page)).toHaveCount(40);

	// 20개가 아래에 붙으면 맨 아래가 아니게 되어, 그대로 두면 더 읽지 않는다
	await page.waitForTimeout(1500);
	await expect(links(page)).toHaveCount(40);
	expect(requests).toHaveLength(2);

	await scrollToBottom(page);
	await expect(links(page)).toHaveCount(TOTAL);
	// 마지막 페이지까지 읽으면 끝 칸이 사라진다
	await expect(list(page).getByRole('status')).toHaveCount(0);

	expect(requests.map((request) => request.cursor)).toEqual([null, 'from-20', 'from-40']);
	expect(requests.every((request) => request.limit === '20')).toBe(true);
});

test('이어 읽기가 실패하면 보이던 목록은 두고, 다시 불러오기로 이어서 읽는다', async ({ page }) => {
	await mockList(page, { failMore: 1 });
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

	await scrollToBottom(page);
	const retry = list(page).getByRole('button', { name: '다시 불러오기' });
	await expect(retry).toBeVisible();
	await expect(list(page).getByText('이전 대화를 불러오지 못했어요.')).toBeVisible();
	await expect(links(page)).toHaveCount(20);

	await retry.click();
	await expect(links(page)).toHaveCount(40);
});
