import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../lib/testing/e2e-coaching';
import { mockHomeApi } from '../lib/testing/home-api-fixture';

/**
 * 키보드 포커스 표시(S15P31A403-308). 기본은 브랜드색 테두리이고, 브라우저 기본 검은 테두리는 어디에도 나오지 않는다.
 * 로고·사용자 메뉴·대화 목록 버튼은 팀 결정으로 테두리 대신 배경으로 보여 준다.
 */
test.beforeEach(({ page }) => mockSignedIn(page));

const BRAND = 'rgb(248, 90, 90)';
const PRIMARY_SOFT = 'rgb(255, 244, 243)';
const CORAL_SOFT = 'rgb(249, 224, 221)';
/** 테두리 대신 배경으로 포커스를 보여 주는 요소 */
const BACKGROUND_FOCUS: Record<string, string> = {
	'CANDiY-FiT 홈': PRIMARY_SOFT,
	'사용자 메뉴': CORAL_SOFT
};

/** Tab 으로 차례대로 옮기며 포커스된 요소의 테두리를 모은다 */
async function tabOutlines(page: Page, count: number) {
	const seen: { name: string; style: string; color: string; background: string }[] = [];
	for (let i = 0; i < count; i++) {
		await page.keyboard.press('Tab');
		seen.push(
			await page.evaluate(() => {
				const el = document.activeElement as HTMLElement;
				const style = getComputedStyle(el);
				return {
					name: el.getAttribute('aria-label') ?? el.textContent?.trim().slice(0, 20) ?? el.tagName,
					style: style.outlineStyle,
					color: style.outlineColor,
					background: style.backgroundColor
				};
			})
		);
	}
	return seen;
}

for (const width of [360, 1366]) {
	test(`${width}px 홈 헤더의 주 메뉴는 브랜드색 테두리, 로고·사용자 메뉴는 배경으로 포커스를 보여 준다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await mockHomeApi(page);
		await page.goto('/home');
		await page.evaluate(() => document.fonts.ready);
		// 로고에서 시작해 키보드로 한 칸 뒤로 갔다가 돌아와 focus-visible 상태로 만든다
		await page.getByRole('link', { name: 'CANDiY-FiT 홈', exact: true }).focus();
		await page.keyboard.press('Shift+Tab');
		const outlines = await tabOutlines(page, 5);
		const names = outlines.map((item) => item.name);
		expect(names).toEqual(expect.arrayContaining(['CANDiY-FiT 홈', '사용자 메뉴']));
		for (const item of outlines) {
			const background = BACKGROUND_FOCUS[item.name];
			if (background) expect(item, item.name).toMatchObject({ style: 'none', background });
			else expect(item, item.name).toMatchObject({ style: 'solid', color: BRAND });
		}
	});

	test(`${width}px 대화 화면의 대화 목록 버튼은 테두리 없이 배경으로 포커스를 보여 준다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await mockCoachingServer(page, () => {});
		const list = { data: { items: [], nextCursor: null } };
		await page.route('**/api/v1/coaching/conversations?**', (route) =>
			route.fulfill({ json: list })
		);
		await page.route('**/api/v1/coaching/conversations', (route) => route.fulfill({ json: list }));
		await page.route(`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages**`, (route) =>
			route.fulfill({
				json: {
					data: {
						conversation: {
							id: CONVERSATION_ID,
							title: '합성 대화',
							summary: null,
							createdAt: '2026-11-06T00:00:00Z',
							updatedAt: '2026-11-06T00:00:00Z'
						},
						items: [],
						nextBeforeSequenceNo: null
					}
				}
			})
		);
		await page.goto(`/coaching/${CONVERSATION_ID}`);
		const toggle = page.getByRole('button', { name: /대화 목록 (열기|접기)/ }).first();
		await expect(toggle).toBeVisible();
		await toggle.focus();
		// focus() 는 focus-visible 을 켜지 않을 수 있어 키보드로 한 번 오가며 확인한다
		await page.keyboard.press('Shift+Tab');
		await page.keyboard.press('Tab');
		await expect(toggle).toBeFocused();
		await expect(toggle).toHaveCSS('outline-style', 'none');
		await expect(toggle).toHaveCSS('background-color', PRIMARY_SOFT);
	});

	// S15P31A403-439 입력칸은 테두리 대신 틀의 선 색으로 포커스를 보여 준다. 전에는 아무 표시가 없었다
	test(`${width}px 질문 입력칸에 포커스가 가면 틀의 선 색이 포커스 색으로 바뀐다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await mockCoachingServer(page, () => {});
		const list = { data: { items: [], nextCursor: null } };
		await page.route('**/api/v1/coaching/conversations?**', (route) =>
			route.fulfill({ json: list })
		);
		await page.route('**/api/v1/coaching/conversations', (route) => route.fulfill({ json: list }));
		await page.goto('/coaching');
		const input = page.getByRole('textbox', { name: '질문 입력' });
		const frame = page.locator('form').filter({ has: input });
		await expect(input).toBeVisible();
		const resting = await frame.evaluate((element) => getComputedStyle(element).borderTopColor);
		expect(resting).not.toBe(BRAND);
		const size = await frame.boundingBox();
		// 키보드로 와도, 마우스로 눌러도 표시된다
		await input.focus();
		await expect(frame).toHaveCSS('border-top-color', BRAND);
		// 선 색만 바뀌고 크기는 그대로다
		expect(await frame.boundingBox()).toEqual(size);
		// 보내기 버튼으로 넘어가면 틀은 돌아가고 버튼에 테두리가 생긴다
		await page.keyboard.press('Tab');
		const send = page.getByRole('button', { name: '질문 보내기' });
		await expect(send).toBeFocused();
		await expect(frame).toHaveCSS('border-top-color', resting);
		await expect(send).toHaveCSS('outline-color', BRAND);
		await expect(send).not.toHaveCSS('outline-style', 'none');
	});
}
