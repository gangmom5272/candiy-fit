import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { mockHomeApi } from '../lib/testing/home-api-fixture';

const missingPath = '/this-page-does-not-exist';

for (const [width, height] of [
	[1920, 1080],
	[1366, 768],
	[1280, 640],
	[1024, 768],
	[768, 1024],
	[768, 600],
	[360, 800]
]) {
	test(`${width}×${height}에서 미등록 URL의 404 구성과 이미지 비율을 유지한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		const response = await page.goto(missingPath);
		expect(response?.status()).toBe(404);
		await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
		await expect(page.getByText('PAGE NOT FOUND')).toBeVisible();
		await expect(page.getByRole('banner')).toHaveCount(1);
		await expect(
			page.getByRole('heading', { name: '찾으시는 페이지가 보이지 않나요?' })
		).toBeVisible();
		const character = page.locator('img[src*="character-404"]');
		await expect(character).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		const noteStyle = await page.getByRole('complementary').evaluate((element) => {
			const probe = document.createElement('span');
			probe.style.color = 'var(--color-coral-soft)';
			element.append(probe);
			const expectedBorder = getComputedStyle(probe).color;
			const style = getComputedStyle(element);
			const result = {
				border: style.borderTopColor,
				background: style.backgroundColor,
				expectedBorder
			};
			probe.remove();
			return result;
		});
		expect(noteStyle.border).toBe(noteStyle.expectedBorder);
		expect(noteStyle.background).not.toBe('rgba(0, 0, 0, 0)');
		await expect
			.poll(() =>
				character.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)
			)
			.toBe(true);
		const bounds = await character.boundingBox();
		if (!bounds) throw new Error('404 캐릭터가 표시되지 않았습니다.');
		expect(bounds.width / bounds.height).toBeCloseTo(1, 2);
		expect(bounds.width).toBeLessThanOrEqual(421);
		if (width >= 768) {
			const card = await page
				.getByRole('region', { name: '페이지를 찾을 수 없어요' })
				.boundingBox();
			const note = await page.getByRole('complementary').boundingBox();
			if (!card || !note) throw new Error('404 카드와 안내 영역이 표시되지 않았습니다.');
			expect(card.width).toBeLessThanOrEqual(1300);
			expect(note.width).toBeLessThanOrEqual(560);
			if (width >= 1280) expect(bounds.x).toBeGreaterThanOrEqual(note.x + note.width);
			else expect(bounds.y).toBeGreaterThanOrEqual(note.y + note.height);
			expect(bounds.x + bounds.width).toBeLessThanOrEqual(card.x + card.width);
			expect(bounds.y + bounds.height).toBeLessThanOrEqual(card.y + card.height);
			// 13:7·한 화면 맞춤은 넓은 데스크톱 기준 화면에서 보존한다.
			// 좁거나 낮은 화면의 가독성·정상 스크롤은 error-responsive 테스트가 검증한다.
			if (width === 1920) {
				expect(card.width / card.height).toBeCloseTo(13 / 7, 2);
				expect(
					await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight)
				).toBe(true);
				const statusSize = await page
					.getByText('404', { exact: true })
					.evaluate((element) => parseFloat(getComputedStyle(element).fontSize));
				const titleSize = await page
					.locator('#error-title')
					.evaluate((element) => parseFloat(getComputedStyle(element).fontSize));
				expect(statusSize).toBeGreaterThan(titleSize * 2);
				expect(statusSize / card.width).toBeCloseTo(130 / 1300, 3);
			}
		}
		expect(
			await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
		).toBe(true);
		await page.screenshot({ path: testInfo.outputPath('404.png'), fullPage: true });
	});
}

test('홈 버튼은 /home으로 연결하고 비로그인 사용자는 기존 인증 흐름을 따른다', async ({ page }) => {
	await page.route('**/api/v1/auth/session', (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요합니다.' } }
		})
	);
	await page.goto(missingPath);
	const home = page.getByRole('link', { name: '홈으로 가기', exact: true });
	await expect(home).toHaveAttribute('href', '/home');
	await home.click();
	await expect(page).toHaveURL(/\/login(?:\?|$)/);
});

test('로그인 사용자는 홈 버튼으로 실제 Home에 진입한다', async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto(missingPath);
	await page.getByRole('link', { name: '홈으로 가기', exact: true }).click();
	await expect(page).toHaveURL('/home');
	await expect(page.getByRole('heading', { name: '오늘의 점수', exact: true })).toBeVisible();
});

test('앱 내부 링크에서 미등록 URL로 이동하면 이전 앱 페이지로 돌아간다', async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto('/');
	await expect(page).toHaveURL('/home');
	await expect(page.getByRole('heading', { name: '오늘의 점수', exact: true })).toBeVisible();
	await page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '건강 데이터', exact: true })
		.click();
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
	await page.evaluate((href) => {
		const link = document.createElement('a');
		link.href = href;
		link.textContent = '미등록 페이지 열기';
		document.body.append(link);
	}, missingPath);
	await page.getByRole('link', { name: '미등록 페이지 열기' }).click();
	await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
	await page.getByRole('button', { name: '이전 페이지' }).click();
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
});

test('클라이언트 날짜 검증의 404도 Header 중복 없이 표시하고 이전 앱으로 돌아간다', async ({
	page
}) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
	await page.evaluate(() => {
		const link = document.createElement('a');
		link.href = '/health/day/not-a-date';
		link.textContent = '잘못된 날짜 열기';
		document.body.append(link);
	});
	await page.getByRole('link', { name: '잘못된 날짜 열기' }).click();
	await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
	await expect(page.getByRole('banner')).toHaveCount(1);
	await page.getByRole('button', { name: '이전 페이지' }).click();
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
});

test('실제 새 탭에서 미등록 URL로 직접 진입하면 이전 페이지 버튼으로 Home에 진입한다', async ({
	browser,
	baseURL
}) => {
	const context = await browser.newContext({ baseURL });
	try {
		const tab = await context.newPage();
		await mockSignedIn(tab);
		await mockHomeApi(tab);
		const response = await tab.goto(missingPath);
		expect(response?.status()).toBe(404);
		await expect(tab.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
		await tab.getByRole('button', { name: '이전 페이지' }).click();
		await expect(tab).toHaveURL('/home');
		await expect(tab.getByRole('heading', { name: '오늘의 점수', exact: true })).toBeVisible();
	} finally {
		await context.close();
	}
});

test('앱에서 새 탭으로 404를 열어도 이전 페이지 버튼은 빈 탭 대신 Home으로 이동한다', async ({
	page
}) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto('/home');
	await expect(page.getByRole('heading', { name: '오늘의 점수', exact: true })).toBeVisible();
	await page.evaluate((href) => {
		const link = document.createElement('a');
		link.href = href;
		link.target = '_blank';
		link.textContent = '새 탭에서 미등록 페이지 열기';
		document.body.append(link);
	}, missingPath);
	const popupPromise = page.waitForEvent('popup');
	await page.getByRole('link', { name: '새 탭에서 미등록 페이지 열기' }).click();
	const tab = await popupPromise;
	await mockSignedIn(tab);
	await mockHomeApi(tab);
	await expect(tab.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
	await tab.getByRole('button', { name: '이전 페이지' }).click();
	await expect(tab).toHaveURL('/home');
	await expect(tab.getByRole('heading', { name: '오늘의 점수', exact: true })).toBeVisible();
	await expect(page).toHaveURL('/home');
	await expect(page.getByRole('heading', { name: '오늘의 점수', exact: true })).toBeVisible();
});
