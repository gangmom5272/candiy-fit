import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { mockHomeApi } from '../lib/testing/home-api-fixture';

async function measureHeader(page: Page) {
	return page.getByRole('banner').evaluate((header) => {
		const measure = (element: Element | null, includeBackground = true) => {
			if (!element) throw new Error('헤더 요소가 없습니다.');
			const style = getComputedStyle(element);
			const bounds = element.getBoundingClientRect();
			return {
				x: bounds.x,
				y: bounds.y,
				width: bounds.width,
				height: bounds.height,
				position: style.position,
				fontSize: style.fontSize,
				borderRadius: style.borderRadius,
				background: includeBackground ? style.backgroundColor : undefined
			};
		};
		return {
			header: measure(header),
			logo: measure(header.querySelector('a > img')),
			account: measure(header.querySelector('button[aria-haspopup="menu"]')),
			navigation: measure(header.querySelector('nav')),
			// 활성 메뉴의 배경만 다르고, 위치·크기·글자 크기는 같아야 한다.
			links: [...header.querySelectorAll('nav > a')].map((link) => measure(link, false))
		};
	});
}

for (const [width, height] of [
	[320, 800],
	[390, 844],
	[640, 900],
	[767, 1024],
	[768, 1024],
	[1024, 768],
	[1366, 768],
	[1920, 1080],
	[390, 360],
	[740, 240]
]) {
	test(`${width}×${height}: 404 헤더는 Home·Health와 같은 배치를 사용한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		await mockSignedIn(page);
		await mockHomeApi(page);
		let home: Awaited<ReturnType<typeof measureHeader>> | undefined;
		for (const path of ['/home', '/health', '/missing-header-345']) {
			await page.goto(path, { waitUntil: 'networkidle' });
			await page.evaluate(() => document.fonts.ready);
			await expect(page.getByRole('banner')).toHaveCount(1);
			await expect(page.getByRole('navigation', { name: '주 메뉴' })).toHaveCount(1);
			const measured = await measureHeader(page);
			await page.screenshot({ path: testInfo.outputPath(`${path.slice(1)}.png`) });
			await testInfo.attach(path, {
				body: JSON.stringify(measured),
				contentType: 'application/json'
			});
			if (path === '/home') home = measured;
			else expect(measured).toEqual(home);
			if (path === '/missing-header-345') {
				await expect(page.locator('nav [aria-current]')).toHaveCount(0);
				expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
					width
				);
				await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
				const main = await page.getByRole('main').boundingBox();
				const navigation = await page.getByRole('navigation').boundingBox();
				if (!main || !navigation) throw new Error('404 본문과 내비게이션이 없습니다.');
				if (width < 768) expect(main.y + main.height).toBeLessThanOrEqual(navigation.y);
				await expect.poll(async () => (await page.getByRole('banner').boundingBox())?.y).toBe(0);
			}
		}
	});
}
