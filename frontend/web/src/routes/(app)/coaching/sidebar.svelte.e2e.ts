import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

const TITLE = '지난주보다 많이 걸었어?';
const NOW_ISO = new Date().toISOString();

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockCoachingServer(page, () => {});
	const list = {
		data: {
			items: [
				{
					id: CONVERSATION_ID,
					title: TITLE,
					summary: null,
					createdAt: NOW_ISO,
					updatedAt: NOW_ISO,
					lastRequestStatus: 'completed'
				}
			],
			nextCursor: null
		}
	};
	await page.route('**/api/v1/coaching/conversations?**', (route) => route.fulfill({ json: list }));
	await page.route('**/api/v1/coaching/conversations', (route) => route.fulfill({ json: list }));
});

/** 화면에서 버튼이 놓인 자리(왼쪽 위 꼭짓점, 정수 px) */
async function position(locator: Locator) {
	const box = await locator.boundingBox();
	if (!box) throw new Error('버튼이 화면에 없습니다');
	return { x: Math.round(box.x), y: Math.round(box.y) };
}

function toggles(page: Page) {
	return {
		open: page.getByRole('button', { name: '대화 목록 열기' }),
		close: page.getByRole('button', { name: '대화 목록 접기' })
	};
}

test.describe('넓은 화면(1024px 이상)', () => {
	test.use({ viewport: { width: 1280, height: 800 } });

	test('첫 화면은 접힌 레일에서 시작하고, 열고 접어도 버튼이 같은 자리에 있다', async ({
		page
	}) => {
		await page.goto('/coaching', { waitUntil: 'networkidle' });
		const { open, close } = toggles(page);

		await expect(open).toBeVisible();
		await expect(page.getByRole('navigation', { name: '대화 목록' })).toBeHidden();
		const spot = await position(open);

		await open.click();
		await expect(page.getByRole('navigation', { name: '대화 목록' })).toBeVisible();
		await expect(close).toBeFocused();
		expect(await position(close)).toEqual(spot);

		await close.click();
		await expect(open).toBeFocused();
		expect(await position(open)).toEqual(spot);
	});

	test('대화 화면은 펼친 채 시작하고, 접어도 버튼이 같은 자리에 있다', async ({ page }) => {
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
		const { open, close } = toggles(page);

		await expect(page.getByRole('navigation', { name: '대화 목록' })).toBeVisible();
		const spot = await position(close);

		await close.click();
		await expect(page.getByRole('navigation', { name: '대화 목록' })).toBeHidden();
		expect(await position(open)).toEqual(spot);
	});

	test('두 화면 모두 제목이 목록 버튼과 같은 높이이고, 새 대화 버튼은 대화 화면 목록에만 있다', async ({
		page
	}) => {
		const middle = async (locator: Locator) => {
			const box = await locator.boundingBox();
			if (!box) throw new Error('요소가 화면에 없습니다');
			return Math.round(box.y + box.height / 2);
		};
		const title = page.getByRole('heading', { level: 1, name: 'AI 코치' });
		const newChat = page.getByRole('link', { name: '새 대화' });
		const { open, close } = toggles(page);

		// 첫 화면은 가운데 입력창이 곧 새 대화라 레일·펼친 목록 모두 새 대화 버튼이 없다
		await page.goto('/coaching', { waitUntil: 'networkidle' });
		expect(await middle(title)).toBe(await middle(open));
		await expect(newChat).toHaveCount(0);
		await open.click();
		await expect(page.getByRole('navigation', { name: '대화 목록' })).toBeVisible();
		await expect(newChat).toHaveCount(0);

		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
		expect(await middle(title)).toBe(await middle(close));
		const list = page.getByRole('navigation', { name: '대화 목록' });
		await list.getByRole('link', { name: '새 대화' }).click();
		await expect(page).toHaveURL('/coaching');
	});

	test('대화 화면 머리말 아래 구분선이 있고, 대화를 내려도 내용은 선 아래에만 보인다', async ({
		page
	}) => {
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
		const subtitle = page
			.getByRole('heading', { level: 1, name: 'AI 코치' })
			.locator('xpath=ancestor::header[1]')
			.locator('p');
		expect(await subtitle.evaluate((element) => getComputedStyle(element).borderBottomWidth)).toBe(
			'1px'
		);
		const [line, log] = await Promise.all([
			subtitle.boundingBox(),
			page.getByRole('log', { name: '대화 내용' }).boundingBox()
		]);
		expect(log!.y).toBeGreaterThanOrEqual(line!.y + line!.height);
	});
});

for (const viewport of [
	{ name: '휴대폰', width: 375, height: 812 },
	{ name: '태블릿', width: 900, height: 1000 }
]) {
	test.describe(`좁은 화면(${viewport.name} ${viewport.width}px)`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('떠 있는 버튼으로 서랍을 열고, Esc·바깥·숨은 닫기 버튼·대화 선택으로 닫는다', async ({
			page
		}) => {
			await page.goto('/coaching', { waitUntil: 'networkidle' });
			const { open } = toggles(page);
			const drawer = page.getByRole('dialog', { name: '대화 목록' });

			await expect(open).toBeVisible();

			await open.click();
			await expect(drawer.getByRole('navigation', { name: '대화 목록' })).toBeVisible();
			await page.keyboard.press('Escape');
			await expect(drawer).toBeHidden();

			await open.click();
			// 서랍 오른쪽 바깥(어두운 부분). 데스크톱 브라우저 세로 스크롤바 자리(오른쪽 끝 ~15px)는 피한다
			await page.mouse.click(viewport.width - 30, viewport.height / 2);
			await expect(drawer).toBeHidden();

			await open.click();
			// 서랍 안에는 눈에 보이는 접기 버튼이 없다(떠 있는 여는 버튼과 헷갈리지 않게).
			// 대신 보조기기·키보드용 닫기 버튼이 목록 끝에 있고, 키보드 포커스가 오면 그때만 보인다
			await expect(drawer.getByRole('button', { name: '대화 목록 접기' })).toHaveCount(0);
			const hiddenClose = drawer.getByRole('button', { name: '대화 목록 닫기' });
			await expect(hiddenClose).toBeAttached();
			expect((await hiddenClose.boundingBox())!.width).toBeLessThanOrEqual(1);
			// 실제 키보드 사용자처럼 Tab으로 닿는다(코드로 focus()하면 브라우저가 키보드 포커스로 보지 않아 안 보인다)
			for (let i = 0; i < 30; i += 1) {
				if (await hiddenClose.evaluate((element) => element === document.activeElement)) break;
				await page.keyboard.press('Tab');
			}
			await expect(hiddenClose).toBeFocused();
			expect((await hiddenClose.boundingBox())!.width).toBeGreaterThan(40);
			await page.keyboard.press('Enter');
			await expect(drawer).toBeHidden();

			await open.click();
			await drawer.getByRole('link', { name: TITLE }).click();
			await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
			await expect(drawer).toBeHidden();
			// 첫 화면·대화 화면 모두 머리말 없이 같은 버튼이 떠 있고, 제목은 보조기기용으로만 남는다
			await expect(open).toBeVisible();
			await expect(page.getByRole('heading', { level: 1, name: 'AI 코치' })).toBeAttached();
			await expect(page.getByRole('heading', { level: 1, name: 'AI 코치' })).not.toBeInViewport();
		});
	});
}
