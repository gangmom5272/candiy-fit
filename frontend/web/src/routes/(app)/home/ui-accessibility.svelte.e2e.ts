import { expect, test, type Locator } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import { mockDayDetailApi } from '../../../lib/testing/day-detail-api-fixture';
import { dailyMessageFixture } from '../../../lib/testing/daily-message-fixture';

const endpoint = '**/api/v1/health/daily-message';
const unavailable = {
	error: {
		code: 'HEALTH_DATA_UNAVAILABLE',
		message: '조회 실패',
		context: { reason: 'LOOKUP_FAILED', metrics: [] }
	}
};

async function textContrast(button: Locator) {
	return button.evaluate((element) => {
		const style = getComputedStyle(element);
		const luminance = (color: string) => {
			const channels = color
				.match(/[\d.]+/g)
				?.slice(0, 3)
				.map(Number);
			if (!channels || channels.length !== 3) throw new Error('색상을 읽을 수 없습니다.');
			const [r, g, b] = channels.map((channel) => {
				const value = channel / 255;
				return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
			});
			return r * 0.2126 + g * 0.7152 + b * 0.0722;
		};
		const foreground = luminance(style.color);
		const background = luminance(style.backgroundColor);
		return {
			color: style.color,
			background: style.backgroundColor,
			ratio: (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
		};
	});
}

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const width of [390, 767, 768, 1366]) {
	test(`${width}px: Home 운동 아이콘은 하루 상세와 같고 기존 24px 영역을 유지한다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/home');
		const homeIcon = page.getByRole('link', { name: '운동 시간 18분', exact: true }).locator('img');
		await expect(homeIcon).toBeVisible();
		const source = await homeIcon.getAttribute('src');
		await expect(homeIcon).toHaveAttribute('alt', '');
		const size = await homeIcon.boundingBox();
		expect(size?.width).toBe(24);
		expect(size?.height).toBe(24);
		await mockDayDetailApi(page);
		await page.goto('/health/day/2026-10-19');
		const detailIcon = page
			.getByLabel('하루 기록 요약')
			.locator('dt')
			.filter({ hasText: '운동' })
			.locator('img');
		await expect(detailIcon).toBeVisible();
		expect(source).toBe(await detailIcon.getAttribute('src'));
	});

	test(`${width}px: 재시도 버튼은 기본·hover·키보드 포커스에서 텍스트 대비를 충족한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: 900 });
		await page.route(endpoint, (route) => route.fulfill({ status: 503, json: unavailable }));
		await page.goto('/home');
		const button = page.getByRole('button', { name: '다시 확인하기', exact: true });
		await expect(button).toBeVisible();
		const normal = await textContrast(button);
		await testInfo.attach('retry-contrast', {
			body: JSON.stringify(normal),
			contentType: 'application/json'
		});
		expect(normal.ratio).toBeGreaterThanOrEqual(4.5);
		await button.hover();
		expect(await textContrast(button)).toEqual(normal);
		await page.keyboard.press('Tab');
		await button.focus();
		await expect(button).toBeFocused();
		expect(await textContrast(button)).toEqual(normal);
		expect(await button.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe(
			'solid'
		);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	});
}

test('재시도 실패 후 Space·Enter·클릭을 지원하고 로딩 중 추가 사용자 입력은 요청하지 않는다', async ({
	page
}) => {
	let calls = 0;
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route(endpoint, async (route) => {
		const call = ++calls;
		if (call <= 2) return route.fulfill({ status: 503, json: unavailable });
		const data = dailyMessageFixture();
		if (call === 3) {
			data.status = 'PARTIAL';
			data.area = 'activity';
			data.code = 'ACTIVE_DAY';
			data.scoreComputedAt = null;
			data.dataAsOf.sleeps = null;
			data.notices = [{ metric: 'sleeps', code: 'DATA_NOT_READY', message: '수면 기록 준비 중' }];
		}
		if (call === 4) await gate;
		await route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
	const retry = card.getByRole('button', { name: '다시 확인하기', exact: true });
	await retry.click();
	await expect(card.getByRole('alert')).toBeVisible();
	await expect.poll(() => calls).toBe(2);
	await retry.focus();
	await page.keyboard.press('Space');
	await expect(card).toContainText('수면 기록 준비 중');
	await retry.focus();
	await page.keyboard.press('Enter');
	const loading = card.getByRole('button', { name: '다시 확인하는 중', exact: true });
	await expect(loading).toBeDisabled();
	await expect(card).toHaveAttribute('aria-busy', 'true');
	await expect.poll(() => calls).toBe(4);
	try {
		await page.keyboard.press('Enter');
		await page.keyboard.press('Space');
		const bounds = await loading.boundingBox();
		if (!bounds) throw new Error('로딩 버튼이 없습니다.');
		await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2, {
			clickCount: 3
		});
		expect(calls).toBe(4);
	} finally {
		release();
	}
	await expect(card).toHaveAttribute('aria-busy', 'false');
	await expect(card.getByRole('heading')).toHaveText(dailyMessageFixture().title);
	await expect(card.getByRole('button')).toHaveCount(0);
	expect(calls).toBe(4);
});
