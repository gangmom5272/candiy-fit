import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../testing/e2e-auth';
import { mockHomeApi } from '../../testing/home-api-fixture';

test.beforeEach(({ page }) => mockSignedIn(page));

for (const width of [360, 390, 767, 768, 1024, 1366]) {
	test(`${width}px 공통 내비게이션은 768px 경계와 이동·여백·오프라인·404를 보존한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: 800 });
		await mockHomeApi(page);
		await page.goto('/home');
		const header = page.getByRole('banner');
		const navigation = page.getByRole('navigation', { name: '주 메뉴' });
		await expect(page.getByRole('link', { name: 'CANDiY-FiT 홈', exact: true })).toBeVisible();
		await expect(page.getByRole('button', { name: '사용자 메뉴' })).toBeVisible();
		await expect(navigation).toHaveCount(1);
		await expect(navigation.getByRole('link')).toHaveCount(3);
		await expect(navigation).toHaveCSS('position', width < 768 ? 'fixed' : 'static');
		await page.evaluate(() => document.fonts.ready);
		const bounds = await navigation.boundingBox();
		const headerBounds = await header.boundingBox();
		if (!bounds || !headerBounds) throw new Error('공통 내비게이션 위치를 확인할 수 없습니다.');
		if (width < 768) {
			expect(bounds.y).toBeGreaterThanOrEqual(headerBounds.y + headerBounds.height);
			expect(bounds.y + bounds.height).toBeCloseTo(800, 0);
			const widths = await navigation
				.getByRole('link')
				.evaluateAll((links) => links.map((link) => link.getBoundingClientRect().width));
			expect(Math.max(...widths) - Math.min(...widths)).toBeLessThanOrEqual(1);
			await expect(page.locator('.app-layout')).toHaveCSS('padding-bottom', `${bounds.height}px`);
		} else {
			expect(bounds.y).toBeGreaterThanOrEqual(headerBounds.y);
			expect(bounds.y + bounds.height).toBeLessThanOrEqual(headerBounds.y + headerBounds.height);
			await expect(page.locator('.app-layout')).toHaveCSS('padding-bottom', '0px');
		}
		await page.screenshot({ path: testInfo.outputPath(`header-${width}.png`), fullPage: true });
		for (const [name, path] of [
			['건강 데이터', '/health'],
			['AI 코치', '/coaching'],
			['홈', '/home']
		]) {
			const link = navigation.getByRole('link', { name, exact: true });
			await link.focus();
			await expect(link).toBeFocused();
			await page.keyboard.press('Enter');
			await expect(page).toHaveURL(path);
			await expect(link).toHaveAttribute('aria-current', 'page');
			await expect(navigation.locator('[aria-current]')).toHaveCount(1);
			expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
				width
			);
		}
		await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
		await expect.poll(async () => (await header.boundingBox())?.y).toBeCloseTo(0, 0);
		if (width < 768) {
			await expect.poll(async () => (await navigation.boundingBox())?.y).toBeCloseTo(bounds.y, 0);
			const main = await page.getByRole('main').boundingBox();
			if (!main) throw new Error('페이지 콘텐츠 위치를 확인할 수 없습니다.');
			expect(main.y + main.height).toBeLessThanOrEqual(bounds.y + 1);
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			width
		);
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		await expect(page.getByRole('menu')).toBeVisible();
		await page.getByRole('menuitem', { name: '마이페이지' }).click();
		await expect(page).toHaveURL('/my');
		await page.getByRole('link', { name: 'CANDiY-FiT 홈', exact: true }).click();
		await expect(page).toHaveURL('/home');
		await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
		await page.context().setOffline(true);
		const offline = page.getByRole('status', { name: '네트워크 연결 상태' });
		await expect(offline).toBeVisible();
		const offlineBounds = await offline.boundingBox();
		expect(offlineBounds?.y).toBeCloseTo(headerBounds.y + headerBounds.height, 0);
		await expect(navigation).toBeVisible();
		await page.context().setOffline(false);
		await expect(offline).toHaveCount(0);
		await page.goto('/not-a-real-page-276');
		await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
		await expect(page.getByRole('banner')).toHaveCount(1);
		await expect(navigation).toHaveCSS('position', width < 768 ? 'fixed' : 'static');
	});
}

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 헤더가 고정되고 홈 활성 여부에 따라 홈 글자 위치가 바뀌지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await page.goto('/home');
		const home = page
			.getByRole('navigation', { name: '주 메뉴' })
			.getByRole('link', { name: '홈', exact: true });
		const measure = () =>
			home.getByText('홈', { exact: true }).evaluate((el) => el.getBoundingClientRect().x);
		await expect(home).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		const active = await measure();
		await page
			.getByRole('navigation', { name: '주 메뉴' })
			.getByRole('link', { name: '건강 데이터', exact: true })
			.click();
		await expect(page).toHaveURL('/health');
		await expect(home).not.toHaveAttribute('aria-current');
		await page.evaluate(() => document.fonts.ready);
		const inactive = await measure();
		// 글꼴 layout의 1px 이내 반올림을 허용하고 홈 글자의 수평 이동을 검출한다.
		expect(Math.abs(inactive - active)).toBeLessThanOrEqual(1);
		await page.goto('/health/activity');
		await page.evaluate(() => window.scrollTo(0, 500));
		await expect
			.poll(async () => (await page.getByRole('banner').boundingBox())?.y)
			.toBeCloseTo(0, 1);
	});
}

for (const [path, name, current] of [
	['/home', '홈', 'page'],
	['/health', '건강 데이터', 'page'],
	['/health/activity', '건강 데이터', 'true'],
	['/coaching', 'AI 코치', 'page'],
	['/coaching/c0000000-0000-4000-8000-000000000001', 'AI 코치', 'true']
]) {
	test(`${path}에서 Header aria-current=${current}`, async ({ page }) => {
		const response = await page.goto(path);
		expect(response?.status()).toBe(200);
		const navigation = page.getByRole('navigation', { name: '주 메뉴' });
		await expect(navigation.locator('[aria-current]')).toHaveCount(1);
		await expect(navigation.getByRole('link', { name, exact: true })).toHaveAttribute(
			'aria-current',
			current
		);
		if (current === 'true')
			await expect(navigation.locator('[aria-current="page"]')).toHaveCount(0);
	});
}

test('Health에서 비활성 홈 아이콘은 다른 비활성 메뉴와 같은 원형 배경을 표시한다', async ({
	page
}) => {
	await page.goto('/health');
	const navigation = page.getByRole('navigation', { name: '주 메뉴' });
	const home = navigation.getByRole('link', { name: '홈', exact: true });
	await expect(home).not.toHaveAttribute('aria-current');
	const homeIcon = home.locator('span').first();
	const coachIcon = navigation.getByRole('link', { name: 'AI 코치', exact: true }).locator('span');
	for (const icon of [homeIcon, coachIcon]) {
		await expect(icon).toHaveCSS('background-color', 'rgb(255, 244, 243)');
		const radius = await icon.evaluate((element) =>
			Number.parseFloat(getComputedStyle(element).borderTopLeftRadius)
		);
		expect(radius).toBeGreaterThanOrEqual(20);
		await expect(icon).toHaveCSS('width', '40px');
		await expect(icon).toHaveCSS('height', '40px');
	}
	await expect(homeIcon).toHaveCSS(
		'border-radius',
		await coachIcon.evaluate((element) => getComputedStyle(element).borderRadius)
	);
});

test('앱 layout은 Header를 한 번 표시하고 확정 route 링크와 활성 홈 메뉴를 제공한다', async ({
	page
}) => {
	await page.goto('/home');
	await expect(page.getByRole('banner')).toHaveCount(1);
	const navigation = page.getByRole('navigation', { name: '주 메뉴' });
	for (const [name, href] of [
		['홈', '/home'],
		['건강 데이터', '/health'],
		['AI 코치', '/coaching']
	]) {
		const link = navigation.getByRole('link', { name, exact: true });
		await expect(link).toHaveAttribute('href', href);
		await expect(link).toHaveCSS('cursor', 'pointer');
	}
	await expect(navigation.locator('[aria-current="page"]')).toHaveText('홈');
	await expect(navigation.getByRole('link', { name: '홈', exact: true })).toHaveCSS(
		'background-color',
		'rgb(255, 244, 243)'
	);
});

test('사용자 메뉴는 재클릭, 외부 클릭, ESC로 닫힌다', async ({ page }) => {
	await page.goto('/home');
	const trigger = page.getByRole('button', { name: '사용자 메뉴' });
	const menu = page.getByRole('menu', { name: '사용자 메뉴' });

	await expect(trigger).toHaveAttribute('aria-expanded', 'false');
	await trigger.click();
	await expect(menu).toBeVisible();
	await expect(trigger).toHaveAttribute('aria-expanded', 'true');
	await expect(menu.getByRole('menuitem')).toHaveText(['마이페이지', '로그아웃']);
	await trigger.click();
	await expect(menu).toHaveCount(0);

	await trigger.click();
	await page.getByRole('heading', { level: 1 }).click();
	await expect(menu).toHaveCount(0);
	await expect(trigger).toHaveAttribute('aria-expanded', 'false');

	await trigger.click();
	await page.keyboard.press('Escape');
	await expect(menu).toHaveCount(0);
	await expect(trigger).toBeFocused();
});

test('호버로 열리고 두 항목은 같은 색상과 크기로 호버 배경을 표시한다', async ({ page }) => {
	await page.goto('/home');
	const trigger = page.getByRole('button', { name: '사용자 메뉴' });
	const menu = page.getByRole('menu');
	const mypage = page.getByRole('menuitem', { name: '마이페이지' });
	const logout = page.getByRole('menuitem', { name: '로그아웃' });

	await trigger.hover();
	await expect(menu).toBeVisible();
	for (const item of [mypage, logout]) {
		// 흰 메뉴 위 작은 글자라 진한 레드(대비 4.95:1, S15P31A403-315)
		await expect(item).toHaveCSS('color', 'rgb(204, 56, 62)');
		await expect(item).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
	}
	const triggerBounds = await trigger.boundingBox();
	if (!triggerBounds) throw new Error('사용자 버튼 위치를 확인할 수 없습니다.');
	await page.mouse.move(
		triggerBounds.x + triggerBounds.width / 2,
		triggerBounds.y + triggerBounds.height + 10
	);
	await expect(menu).toBeVisible();
	await mypage.hover();
	await expect(mypage).toHaveCSS('background-color', 'rgb(255, 244, 243)');
	await logout.hover();
	await expect(logout).toHaveCSS('background-color', 'rgb(255, 244, 243)');
	await expect(mypage).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
	const mypageBounds = await mypage.boundingBox();
	const logoutBounds = await logout.boundingBox();
	expect(mypageBounds?.height).toBe(logoutBounds?.height);
	expect(mypageBounds?.width).toBe(logoutBounds?.width);
	await page.getByRole('heading', { level: 1 }).hover();
	await expect(menu).toHaveCount(0);
});

test('키보드로 사용자 메뉴를 열고 항목 사이를 이동할 수 있다', async ({ page }) => {
	await page.goto('/home');
	const trigger = page.getByRole('button', { name: '사용자 메뉴' });
	const mypage = page.getByRole('menuitem', { name: '마이페이지' });
	const logout = page.getByRole('menuitem', { name: '로그아웃' });

	await trigger.focus();
	await page.keyboard.press('Enter');
	await expect(mypage).toBeFocused();
	await expect(mypage).not.toHaveAttribute('aria-disabled');
	await expect(logout).not.toHaveAttribute('aria-disabled');
	await page.keyboard.press('ArrowDown');
	await expect(logout).toBeFocused();
	await page.keyboard.press('ArrowDown');
	await expect(mypage).toBeFocused();
	await page.keyboard.press('End');
	await expect(logout).toBeFocused();
	await page.keyboard.press('Home');
	await expect(mypage).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(page.getByRole('menu')).toHaveCount(0);
});

for (const viewport of [
	{ width: 2560, height: 1440 },
	{ width: 1920, height: 1080 },
	{ width: 1366, height: 768 },
	{ width: 1024, height: 768 }
]) {
	test(`${viewport.width}×${viewport.height}에서 메뉴가 헤더 밖으로 표시되고 잘리지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/home');
		await page.getByRole('button', { name: '사용자 메뉴' }).click();
		const menu = page.getByRole('menu');
		await expect(menu).toBeVisible();
		const bounds = await menu.boundingBox();
		expect(bounds).not.toBeNull();
		if (!bounds) throw new Error('사용자 메뉴 위치를 확인할 수 없습니다.');
		expect(bounds.x).toBeGreaterThanOrEqual(0);
		expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
		expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
		const headerBottom = await page
			.locator('header')
			.evaluate((header) => header.getBoundingClientRect().bottom);
		expect(bounds.y).toBeGreaterThanOrEqual(headerBottom);
		const hitInsideMenu = await menu.evaluate((element) => {
			const rect = element.getBoundingClientRect();
			const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.bottom - 8);
			return hit !== null && element.contains(hit);
		});
		expect(hitInsideMenu).toBe(true);
	});
}

for (const width of [360, 1366]) {
	test(`${width}px 주 메뉴는 기본·선택·호버·키보드 포커스를 서로 다르게 보여 준다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await mockHomeApi(page);
		await page.goto('/home');
		const navigation = page.getByRole('navigation', { name: '주 메뉴' });
		const home = navigation.getByRole('link', { name: '홈', exact: true });
		const health = navigation.getByRole('link', { name: '건강 데이터', exact: true });
		const look = (link: typeof home) =>
			link.evaluate((el) => {
				const style = getComputedStyle(el);
				return {
					background: style.backgroundColor,
					color: style.color,
					weight: style.fontWeight,
					outline: `${style.outlineStyle} ${style.outlineColor}`
				};
			});
		await expect(home).toHaveAttribute('aria-current', 'page');
		const selected = await look(home);
		const idle = await look(health);
		// 선택: 연분홍 배경 + 진한 굵은 글자 / 기본: 투명 배경 + 회색 글자
		expect(selected.background).toBe('rgb(255, 244, 243)');
		expect(selected.color).toBe('rgb(44, 46, 53)');
		expect(selected.weight).toBe('700');
		expect(idle.background).toBe('rgba(0, 0, 0, 0)');
		expect(idle.color).toBe('rgb(109, 114, 128)');
		expect(Number(idle.weight)).toBeLessThan(700);
		// 호버: 옅은 회색 배경과 진한 글자. 선택 칸은 호버해도 그대로다
		await health.hover();
		await expect(health).toHaveCSS('background-color', 'rgb(243, 246, 251)');
		await expect(health).toHaveCSS('color', 'rgb(44, 46, 53)');
		await home.hover();
		await expect(home).toHaveCSS('background-color', 'rgb(255, 244, 243)');
		// 키보드 포커스: 브라우저 기본 검은 테두리 대신 브랜드색 테두리
		await page.mouse.move(0, 400);
		await home.focus();
		await page.keyboard.press('Tab');
		await expect(health).toBeFocused();
		await expect(health).toHaveCSS('outline-style', 'solid');
		await expect(health).toHaveCSS('outline-color', 'rgb(248, 90, 90)');
		// 마우스 클릭으로는 포커스 테두리가 남지 않는다
		await health.click();
		await expect(page).toHaveURL('/health');
		await expect(health).toHaveCSS('outline-style', 'none');
	});
}
