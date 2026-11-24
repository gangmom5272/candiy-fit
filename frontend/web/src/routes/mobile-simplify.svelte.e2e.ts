import { expect, test } from '@playwright/test';
import { e2eUser, enterOnboarding, mockSignedIn } from '../lib/testing/e2e-auth';

/**
 * 휴대폰 화면 정리(S15P31A403-347).
 * 좁은 화면에서는 설명 패널을 빼고 입력·버튼이 바로 보이게 한다. 태블릿 이상은 그대로다.
 */
const PHONE = { width: 390, height: 844 };
const TABLET = { width: 768, height: 1024 };

test.describe('로그인', () => {
	test('휴대폰에서는 소개 상자 없이 로그인 내용이 카드 가운데에 놓인다', async ({ page }) => {
		await page.setViewportSize(PHONE);
		await page.goto('/login', { waitUntil: 'networkidle' });

		await expect(page.getByRole('region', { name: '캔디핏 소개' })).toBeHidden();
		const heading = page.getByRole('heading', { level: 1, name: '캔디핏 로그인' });
		await expect(heading).toBeVisible();
		await expect(page.getByRole('button', { name: '카카오로 시작하기' })).toBeVisible();

		// 내용 묶음(제목부터 안내 문구까지)의 위아래 여백이 비슷하다
		const { top, bottom, viewport } = await page.evaluate(() => {
			const content = document.querySelector('main h1')!.parentElement!;
			const box = content.getBoundingClientRect();
			return { top: box.top, bottom: box.bottom, viewport: window.innerHeight };
		});
		expect(Math.abs(top - (viewport - bottom))).toBeLessThanOrEqual(24);
		expect(
			await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
		).toBe(true);
	});

	test('태블릿에서는 소개 상자와 안내 목록이 그대로 보인다', async ({ page }) => {
		await page.setViewportSize(TABLET);
		await page.goto('/login', { waitUntil: 'networkidle' });

		const intro = page.getByRole('region', { name: '캔디핏 소개' });
		await expect(intro).toBeVisible();
		await expect(intro.getByText('워치 기록을 자동으로')).toBeVisible();
	});
});

test.describe('회원가입', () => {
	test('휴대폰에서는 기본 정보 안내 없이 입력칸이 바로 보인다', async ({ page }) => {
		await page.setViewportSize(PHONE);
		await enterOnboarding(page);

		await expect(page.getByRole('region', { name: '기본 정보 안내' })).toBeHidden();
		await expect(page.getByRole('heading', { level: 1, name: '기본 정보 입력' })).toBeInViewport();
		await expect(page.getByLabel('닉네임')).toBeInViewport();
	});

	test('태블릿에서는 기본 정보 안내가 그대로 보인다', async ({ page }) => {
		await page.setViewportSize(TABLET);
		await enterOnboarding(page);

		await expect(page.getByRole('region', { name: '기본 정보 안내' })).toBeVisible();
	});

	for (const viewport of [PHONE, { width: 1366, height: 768 }]) {
		test(`${viewport.width}px: 돌아가기는 가입하기 왼쪽에 같은 높이로 놓이고 로그인 화면으로 보낸다`, async ({
			page
		}) => {
			await page.setViewportSize(viewport);
			await enterOnboarding(page);

			const back = page.getByRole('button', { name: '돌아가기' });
			const submit = page.getByRole('button', { name: '가입하기' });
			const [backBox, submitBox] = await Promise.all([back.boundingBox(), submit.boundingBox()]);
			expect(backBox!.x + backBox!.width).toBeLessThanOrEqual(submitBox!.x);
			expect(backBox!.y).toBe(submitBox!.y);
			expect(backBox!.height).toBe(submitBox!.height);
			expect(backBox!.width).toBe(submitBox!.width);
			// 돌아가기는 폼을 제출하지 않는다
			await expect(back).toHaveAttribute('type', 'button');

			await back.click();
			await expect(page).toHaveURL('/login');
		});
	}
});

test.describe('마이페이지', () => {
	test.beforeEach(async ({ page }) => {
		await mockSignedIn(page);
		await page.route('**/api/v1/users/me', (route) => route.fulfill({ json: { data: e2eUser } }));
	});

	test('휴대폰에서는 기본 정보 안내 없이 수정 폼이 바로 보이고, 돌아가기 버튼은 없다', async ({
		page
	}) => {
		await page.setViewportSize(PHONE);
		await page.goto('/my', { waitUntil: 'networkidle' });

		await expect(page.getByRole('region', { name: '기본 정보 안내' })).toBeHidden();
		await expect(page.getByRole('heading', { level: 1, name: '기본 정보 수정' })).toBeInViewport();
		await expect(page.getByRole('button', { name: '돌아가기' })).toHaveCount(0);
	});

	test('태블릿에서는 기본 정보 안내가 그대로 보인다', async ({ page }) => {
		await page.setViewportSize(TABLET);
		await page.goto('/my', { waitUntil: 'networkidle' });

		await expect(page.getByRole('region', { name: '기본 정보 안내' })).toBeVisible();
	});
});
