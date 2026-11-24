import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { mockHomeApi } from '../lib/testing/home-api-fixture';
const metricCases = [
	['걸음 수', '3,200보', '/health/activity', 'icon-foot.svg'],
	['운동 시간', '18분', '/health/activity#exercise', 'icon-exercise.svg'],
	['수면', '7시간 0분', '/health/sleep', 'icon-sleep.svg'],
	['안정 시 심박수', '64.5 bpm', '/health/heart', 'icon-heart.svg']
];
const expectedIcons = metricCases.map(([, , , icon]) =>
	readFileSync(new URL(`../lib/assets/icons/${icon}`, import.meta.url), 'utf8')
);
import { mockSignedIn } from '../lib/testing/e2e-auth';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

test('Home은 미니 그래프·보조 설명·자세히 보기 없이 핵심 지표와 코칭 이동을 제공한다', async ({
	page
}) => {
	await page.goto('/home');
	await expect(page).toHaveTitle('홈 | 캔디핏');
	const records = page.getByRole('region', { name: '최근 건강 기록' });
	await expect(records.locator('svg')).toHaveCount(0);
	await expect(records).not.toContainText('평균');
	await expect(records.locator('.metric-description')).toHaveCount(0);
	await expect(page.getByRole('link', { name: '자세히 보기', exact: true })).toHaveCount(0);
	await expect(page.getByText(/지표별 상세 정보는 건강 데이터에서/)).toHaveCount(0);
	await expect(page.getByRole('link', { name: 'AI 코치에게 물어보기', exact: true })).toBeVisible();
});

for (const [label, value, path] of metricCases) {
	test(`홈 ${label} 카드 전체 클릭 및 Enter → ${path}`, async ({ page }) => {
		for (const interaction of ['click', 'keyboard']) {
			await page.goto('/home');
			const card = page
				.getByRole('region', { name: '최근 건강 기록' })
				.getByRole('link', { name: `${label} ${value}`, exact: true });
			await expect(card).toHaveAttribute('href', path);
			await expect(card).not.toHaveAttribute('aria-label');
			await expect(card).toHaveCSS('text-decoration-line', 'none');
			await expect(card.locator('a, button, input, select, textarea')).toHaveCount(0);
			if (interaction === 'click') {
				// 카드 왼쪽 여백도 이동 영역이어야 한다.
				await card.click({ position: { x: 6, y: 80 } });
			} else {
				await card.focus();
				await expect(card).toBeFocused();
				await page.keyboard.press('Enter');
			}
			await expect(page).toHaveURL(path);
			if (path === '/health/activity') {
				await expect(
					page.getByRole('heading', { name: '일별 걸음 수', exact: true })
				).toBeVisible();
			} else if (path === '/health/activity#exercise')
				await expect(
					page.getByRole('heading', { name: '운동', level: 2, exact: true })
				).toBeInViewport();
			else await expect(page.getByRole('heading', { level: 1, name: '건강 데이터' })).toBeVisible();
		}
	});
}

test('로그인한 사용자가 루트로 들어오면 홈으로 이동하고 뒤로 가도 루트로 돌아오지 않는다', async ({
	page
}) => {
	let sessionCalls = 0;
	page.on('request', (request) => {
		if (request.url().endsWith('/api/v1/auth/session')) sessionCalls += 1;
	});
	await page.goto('about:blank');
	await page.goto('/');

	await expect(page).toHaveURL('/home');
	await expect(page.getByRole('heading', { level: 1, name: '오늘의 점수' })).toBeVisible();
	// 로그인 확인은 홈의 AuthGate가 한 번만 한다
	expect(sessionCalls).toBe(1);
	// 루트는 기록에 남지 않아 뒤로 가기가 다시 홈으로 튕기지 않는다
	await page.goBack();
	await expect(page).toHaveURL('about:blank');
});

test('로그인 기록이 없는 사용자(가입 전 포함)는 루트에서 로그인 화면으로 간다', async ({
	page
}) => {
	await page.route('**/api/v1/auth/session', (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요해요.' } }
		})
	);
	await page.goto('/');

	await expect(page).toHaveURL('/login');
	await expect(page.getByRole('heading', { name: '캔디핏 로그인' })).toBeVisible();
	// 홈 화면을 잠깐이라도 그리지 않는다
	await expect(page.getByRole('banner')).toHaveCount(0);
});

test('로그인 확인이 일시 실패하면 루트에서 다시 시도 화면을 보여 준다', async ({ page }) => {
	await page.route('**/api/v1/auth/session', (route) =>
		route.fulfill({
			status: 503,
			json: { error: { code: 'AUTH_UNAVAILABLE', message: '잠시 후 다시', retryable: true } }
		})
	);
	await page.goto('/');

	await expect(
		page.getByRole('heading', { name: '로그인 상태를 확인하지 못했어요' })
	).toBeVisible();
	await expect(page.getByRole('button', { name: '다시 시도' })).toBeVisible();
});

for (const width of [360, 1366, 1920]) {
	test(`${width}px에서 홈의 AI 코치에게 물어보기 링크로 코칭 화면에 이동한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/home');
		const coachingLink = page.getByRole('link', { name: 'AI 코치에게 물어보기', exact: true });
		await expect(coachingLink).toHaveAttribute('href', '/coaching');
		await expect(coachingLink).toHaveCSS('cursor', 'pointer');
		if (width === 1366) {
			await coachingLink.focus();
			await page.keyboard.press('Enter');
		} else {
			await coachingLink.click();
		}
		await expect(page).toHaveURL('/coaching');
		await expect(
			page.getByRole('heading', { level: 1, name: 'AI 코치', exact: true })
		).toBeVisible();
		await expect(
			page
				.getByRole('navigation', { name: '주 메뉴' })
				.getByRole('link', { name: 'AI 코치', exact: true })
		).toHaveAttribute('aria-current', 'page');
	});
}

for (const width of [360, 390, 639, 640, 767, 768, 820, 1023, 1024, 1366, 1440, 1536, 1920, 2560]) {
	test(`${width}px에서 최신 홈 건강 기록 4개와 아이콘을 잘림 없이 표시한다`, async ({ page }) => {
		await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
		await page.goto('/home', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		const records = page.getByRole('region', { name: '최근 건강 기록' });
		const cards = records.getByRole('link');
		await expect(cards).toHaveCount(4);
		await expect(cards.getByRole('heading', { level: 2 })).toHaveText(
			metricCases.map(([label]) => label)
		);
		const expected = metricCases.map(([, value]) => value);
		for (let index = 0; index < expected.length; index++) {
			const card = cards.nth(index);
			await expect(card.getByText(expected[index], { exact: true })).toBeVisible();
			await expect
				.poll(() =>
					card
						.locator('img')
						.evaluateAll((images: HTMLImageElement[]) =>
							images.every((image) => image.complete && image.naturalWidth > 0)
						)
				)
				.toBe(true);
			expect(
				await card.evaluate((element) => {
					const bounds = element.getBoundingClientRect();
					return Array.from(element.querySelectorAll('h2, p, img, svg')).every((child) => {
						const rect = child.getBoundingClientRect();
						return (
							rect.left >= bounds.left &&
							rect.right <= bounds.right &&
							rect.top >= bounds.top &&
							rect.bottom <= bounds.bottom &&
							child.scrollWidth <= child.clientWidth
						);
					});
				})
			).toBe(true);
		}
		const icons = await cards.locator('span img').evaluateAll(async (images, expected) => {
			// Vite의 data URL 인라인 여부와 따옴표·태그 사이 공백에 관계없이 SVG 전체를 비교한다.
			const normalize = (svg: string) => {
				const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
				if (document.querySelector('parsererror'))
					throw new Error('아이콘 SVG를 읽을 수 없습니다.');
				return document.documentElement.outerHTML.replace(/>\s+</g, '><');
			};
			return {
				actual: await Promise.all(
					images.map(async (image) => {
						const source = image.getAttribute('src');
						if (!source) throw new Error('아이콘 경로가 없습니다.');
						const response = await fetch(source);
						if (!response.ok) throw new Error('아이콘을 불러오지 못했습니다.');
						return normalize(await response.text());
					})
				),
				expected: expected.map(normalize)
			};
		}, expectedIcons);
		expect(icons.actual).toEqual(icons.expected);
		expect(new Set(icons.actual).size).toBe(4);
		const hero = await page.getByRole('region', { name: '오늘의 점수', exact: true }).boundingBox();
		const copy = await page.locator('.hero-copy').boundingBox();
		const recordsBounds = await records.boundingBox();
		const headerBounds = await page.getByRole('banner').boundingBox();
		if (!hero || !copy || !recordsBounds || !headerBounds)
			throw new Error('홈 레이아웃 위치를 확인할 수 없습니다.');
		expect(copy.y).toBeGreaterThanOrEqual(headerBounds.y + headerBounds.height);
		// Figma illustration의 투명 여백은 인접 영역과 겹칠 수 있다.
		expect(recordsBounds.y).toBeGreaterThanOrEqual(hero.y + hero.height);
		if (width < 1024) {
			const image = await page.locator('.hero-image').boundingBox();
			const trigger = await page.locator('.summary-trigger').boundingBox();
			const states = await page.locator('.summary-status').all();
			if (!image || !trigger) throw new Error('모바일 점수 요약을 확인할 수 없습니다.');
			expect(states).toHaveLength(3);
			const firstState = await states[0].boundingBox();
			if (!firstState) throw new Error('첫 상태 항목이 없습니다.');
			expect(image.x - copy.x - copy.width).toBeGreaterThanOrEqual(12);
			expect(image.width / image.height).toBeCloseTo(4 / 3, 2);
			expect(trigger.y).toBeGreaterThanOrEqual(
				Math.max(copy.y + copy.height, image.y + image.height)
			);
			if (width < 640) expect(hero.height).toBeLessThanOrEqual(220);
			for (const state of states) {
				const bounds = await state.boundingBox();
				if (!bounds) throw new Error('상태 항목이 없습니다.');
				expect(bounds.y).toBeCloseTo(firstState.y, 1);
				expect(bounds.width).toBeCloseTo(firstState.width, 1);
			}
			if (width === 390) {
				const lastCard = await cards.last().boundingBox();
				const nav = await page.getByRole('navigation', { name: '주 메뉴' }).boundingBox();
				if (!lastCard || !nav) throw new Error('첫 화면 지표 위치를 확인할 수 없습니다.');
				expect(lastCard.y + lastCard.height).toBeLessThanOrEqual(nav.y);
			}
			// 긴 표시 문구도 높이가 늘어나며 각 열 안에서 온전히 줄바꿈되어야 한다.
			await page
				.locator('.status-label')
				.first()
				.evaluate((element) => {
					element.textContent = '긴 상태 문구가 여러 줄로 표시되어도 잘리지 않아야 합니다';
				});
			expect((await page.locator('.home-hero').boundingBox())!.height).toBeGreaterThan(hero.height);
			for (const label of await page.locator('.status-label').all()) {
				expect(
					await label.evaluate((element) => {
						const bounds = element.getBoundingClientRect();
						const cell = element.parentElement!.getBoundingClientRect();
						return (
							bounds.left >= cell.left &&
							bounds.right <= cell.right &&
							bounds.bottom <= cell.bottom &&
							element.scrollWidth <= element.clientWidth
						);
					})
				).toBe(true);
			}
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	});
}

test('/home을 직접 열 수 있고 홈 로고와 메뉴도 /home을 가리킨다', async ({ page }) => {
	const response = await page.goto('/home');
	expect(response?.status()).toBe(200);
	await expect(page.getByRole('heading', { level: 1, name: '오늘의 점수' })).toBeVisible();
	const logo = page.getByRole('link', { name: 'CANDiY-FiT 홈' });
	const homeMenu = page
		.getByRole('navigation', { name: '주 메뉴' })
		.getByRole('link', { name: '홈', exact: true });
	await expect(logo).toHaveAttribute('href', '/home');
	await expect(homeMenu).toHaveAttribute('href', '/home');
	await logo.click();
	await expect(page).toHaveURL('/home');
	await homeMenu.click();
	await expect(page).toHaveURL('/home');
});

test('1920×1080에서 홈 카드의 균등 배치와 간결한 수치를 유지한다', async ({ page }) => {
	await page.setViewportSize({ width: 1920, height: 1080 });
	await page.goto('/home');
	await page.evaluate(() => document.fonts.ready);
	const records = page.getByRole('region', { name: '최근 건강 기록' });
	await expect(records.locator('img[src*="home-"]')).toHaveCount(0);
	const boxes = await records.getByRole('link').evaluateAll((links) =>
		links.map((el) => {
			const b = el.getBoundingClientRect();
			return { x: b.x, y: b.y, width: b.width, right: b.right };
		})
	);
	for (const box of boxes) {
		expect(box.y).toBeCloseTo(boxes[0].y, 1);
		expect(box.width).toBeCloseTo(boxes[0].width, 1);
	}
	const gaps = boxes.slice(1).map((box, i) => box.x - boxes[i].right);
	expect(gaps.every((gap) => gap > 0 && Math.abs(gap - gaps[0]) < 1)).toBe(true);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

for (const viewport of [
	{ width: 1366, height: 768 },
	{ width: 1440, height: 900 },
	{ width: 1536, height: 864 },
	{ width: 1920, height: 1080 },
	{ width: 2560, height: 1440 }
]) {
	test(`${viewport.width}×${viewport.height}에서 Hero 간격과 이미지 비율을 유지하고 하단에 접근한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/home', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		const copy = await page.locator('.hero-copy').boundingBox();
		const summary = await page
			.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' })
			.boundingBox();
		const image = await page.locator('.hero-image').boundingBox();
		const title = await page.getByRole('heading', { level: 1 }).boundingBox();
		const total = await page.locator('.hero-total').boundingBox();
		if (!copy || !summary || !image || !title || !total)
			throw new Error('Hero 요소 위치를 확인할 수 없습니다.');
		// 브랜드 라벨을 제외한 점수 본문과 실제 상태 박스의 중심을 비교한다.
		const scoreBodyCenter = (title.y + total.y + total.height) / 2;
		expect(Math.abs(summary.y + summary.height / 2 - scoreBodyCenter)).toBeLessThanOrEqual(1);
		expect(summary.x - (copy.x + copy.width)).toBeGreaterThanOrEqual(64);
		expect(image.x - (summary.x + summary.width)).toBeGreaterThanOrEqual(52);
		expect(image.width / image.height).toBeCloseTo(4 / 3, 2);
		// 세로 공간에 따라 장식만 400~512px 사이에서 압축하고 비율은 유지한다.
		expect(image.width).toBeGreaterThanOrEqual(400);
		expect(image.width).toBeLessThanOrEqual(512);
		await expect(page.getByRole('heading', { level: 1 })).toHaveCSS('font-size', '60px');
		await expect(page.locator('.hero-total')).toHaveCSS('font-size', '60px');
		await expect(page.locator('.score-denominator')).toHaveCSS('font-size', '44px');
		for (const value of await page.locator('.metric-value').all()) {
			const fontSize = await value.evaluate((element) =>
				parseFloat(getComputedStyle(element).fontSize)
			);
			expect(fontSize).toBeGreaterThanOrEqual(36);
			expect(fontSize).toBeLessThanOrEqual(40);
		}
		const main = await page.getByRole('main').boundingBox();
		const records = await page.getByRole('region', { name: '최근 건강 기록' }).boundingBox();
		if (!main || !records) throw new Error('본문 너비를 확인할 수 없습니다.');
		expect(Math.abs(main.width - 1344)).toBeLessThanOrEqual(1);
		expect(Math.abs(records.width - 1280)).toBeLessThanOrEqual(1);
		for (const bounds of [copy, summary, image]) {
			expect(bounds.x).toBeGreaterThanOrEqual(0);
			expect(bounds.y).toBeGreaterThanOrEqual(0);
			expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width);
			expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
		}
		expect(
			await page
				.getByRole('heading', { level: 1 })
				.evaluate((element) => element.scrollWidth <= element.clientWidth)
		).toBe(true);
		await expect(page.getByRole('banner')).toHaveCount(1);
		await expect(
			page.getByRole('region', { name: '최근 건강 기록' }).getByRole('link')
		).toHaveCount(4);
		for (const heading of await page.getByRole('main').getByRole('heading', { level: 2 }).all()) {
			await heading.scrollIntoViewIfNeeded();
			await expect(heading).toBeInViewport({ ratio: 1 });
		}
		const footer = page.getByRole('contentinfo');
		await footer.scrollIntoViewIfNeeded();
		await expect(footer).toBeInViewport();
		const footerBounds = await footer.boundingBox();
		if (!footerBounds) throw new Error('footer 위치를 확인할 수 없습니다.');
		expect(footerBounds.y).toBeGreaterThanOrEqual(0);
		// 정수 스크롤 위치와 소수점 layout 좌표 사이의 반올림 차이만 허용한다.
		expect(footerBounds.y + footerBounds.height).toBeLessThanOrEqual(viewport.height + 1);
		for (const button of await footer.getByRole('button').all()) {
			await expect(button).toBeInViewport({ ratio: 1 });
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			viewport.width
		);
	});
}

for (const [before, after] of [
	[
		{ width: 767, height: 1024 },
		{ width: 768, height: 1024 }
	],
	[
		{ width: 1920, height: 901 },
		{ width: 1920, height: 900 }
	],
	[
		{ width: 1440, height: 901 },
		{ width: 1440, height: 900 }
	],
	[
		{ width: 1535, height: 1080 },
		{ width: 1536, height: 1080 }
	],
	[
		{ width: 1535, height: 864 },
		{ width: 1536, height: 864 }
	],
	[
		{ width: 1366, height: 701 },
		{ width: 1366, height: 700 }
	]
]) {
	test(`${before.width}×${before.height} → ${after.width}×${after.height}에서 Home 구성이 튀지 않는다`, async ({
		page
	}) => {
		const layouts = [];
		for (const viewport of [before, after]) {
			await page.setViewportSize(viewport);
			await page.goto('/home', { waitUntil: 'networkidle' });
			await page.evaluate(() => document.fonts.ready);
			layouts.push(
				await page.evaluate(
					({ includeFooter, scoreOnly }) => {
						const header = document.querySelector('header');
						const main = document.querySelector('main');
						if (!header || !main) throw new Error('Home shell을 찾을 수 없습니다.');
						return [
							'.hero-image',
							'.hero-copy',
							'.summary-trigger',
							'.hero-title',
							'.hero-total',
							// 768px에서 다른 홈 영역은 기존 breakpoint에 따라 전환된다.
							...(scoreOnly
								? []
								: [
										'.health-records a',
										'.home-highlights section',
										'.home-highlights section > div',
										'.home-highlights img',
										'.coaching-highlight',
										'.coaching-highlight > div',
										'.coaching-highlight img'
									]),
							...(includeFooter ? ['footer'] : [])
						].map((selector) => {
							const element = document.querySelector(selector);
							if (!element) throw new Error(`${selector}를 찾을 수 없습니다.`);
							const bounds = element.getBoundingClientRect();
							return {
								// 공통 Header의 2xl 높이 변화는 Home 내부 구성 변화에서 제외한다.
								x: bounds.x - main.getBoundingClientRect().x,
								y: bounds.y - header.getBoundingClientRect().bottom,
								width: bounds.width,
								height: bounds.height
							};
						});
					},
					{ includeFooter: before.width === after.width, scoreOnly: before.width === 767 }
				)
			);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
		}
		for (let index = 0; index < layouts[0].length; index++) {
			for (const key of ['x', 'y', 'width', 'height'] as const) {
				// 1px viewport 변화에서 연속적인 보간·소수점 반올림만 허용한다.
				expect(Math.abs(layouts[1][index][key] - layouts[0][index][key])).toBeLessThanOrEqual(2);
			}
		}
	});
}

for (const width of [1440, 1920]) {
	test(`${width}px에서 높이를 줄일 때 압축한 뒤에도 부족하면 페이지 스크롤을 유지한다`, async ({
		page
	}) => {
		let hadScroll = false;
		let previousSizes: number[] | undefined;
		for (const height of [1080, 1000, 901, 900, 864, 800, 768, 701, 700, 600]) {
			await page.setViewportSize({ width, height });
			await page.goto('/home', { waitUntil: 'networkidle' });
			await page.evaluate(() => document.fonts.ready);
			const sizes = await page.evaluate(() =>
				['.hero-image', '.metric-card', '.home-highlights section'].map((selector) => {
					const element = document.querySelector(selector);
					if (!element) throw new Error(`${selector}를 찾을 수 없습니다.`);
					return element.getBoundingClientRect().height;
				})
			);
			if (previousSizes) {
				for (let index = 0; index < sizes.length; index++) {
					expect(sizes[index], `${height}px에서 세로 크기가 커지지 않음`).toBeLessThanOrEqual(
						previousSizes[index] + 1
					);
				}
			}
			previousSizes = sizes;
			const hasScroll = await page.evaluate(
				() => document.documentElement.scrollHeight > innerHeight
			);
			if (hadScroll) expect(hasScroll, `${height}px에서 페이지 스크롤 유지`).toBe(true);
			hadScroll = hasScroll;
		}
		expect(hadScroll).toBe(true);
	});
}

for (const viewport of [
	{ width: 1366, height: 768 },
	{ width: 1440, height: 900 },
	{ width: 1536, height: 864 },
	{ width: 1920, height: 1080 },
	{ width: 2560, height: 1440 }
]) {
	test(`${viewport.width}×${viewport.height}에서 Header와 홈, footer가 한 화면에 들어온다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/home', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		await expect(page.getByRole('banner')).toHaveCount(1);
		const dimensions = await page.evaluate(() => ({
			height: document.documentElement.scrollHeight,
			width: document.documentElement.scrollWidth
		}));
		expect(dimensions.height).toBeLessThanOrEqual(viewport.height);
		expect(dimensions.width).toBeLessThanOrEqual(viewport.width);
		const footer = page.getByRole('contentinfo');
		await expect(footer).toBeInViewport({ ratio: 1 });
		const headings = page.getByRole('main').getByRole('heading', { level: 2 });
		await expect(headings).toHaveCount(6);
		for (const heading of await headings.all()) {
			await expect(heading).toBeInViewport({ ratio: 1 });
		}
		for (const content of await page
			.locator(
				'.health-records a, .home-highlights section, .home-highlights button, .home-highlights a'
			)
			.all()) {
			await expect(content).toBeInViewport({ ratio: 1 });
		}
	});
}

for (const viewport of [
	{ width: 1366, height: 600 },
	{ width: 683, height: 384 }
]) {
	test(`${viewport.width}×${viewport.height}에서 공간이 부족하면 스크롤하여 하단 콘텐츠에 접근한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await page.goto('/home', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeGreaterThan(
			viewport.height
		);
		const headings = page.getByRole('main').getByRole('heading', { level: 2 });
		await expect(headings).toHaveCount(6);
		for (const heading of await headings.all()) {
			await heading.evaluate((element) => element.scrollIntoView({ block: 'center' }));
			await expect(heading).toBeInViewport({ ratio: 1 });
		}
		const footer = page.getByRole('contentinfo');
		await footer.scrollIntoViewIfNeeded();
		await expect(footer).toBeInViewport();
		const footerBounds = await footer.boundingBox();
		if (!footerBounds) throw new Error('footer 위치를 확인할 수 없습니다.');
		expect(footerBounds.y).toBeGreaterThanOrEqual(0);
		// document의 정수 스크롤 높이와 소수점 좌표 사이의 반올림 차이만 허용한다.
		expect(footerBounds.y + footerBounds.height).toBeLessThanOrEqual(viewport.height + 1);
		expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0);
		expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
			viewport.width
		);
	});
}
