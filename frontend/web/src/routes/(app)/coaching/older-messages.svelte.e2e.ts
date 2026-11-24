import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

const NOW_ISO = '2026-10-27T01:00:00Z';

/** 질문·답변 i번째 쌍 (sequenceNo 2i-1, 2i) */
function pair(i: number) {
	const requestId = `44444444-4444-4444-8444-${String(i).padStart(12, '0')}`;
	const base = {
		requestId,
		requestStatus: 'completed',
		errorCode: null,
		createdAt: NOW_ISO,
		evidence: [],
		errorContext: null
	};
	return [
		{ ...base, id: `u${i}`, role: 'user', sequenceNo: i * 2 - 1, content: `질문 ${i}` },
		{
			...base,
			id: `a${i}`,
			role: 'assistant',
			sequenceNo: i * 2,
			content: `답변 ${i}\n두 번째 줄\n세 번째 줄`
		}
	];
}

/**
 * 40쌍(80개) 대화. 서버처럼 처음에는 최근 50개(16~40쌍 뒤쪽 + 15번째 답변)만 주고,
 * beforeSequenceNo로 그 앞을 준다
 */
async function mockLongConversation(page: Page) {
	const all = Array.from({ length: 40 }, (_, i) => pair(i + 1)).flat();
	const requests: (string | null)[] = [];
	await page.route(`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages**`, (route) => {
		const before = new URL(route.request().url()).searchParams.get('beforeSequenceNo');
		requests.push(before);
		const upTo = before ? Number(before) - 1 : all.length;
		const items = all.filter((m) => m.sequenceNo <= upTo).slice(-50);
		const first = items[0]?.sequenceNo ?? 1;
		return route.fulfill({
			json: {
				data: {
					conversation: {
						id: CONVERSATION_ID,
						title: '긴 대화',
						summary: null,
						createdAt: NOW_ISO,
						updatedAt: NOW_ISO
					},
					items,
					nextBeforeSequenceNo: first > 1 ? first : null
				}
			}
		});
	});
	return requests;
}

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockCoachingServer(page, () => {});
});

// 278: 메시지가 50개를 넘는 대화도 앞부분을 볼 수 있다. 붙인 뒤에도 보던 메시지 위치가 그대로다
for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 375, height: 812 }
]) {
	test(`${viewport.width}px: 이전 대화 보기로 앞부분을 불러오고 보던 위치를 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		const requests = await mockLongConversation(page);
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

		const log = page.getByRole('log', { name: '대화 내용' });
		await expect(log.getByText('질문 40', { exact: true })).toBeVisible();
		await expect(log.getByText('질문 1', { exact: true })).toHaveCount(0);

		const more = log.getByRole('button', { name: '이전 대화 보기' });
		await more.scrollIntoViewIfNeeded();
		const anchor = log.getByText('질문 16', { exact: true });
		const before = (await anchor.boundingBox())!.y;
		await more.click();

		await expect(log.getByText('질문 1', { exact: true })).toBeAttached();
		await expect(more).toHaveCount(0);
		expect(requests).toEqual([null, '31']);
		// 앞에 붙은 메시지만큼 내려 주어, 누르기 전에 보던 메시지가 같은 자리에 있다
		expect(Math.abs((await anchor.boundingBox())!.y - before)).toBeLessThan(4);
	});
}

test('이전 대화를 불러오지 못하면 다시 불러오기로 다시 시도한다', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await mockLongConversation(page);
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	let fail = true;
	await page.route(
		`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages?*beforeSequenceNo*`,
		(route) =>
			fail
				? route.fulfill({
						status: 503,
						json: { error: { code: 'INTERNAL_ERROR', message: '오류' } }
					})
				: route.fallback()
	);

	const log = page.getByRole('log', { name: '대화 내용' });
	await log.getByRole('button', { name: '이전 대화 보기' }).click();
	await expect(log.getByRole('alert')).toContainText('이전 대화를 불러오지 못했어요.');
	fail = false;
	await log.getByRole('button', { name: '다시 불러오기' }).click();
	await expect(log.getByText('질문 1', { exact: true })).toBeAttached();
});
