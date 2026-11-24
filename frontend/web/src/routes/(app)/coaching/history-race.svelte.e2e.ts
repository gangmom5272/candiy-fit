import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/** e2e 전용 합성 값. 실제 대화가 아니다. */
const A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const NOW = '2026-11-10T03:00:00Z';

function conversation(id: string, title: string) {
	return { id, title, summary: null, createdAt: NOW, updatedAt: NOW };
}

function messagePage(id: string, title: string, content: string) {
	return {
		data: {
			conversation: conversation(id, title),
			items: [
				{
					id: `u-${content}`,
					requestId: `r-${content}`,
					role: 'user',
					sequenceNo: 1,
					content,
					requestStatus: 'completed',
					errorCode: null,
					createdAt: NOW,
					evidence: [],
					errorContext: null
				}
			],
			nextBeforeSequenceNo: null
		}
	};
}

test.use({ viewport: { width: 1280, height: 800 } });

test('A → B → A로 돌아왔을 때 처음 보낸 느린 A 응답이 새 이력을 덮어쓰지 않는다', async ({
	page
}) => {
	await mockSignedIn(page);
	await mockCoachingServer(page, () => {});

	let aRequests = 0;
	let releaseFirstA: () => void = () => {};
	const firstAHeld = new Promise<void>((resolve) => (releaseFirstA = resolve));

	await page.route(
		(url) => url.pathname.startsWith('/api/v1/coaching/conversations'),
		async (route) => {
			const { pathname } = new URL(route.request().url());
			if (pathname === '/api/v1/coaching/conversations') {
				return route.fulfill({
					json: {
						data: {
							items: [
								{ ...conversation(A, '대화 A'), lastRequestStatus: 'completed' },
								{ ...conversation(B, '대화 B'), lastRequestStatus: 'completed' }
							],
							nextCursor: null
						}
					}
				});
			}
			if (pathname.includes(B)) {
				return route.fulfill({ json: messagePage(B, '대화 B', 'B의 질문') });
			}
			aRequests += 1;
			if (aRequests === 1) {
				// 처음 보낸 A 조회는 늦게 끝난다. 그 사이 화면은 B로 갔다가 A로 돌아온다
				await firstAHeld;
				// 화면이 이 조회를 취소했다면 응답을 줄 곳이 없다
				return route.fulfill({ json: messagePage(A, '대화 A', '옛 A 이력') }).catch(() => {});
			}
			return route.fulfill({ json: messagePage(A, '대화 A', '새 A 이력') });
		}
	);

	await page.goto(`/coaching/${A}`);
	const list = page.getByRole('navigation', { name: '대화 목록' });
	const log = page.getByRole('log', { name: '대화 내용' });

	await list.getByRole('link', { name: '대화 B' }).click();
	await expect(log.getByText('B의 질문')).toBeVisible();

	await list.getByRole('link', { name: '대화 A' }).click();
	await expect(log.getByText('새 A 이력')).toBeVisible();

	// 이제 처음 보낸 A 조회가 끝난다. 새 이력이 그대로여야 한다
	releaseFirstA();
	await page.waitForTimeout(500);
	await expect(log.getByText('새 A 이력')).toBeVisible();
	await expect(log.getByText('옛 A 이력')).toHaveCount(0);
	expect(aRequests).toBe(2);
});
