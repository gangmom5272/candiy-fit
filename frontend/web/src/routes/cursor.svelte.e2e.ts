import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../lib/testing/e2e-coaching';
import { mockHomeApi } from '../lib/testing/home-api-fixture';

/**
 * 클릭하는 요소는 손가락 커서다(S15P31A403-315). 화면에 보이는 누를 수 있는 요소를 모두 훑어
 * 화살표 커서로 남은 것이 없는지 본다. 누를 수 없는(disabled) 버튼은 대상이 아니다. 합성 데이터다.
 */
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await mockCoachingServer(page, () => {});
	const list = {
		data: {
			items: [
				{
					id: CONVERSATION_ID,
					title: '합성 대화',
					summary: null,
					createdAt: '2026-11-09T00:00:00Z',
					updatedAt: '2026-11-09T00:00:00Z',
					lastRequestStatus: 'completed'
				}
			],
			nextCursor: null
		}
	};
	await page.route('**/api/v1/coaching/conversations?**', (route) => route.fulfill({ json: list }));
	await page.route('**/api/v1/coaching/conversations', (route) => route.fulfill({ json: list }));
});

/** 보이는 클릭 요소 중 커서가 손가락이 아닌 것 */
function notPointer(page: Page) {
	return page.evaluate(() =>
		[
			...document.querySelectorAll<HTMLElement>(
				'button:not(:disabled), a[href], [role="tab"], [role="menuitem"], summary'
			)
		]
			.filter((element) => element.getClientRects().length > 0 && !element.closest('[inert]'))
			.filter((element) => getComputedStyle(element).cursor !== 'pointer')
			.map(
				(element) => element.getAttribute('aria-label') ?? element.textContent?.trim().slice(0, 20)
			)
	);
}

for (const width of [390, 1366]) {
	test(`${width}px AI 코치·마이페이지·헤더 메뉴의 클릭 요소는 모두 손가락 커서다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		for (const path of ['/coaching', '/my']) {
			await page.goto(path, { waitUntil: 'networkidle' });
			expect(await notPointer(page), path).toEqual([]);
		}
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await expect(page.getByRole('menu')).toBeVisible();
		expect(await notPointer(page), '사용자 메뉴').toEqual([]);
	});
}

test('누를 수 없는 버튼은 손가락 커서가 아니다', async ({ page }) => {
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	const send = page.getByRole('button', { name: '질문 보내기' });
	await expect(send).toHaveCSS('cursor', 'pointer');
	await send.evaluate((element) => ((element as HTMLButtonElement).disabled = true));
	await expect(send).not.toHaveCSS('cursor', 'pointer');
});
