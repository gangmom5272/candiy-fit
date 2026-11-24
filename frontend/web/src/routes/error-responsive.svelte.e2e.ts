import { expect, test, chromium, type Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { mockSignedIn } from '../lib/testing/e2e-auth';
import { mockHomeApi } from '../lib/testing/home-api-fixture';

const missingPath = '/error-responsive-missing-page';

async function countHistoryBack(page: Page) {
	await page.addInitScript(() => {
		const back = history.back.bind(history);
		history.back = () => {
			// 없는 경로는 문서 탐색이므로 동일 탭의 문서 사이에서도 호출 수를 유지한다.
			sessionStorage.setItem(
				'error-test-back-calls',
				String(Number(sessionStorage.getItem('error-test-back-calls') ?? 0) + 1)
			);
			back();
		};
	});
}

async function historyBackCalls(page: Page) {
	return page.evaluate(() => Number(sessionStorage.getItem('error-test-back-calls') ?? 0));
}

async function measureError(page: Page) {
	return page.evaluate(() => {
		const fontSize = (selector: string) => {
			const element = document.querySelector(selector);
			if (!element) throw new Error(`${selector} 요소가 없습니다.`);
			return parseFloat(getComputedStyle(element).fontSize);
		};
		return {
			width: innerWidth,
			height: innerHeight,
			dpr: devicePixelRatio,
			cssZoom: getComputedStyle(document.documentElement).zoom,
			visualScale: visualViewport?.scale,
			documentWidth: document.documentElement.scrollWidth,
			documentHeight: document.documentElement.scrollHeight,
			title: fontSize('#error-title'),
			body: fontSize('.error-description'),
			note: fontSize('.error-note p'),
			button: fontSize('.error-action-primary')
		};
	});
}

async function expectFocusVisible(page: Page, name: string) {
	const control = page.getByRole(name === '홈으로 가기' ? 'link' : 'button', {
		name,
		exact: true
	});
	await expect(control).toBeFocused();
	const visibility = await control.evaluate((element) => {
		const bounds = element.getBoundingClientRect();
		const header = document.querySelector('header')?.getBoundingClientRect();
		const navigation = document.querySelector('header nav');
		const navigationTop =
			navigation && getComputedStyle(navigation).position === 'fixed'
				? navigation.getBoundingClientRect().top
				: innerHeight;
		const style = getComputedStyle(element);
		const outline = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset);
		return {
			top: bounds.top - outline,
			bottom: bounds.bottom + outline,
			headerBottom: header?.bottom ?? 0,
			viewportBottom: navigationTop,
			outlineStyle: style.outlineStyle,
			centerIsControl: element.contains(
				document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2)
			)
		};
	});
	expect(visibility.top).toBeGreaterThanOrEqual(visibility.headerBottom);
	expect(visibility.bottom).toBeLessThanOrEqual(visibility.viewportBottom);
	expect(visibility.outlineStyle).toBe('solid');
	expect(visibility.centerIsControl).toBe(true);
}

for (const [width, height] of [
	[320, 800],
	[360, 800],
	[390, 844],
	[639, 900],
	[640, 900],
	[767, 1024],
	[768, 1024],
	[769, 1024],
	[1023, 768],
	[1024, 768],
	[1366, 768],
	[1920, 1080],
	[390, 360],
	[740, 240],
	[768, 240],
	[1366, 240]
]) {
	test(`${width}×${height}: 404 글자·버튼 크기와 콘텐츠 접근성을 유지한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		await page.goto(missingPath, { waitUntil: 'networkidle' });
		await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		const measured = await measureError(page);
		// 본문 16px·보조 14px는 기존 모바일 값과 디자인 시스템의 body/body-sm 기준이다.
		expect(measured.body).toBeGreaterThanOrEqual(16);
		expect(measured.note).toBeGreaterThanOrEqual(14);
		expect(measured.title).toBeGreaterThanOrEqual(30);
		expect(measured.button).toBeGreaterThanOrEqual(18);
		expect(measured.documentWidth).toBeLessThanOrEqual(measured.width);
		if (height <= 360) expect(measured.documentHeight).toBeGreaterThan(measured.height);

		const geometry = await page
			.getByRole('region', { name: '페이지를 찾을 수 없어요' })
			.evaluate((card) => {
				const bounds = card.getBoundingClientRect();
				return [
					...card.querySelectorAll<HTMLElement>('.error-action, .error-note, .error-character')
				].map((element) => ({
					kind: element.className,
					bounds: element.getBoundingClientRect().toJSON(),
					card: bounds.toJSON()
				}));
			});
		for (const item of geometry) {
			expect(item.bounds.left).toBeGreaterThanOrEqual(item.card.left);
			expect(item.bounds.right).toBeLessThanOrEqual(item.card.right);
			expect(item.bounds.top).toBeGreaterThanOrEqual(item.card.top);
			expect(item.bounds.bottom).toBeLessThanOrEqual(item.card.bottom);
			// 기존 60px 버튼을 유지한다. WCAG 2.5.8의 24px와 향상 기준 44px보다 크다.
			if (item.kind.includes('error-action')) expect(item.bounds.height).toBeGreaterThanOrEqual(60);
			if (item.kind.includes('error-character'))
				expect(item.bounds.width / item.bounds.height).toBeCloseTo(1, 2);
		}
		await testInfo.attach('measurements', {
			body: JSON.stringify({ measured, geometry }, null, 2),
			contentType: 'application/json'
		});
		await page.screenshot({ path: testInfo.outputPath('404-responsive.png'), fullPage: true });
	});
}

for (const [width, height] of [
	[740, 240],
	[390, 360],
	[768, 240]
]) {
	test(`${width}×${height}: Tab·Shift+Tab 포커스와 테두리가 헤더에 가려지지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height });
		await page.goto(missingPath, { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		// 로고·주 메뉴 세 개·사용자 메뉴 다음의 두 복구 동작까지 실제 Tab으로 이동한다.
		for (let index = 0; index < 6; index++) await page.keyboard.press('Tab');
		await expectFocusVisible(page, '홈으로 가기');
		await page.keyboard.press('Tab');
		await expectFocusVisible(page, '이전 페이지');
		await page.keyboard.press('Shift+Tab');
		await expectFocusVisible(page, '홈으로 가기');
	});
}

test('이미 보이는 복구 버튼 사이의 포커스 이동은 페이지를 스크롤하지 않는다', async ({ page }) => {
	await page.setViewportSize({ width: 1920, height: 1080 });
	await page.goto(missingPath, { waitUntil: 'networkidle' });
	await page.evaluate(() => document.fonts.ready);
	const before = await page.evaluate(() => scrollY);
	await page.getByRole('link', { name: '홈으로 가기', exact: true }).focus();
	await expectFocusVisible(page, '홈으로 가기');
	await page.keyboard.press('Tab');
	await expectFocusVisible(page, '이전 페이지');
	await page.keyboard.press('Shift+Tab');
	await expectFocusVisible(page, '홈으로 가기');
	expect(await page.evaluate(() => scrollY)).toBe(before);
});

for (const [width, height] of [
	[1366, 768],
	[1920, 1080],
	[1366, 480]
]) {
	test(`${width}×${height}: 실제 Chromium 200% 확대가 글자와 버튼을 축소하지 않는다`, async ({
		baseURL
	}, testInfo) => {
		const extensionPath = testInfo.outputPath('zoom-extension');
		mkdirSync(extensionPath, { recursive: true });
		writeFileSync(
			`${extensionPath}/manifest.json`,
			JSON.stringify({
				manifest_version: 3,
				name: '404 page zoom regression',
				version: '1.0',
				permissions: ['tabs'],
				background: { service_worker: 'worker.js' }
			})
		);
		writeFileSync(
			`${extensionPath}/worker.js`,
			'chrome.runtime.onInstalled.addListener(() => {});'
		);
		const context = await chromium.launchPersistentContext(testInfo.outputPath('browser-profile'), {
			channel: 'chromium',
			headless: true,
			viewport: { width, height },
			args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]
		});
		try {
			const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'));
			const page = await context.newPage();
			await mockSignedIn(page);
			await mockHomeApi(page);
			await page.goto(new URL(missingPath, baseURL).href, { waitUntil: 'networkidle' });
			await expect(page.getByRole('heading', { name: '페이지를 찾을 수 없어요' })).toBeVisible();
			await page.evaluate(() => document.fonts.ready);
			const before = await measureError(page);
			const zoom = await worker.evaluate(async (url) => {
				// Chrome 확장 컨텍스트의 실제 페이지 확대 API. CSS zoom·핀치 확대를 사용하지 않는다.
				const { chrome } = globalThis as typeof globalThis & {
					chrome: {
						tabs: {
							query(query: Record<string, never>): Promise<{ id?: number; url?: string }[]>;
							setZoom(id: number, factor: number): Promise<void>;
							getZoom(id: number): Promise<number>;
						};
					};
				};
				const tab = (await chrome.tabs.query({})).find((tab) => tab.url === url);
				if (tab?.id === undefined) throw new Error('확대할 Chromium 탭이 없습니다.');
				await chrome.tabs.setZoom(tab.id, 2);
				return chrome.tabs.getZoom(tab.id);
			}, page.url());
			expect(zoom).toBe(2);
			await expect.poll(async () => (await measureError(page)).dpr).toBe(before.dpr * 2);
			const after = await measureError(page);
			expect(after.cssZoom).toBe('1');
			expect(after.visualScale).toBe(1);
			expect(after.width).toBe(Math.round(width / 2));
			expect(after.documentWidth).toBeLessThanOrEqual(after.width);
			for (const key of ['title', 'body', 'note', 'button'] as const)
				expect(after[key] * after.dpr).toBeGreaterThanOrEqual(before[key] * before.dpr * 2);
			for (let index = 0; index < 6; index++) await page.keyboard.press('Tab');
			await expectFocusVisible(page, '홈으로 가기');
			await page.keyboard.press('Tab');
			await expectFocusVisible(page, '이전 페이지');
			await page.keyboard.press('Shift+Tab');
			await expectFocusVisible(page, '홈으로 가기');
			await testInfo.attach('real-page-zoom', {
				body: JSON.stringify({ before, zoom, after }, null, 2),
				contentType: 'application/json'
			});
			await page.screenshot({ path: testInfo.outputPath('404-200-percent.png'), fullPage: true });
			await page.evaluate(() => scrollTo(0, 0));
			const header = await page.getByRole('banner').boundingBox();
			const navigation = await page.getByRole('navigation', { name: '주 메뉴' }).boundingBox();
			for (const path of ['/home', '/health']) {
				await page.goto(new URL(path, baseURL).href, { waitUntil: 'networkidle' });
				await expect(page.getByRole('banner')).toHaveCount(1);
				expect(await page.getByRole('banner').boundingBox()).toEqual(header);
				expect(await page.getByRole('navigation', { name: '주 메뉴' }).boundingBox()).toEqual(
					navigation
				);
				await page.screenshot({ path: testInfo.outputPath(`${path.slice(1)}-200-percent.png`) });
			}
			await page.goto(new URL(missingPath, baseURL).href, { waitUntil: 'networkidle' });
			await page.route('**/api/v1/auth/logout', (route) => route.fulfill({ status: 204 }));
			await page.getByRole('button', { name: '사용자 메뉴' }).focus();
			await page.keyboard.press('Enter');
			const logout = page.getByRole('menuitem', { name: '로그아웃' });
			await expect(logout).toBeEnabled();
			const menuBounds = await page.getByRole('menu').boundingBox();
			if (!menuBounds) throw new Error('확대된 사용자 메뉴가 없습니다.');
			expect(menuBounds.x).toBeGreaterThanOrEqual(0);
			expect(menuBounds.y + menuBounds.height).toBeLessThanOrEqual(after.height);
			await page.keyboard.press('End');
			await expect(logout).toBeFocused();
			await page.keyboard.press('Enter');
			await expect(page).toHaveURL(new URL('/login', baseURL).href);
		} finally {
			await context.close();
		}
	});
}

test('히스토리가 한 개인 최초 진입도 이전 페이지 대신 홈으로 이동한다', async ({
	page,
	baseURL
}) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.evaluate((url) => location.replace(url), new URL(missingPath, baseURL).href);
	await expect(page.getByRole('button', { name: '이전 페이지' })).toBeVisible();
	expect(await page.evaluate(() => history.length)).toBe(1);
	await page.getByRole('button', { name: '이전 페이지' }).click();
	await expect(page).toHaveURL('/home');
});

test('외부 사이트에서 진입한 오류의 이전 페이지는 홈으로 이동한다', async ({ page, baseURL }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.route('http://external.test/**', (route) =>
		route.fulfill({
			contentType: 'text/html; charset=utf-8',
			body: `<a href="${new URL(missingPath, baseURL).href}">오류 페이지 열기</a>`
		})
	);
	await page.goto('http://external.test/start');
	await page.getByRole('link', { name: '오류 페이지 열기' }).click();
	await page.getByRole('button', { name: '이전 페이지' }).click();
	await expect(page).toHaveURL('/home');
});

test('Navigation API가 없어도 오류 페이지에서 홈으로 안전하게 이동한다', async ({ page }) => {
	await page.addInitScript(() => Object.defineProperty(window, 'navigation', { value: undefined }));
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto(missingPath);
	await page.getByRole('button', { name: '이전 페이지' }).click();
	await expect(page).toHaveURL('/home');
});

test('이전 페이지 버튼의 실제 빠른 연속 클릭은 이전 앱 화면으로 돌아간다', async ({ page }) => {
	await countHistoryBack(page);
	await page.setViewportSize({ width: 1366, height: 768 });
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto('/home');
	await page
		.getByRole('navigation')
		.getByRole('link', { name: '건강 데이터', exact: true })
		.click();
	await expect(page).toHaveURL('/health');
	await page.evaluate((href) => {
		const link = document.createElement('a');
		link.href = href;
		document.body.append(link);
		link.click();
	}, missingPath);
	const back = page.getByRole('button', { name: '이전 페이지', exact: true });
	await back.scrollIntoViewIfNeeded();
	const bounds = await back.boundingBox();
	if (!bounds) throw new Error('이전 페이지 버튼이 없습니다.');
	await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, {
		clickCount: 3,
		delay: 5
	});
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
	expect(await historyBackCalls(page)).toBe(1);
});

test('같은 이벤트의 세 클릭은 한 단계만 돌아가고 브라우저 재방문 뒤 다시 사용할 수 있다', async ({
	page
}) => {
	await countHistoryBack(page);
	await mockSignedIn(page);
	await mockHomeApi(page);
	await page.goto('/home');
	await page
		.getByRole('navigation')
		.getByRole('link', { name: '건강 데이터', exact: true })
		.click();
	await expect(page).toHaveURL('/health');
	await page.evaluate((href) => {
		const link = document.createElement('a');
		link.href = href;
		document.body.append(link);
		link.click();
	}, missingPath);
	const error = page.getByRole('heading', { name: '페이지를 찾을 수 없어요', exact: true });
	const back = page.getByRole('button', { name: '이전 페이지', exact: true });
	await expect(error).toBeVisible();
	await back.evaluate((button) => {
		if (!(button instanceof HTMLButtonElement)) throw new Error('이전 페이지 버튼이 없습니다.');
		// 프레임워크가 다음 popstate를 처리하기 전에 발생하는 세 입력을 재현한다.
		for (let index = 0; index < 3; index++) button.click();
	});
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
	expect(await historyBackCalls(page)).toBe(1);

	await page.goForward();
	await expect(error).toBeVisible();
	await page.goBack();
	await expect(page).toHaveURL('/health');
	await page.goForward();
	await expect(error).toBeVisible();
	await back.click();
	await expect(page).toHaveURL('/health');
	await expect(page.getByRole('heading', { name: '건강 데이터', exact: true })).toBeVisible();
	expect(await historyBackCalls(page)).toBe(2);
});
