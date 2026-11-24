import { expect, test, type Locator } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { mockHomeApi } from '../../../lib/testing/home-api-fixture';
import { dailyMessageFixture } from '../../../lib/testing/daily-message-fixture';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const endpoint = '**/api/v1/health/daily-message';

// object-fit의 여백과 원본의 투명 픽셀을 제외한 실제 이미지 범위를 확인한다.
async function visibleMessageImage(card: Locator) {
	return card.locator('.message-image').evaluate(async (element) => {
		if (!(element instanceof HTMLImageElement)) throw new Error('캐릭터 이미지가 없습니다.');
		await element.decode();
		const canvas = document.createElement('canvas');
		canvas.width = element.naturalWidth;
		canvas.height = element.naturalHeight;
		const context = canvas.getContext('2d');
		if (!context) throw new Error('캐릭터 투명 여백을 확인할 수 없습니다.');
		context.drawImage(element, 0, 0);
		const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
		let left = canvas.width,
			top = canvas.height,
			right = 0,
			bottom = 0;
		for (let y = 0; y < canvas.height; y++) {
			for (let x = 0; x < canvas.width; x++) {
				if (pixels[(y * canvas.width + x) * 4 + 3] === 0) continue;
				left = Math.min(left, x);
				top = Math.min(top, y);
				right = Math.max(right, x + 1);
				bottom = Math.max(bottom, y + 1);
			}
		}
		const box = element.getBoundingClientRect();
		const scale = Math.min(box.width / canvas.width, box.height / canvas.height);
		const imageLeft = box.right - canvas.width * scale;
		const imageTop = box.bottom - canvas.height * scale;
		return {
			left: imageLeft + left * scale,
			top: imageTop + top * scale,
			right: imageLeft + right * scale,
			bottom: imageTop + bottom * scale
		};
	});
}
test.use({ timezoneId: 'America/Los_Angeles' });
test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

for (const [width, height] of [
	[1920, 1080],
	[1440, 900],
	[1366, 768],
	[1280, 720],
	[1024, 768],
	[1056, 768],
	[1088, 1080],
	[1100, 768],
	[1152, 768],
	[1279, 768],
	[768, 1024],
	[820, 1024],
	[1023, 1024],
	[767, 1024],
	[640, 960],
	[639, 960],
	[390, 844],
	[360, 800]
]) {
	test(`${width}px: 시각 정보 없이 설명 한 줄·두 줄에서 카드와 푸터 위치를 유지한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height });
		const data = dailyMessageFixture();
		data.status = 'WAITING';
		data.code = 'PENDING_RECORDS';
		data.title = '오늘도 나를 응원해요';
		data.description = '건강한 하루를 응원해요.';
		await page.route(endpoint, (route) => route.fulfill({ json: { data } }));
		await page.goto('/home');
		const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
		await expect(card.getByRole('heading')).toHaveText(data.title);
		await page.evaluate(() => document.fonts.ready);
		const descriptionLines = () =>
			card.locator('.message-description').evaluate((element) => {
				const range = document.createRange();
				range.selectNodeContents(element);
				return [...range.getClientRects()].filter((rect) => rect.width > 0).length;
			});
		expect(await descriptionLines()).toBe(1);
		await expect(card.getByText('동기화 대기', { exact: true })).toHaveCount(0);
		await expect(card.getByText('기록이 반영된 시각', { exact: true })).toHaveCount(0);
		await expect(card.getByLabel('기록이 반영된 시각', { exact: true })).toHaveCount(0);
		await expect(card.getByLabel('수면 점수 계산 시각')).toHaveCount(0);
		await expect(card.locator('time, dl')).toHaveCount(0);
		const layout = async () => {
			const visibleImage = await visibleMessageImage(card);
			return page.evaluate((visibleImage) => {
				const card = document.querySelector('.health-highlight');
				const footer = document.querySelector('footer');
				if (!card || !footer) throw new Error('카드 또는 푸터가 없습니다.');
				const label = card.querySelector('.message-label');
				const content = card.querySelector('.message-content');
				const image = card.querySelector('img');
				const heading = content?.querySelector('h2');
				if (!label || !content || !image || !heading)
					throw new Error('한마디 카드 요소가 없습니다.');
				const cardBox = card.getBoundingClientRect();
				const contentBox = content.getBoundingClientRect();
				const imageBox = image.getBoundingClientRect();
				return {
					labelTop: label.getBoundingClientRect().top - contentBox.top,
					paddingTop: Number.parseFloat(getComputedStyle(content).paddingTop),
					messageGap: heading.getBoundingClientRect().top - label.getBoundingClientRect().bottom,
					bubbleColor: getComputedStyle(content).backgroundColor,
					contentImageGap: visibleImage.left - contentBox.right,
					imageBottomGap:
						cardBox.bottom -
						Number.parseFloat(getComputedStyle(card).paddingBottom) -
						imageBox.bottom,
					imageInsideCard: imageBox.top >= cardBox.top && imageBox.bottom <= cardBox.bottom,
					cardHeight: card.getBoundingClientRect().height,
					footerTop: footer.getBoundingClientRect().top + scrollY,
					documentHeight: document.documentElement.scrollHeight,
					horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
					banners: [...document.querySelectorAll('.home-highlights section')].map((element) => {
						const rect = element.getBoundingClientRect();
						return {
							width: rect.width,
							top: rect.top + scrollY,
							height: rect.height,
							bottom: rect.bottom + scrollY
						};
					})
				};
			}, visibleImage);
		};
		const before = await layout();
		expect(before.horizontalOverflow).toBe(false);
		expect(before.labelTop).toBeCloseTo(before.paddingTop, 0);
		expect(before.contentImageGap).toBeGreaterThanOrEqual(12);
		expect(before.messageGap).toBeCloseTo(8, 0);
		expect(before.bubbleColor).toBe('rgb(255, 251, 244)');
		expect(before.imageBottomGap).toBeCloseTo(0, 0);
		expect(before.imageInsideCard).toBe(true);
		expect(
			await page.locator('.coaching-ending').evaluate((element) => {
				const range = document.createRange();
				range.selectNodeContents(element);
				return range.getClientRects().length;
			})
		).toBe(1);
		if (width >= 1024) {
			expect(before.banners[0].height).toBe(before.banners[1].height);
			expect(before.banners[0].bottom).toBe(before.banners[1].bottom);
			if (width < 1280) {
				const coach = page.locator('.coaching-highlight');
				const heading = await coach.getByRole('heading').boundingBox();
				const button = await coach.getByRole('link').boundingBox();
				const image = await coach.locator('img').boundingBox();
				if (!heading || !button || !image) throw new Error('코칭 배너 요소가 없습니다.');
				expect(heading.x + heading.width).toBeLessThanOrEqual(image.x);
				expect(button.x + button.width).toBeLessThanOrEqual(image.x);
			}
		} else {
			expect(before.banners[1].height).toBeCloseTo(width < 640 ? 160 : 156, 1);
			expect(before.banners[1].width).toBeCloseTo(before.banners[0].width, 1);
			expect(before.banners[1].top - before.banners[0].bottom).toBeCloseTo(16, 1);
			const coach = page.locator('.coaching-highlight');
			const heading = await coach.getByRole('heading').boundingBox();
			const button = await coach.getByRole('link', { name: 'AI 코치에게 물어보기' }).boundingBox();
			const image = await coach.locator('img').boundingBox();
			const group = await coach.locator('.coaching-content').boundingBox();
			const bounds = await coach.boundingBox();
			if (!heading || !button || !image || !group || !bounds)
				throw new Error('코칭 배너 요소가 없습니다.');
			expect(button.height).toBeGreaterThanOrEqual(36);
			expect(button.y - heading.y - heading.height).toBeCloseTo(16, 1);
			expect(image.x).toBeGreaterThanOrEqual(group.x + group.width);
			expect(group.y + group.height / 2).toBeCloseTo(bounds.y + bounds.height / 2, 1);
			await expect(coach.getByRole('link')).toHaveAttribute('href', '/coaching');
		}
		await page.screenshot({
			path: testInfo.outputPath(`description-one-${width}.png`),
			fullPage: true
		});
		data.description = await card.locator('.message-description').evaluate((element) => {
			const style = getComputedStyle(element);
			const context = document.createElement('canvas').getContext('2d');
			if (!context) throw new Error('문구 너비를 확인할 수 없습니다.');
			context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
			const source = '건강 기록을 돌아보며 나를 위한 작은 습관을 이어 가세요. '.repeat(20);
			let text = '';
			for (const character of source) {
				text += character;
				if (context.measureText(text).width >= element.clientWidth * 1.5) break;
			}
			return text.trim();
		});
		await page.reload();
		await expect(card.locator('.message-description')).toHaveText(data.description);
		await page.evaluate(() => document.fonts.ready);
		expect(await descriptionLines()).toBe(2);
		const after = await layout();
		expect(after).toEqual(before);
		const artifacts = resolve('../../test-results/home-230/final-review');
		mkdirSync(artifacts, { recursive: true });
		writeFileSync(
			resolve(artifacts, `description-${width}.json`),
			JSON.stringify({ width, height, before, after }, null, 2)
		);
		await page.screenshot({
			path: testInfo.outputPath(`description-two-${width}.png`),
			fullPage: true
		});
	});
}

for (const status of ['READY', 'PARTIAL', 'EMPTY', 'WAITING'] as const) {
	test(`${status}: 서버 문구와 null area 및 필수 notices를 표시한다`, async ({ page }) => {
		const data = dailyMessageFixture();
		data.status = status;
		if (status === 'EMPTY' || status === 'WAITING') {
			data.code = status === 'EMPTY' ? 'NO_RECORDS' : 'PENDING_RECORDS';
			data.title =
				status === 'EMPTY'
					? '오늘은 기록 없이도 내 하루를 돌아볼까요?'
					: '오늘의 기록을 아직 확인하지 못했어요';
			data.description = '서버가 제공한 상태 안내';
			data.scoreComputedAt = null;
		}
		data.notices = [
			{
				metric: 'steps',
				code: 'SYNC_FAILED',
				message:
					status === 'EMPTY' || status === 'WAITING'
						? '최근 동기화에 실패했어요. 앱의 동기화 상태를 확인해 주세요.'
						: '최근 동기화에 실패해 이전에 저장된 기록을 보여 드려요.'
			}
		];
		if (status === 'PARTIAL') {
			data.code = 'ACTIVE_DAY';
			data.area = 'activity';
			data.title = '차곡차곡 쌓은 움직임, 뿌듯해해도 좋아요';
			data.description = '운동과 걸음 기록에 오늘의 움직임이 담겼어요.';
			data.scoreComputedAt = null;
			data.dataAsOf.sleeps = null;
			data.notices.push({
				metric: 'sleeps',
				code: 'LOOKUP_FAILED',
				message: '수면 기록은 불러오지 못했어요.'
			});
		}
		await page.route(endpoint, (route) => route.fulfill({ json: { data } }));
		await page.goto('/home');
		const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
		await expect(card.getByRole('heading')).toHaveText(data.title);
		await expect(card).toContainText(data.description);
		for (const notice of data.notices)
			await expect(card.getByRole('listitem').filter({ hasText: notice.message })).toBeVisible();
		await expect(card.getByRole('button', { name: /기록이 반영된 시각/ })).toHaveCount(0);
		await expect(card.getByText('기록이 반영된 시각', { exact: true })).toHaveCount(0);
		await expect(card.getByLabel('기록이 반영된 시각', { exact: true })).toHaveCount(0);
		await expect(card.getByLabel('수면 점수 계산 시각')).toHaveCount(0);
		await expect(card.locator('time')).toHaveCount(0);
		await expect(card).not.toContainText('확인 가능한 시각 없음');
		await expect(card.getByRole('button', { name: '다시 확인하기' })).toHaveCount(
			status === 'PARTIAL' ? 1 : 0
		);
		await expect(card.getByRole('alert')).toHaveCount(0);
	});
}

test('PARTIAL 재시도는 notices를 유지하고 한마디만 다시 조회한다. 새로고침은 새 요청이다', async ({
	page
}) => {
	let calls = 0;
	let release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	const other: string[] = [];
	page.on('request', (request) => {
		const pathname = new URL(request.url()).pathname;
		if (pathname.startsWith('/api/v1/health/') && !pathname.endsWith('/daily-message'))
			other.push(request.url());
	});
	await page.route(endpoint, async (route) => {
		calls++;
		const data = dailyMessageFixture();
		if (calls === 1) {
			data.status = 'PARTIAL';
			data.area = 'activity';
			data.code = 'ACTIVE_DAY';
			data.scoreComputedAt = null;
			data.dataAsOf.sleeps = null;
			data.notices = [
				{ metric: 'sleeps', code: 'DATA_NOT_READY', message: '수면 기록을 준비 중이에요.' }
			];
		}
		if (calls === 2) await gate;
		await route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
	await expect(card).toContainText('수면 기록을 준비 중이에요.');
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	expect(other).toHaveLength(5);
	await card.getByRole('button', { name: '다시 확인하기' }).focus();
	await page.keyboard.press('Enter');
	await expect(card).toHaveAttribute('aria-busy', 'true');
	await expect(card).toContainText('수면 기록을 준비 중이에요.');
	release();
	await expect(card.getByRole('button', { name: '다시 확인하기' })).toHaveCount(0);
	expect(other).toHaveLength(5);
	expect(calls).toBe(2);
	await page.reload();
	await expect(card).toHaveAttribute('aria-busy', 'false');
	expect(calls).toBe(3);
});

for (const reason of ['DATA_NOT_READY', 'LOOKUP_FAILED']) {
	test(`503 ${reason}: 내부 정보 없이 준비·조회 오류를 구분하고 재시도`, async ({ page }) => {
		let calls = 0;
		await page.route(endpoint, (route) =>
			++calls === 1
				? route.fulfill({
						status: 503,
						json: {
							error: {
								code: 'HEALTH_DATA_UNAVAILABLE',
								message: 'private SQL UUID',
								context: { reason, metrics: [] }
							}
						}
					})
				: route.fallback()
		);
		await page.goto('/home');
		const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
		await expect(card.getByRole(reason === 'DATA_NOT_READY' ? 'status' : 'alert')).toContainText(
			reason === 'DATA_NOT_READY'
				? '첫 기록을 보여 드릴 준비 중이에요'
				: '오늘의 한마디를 불러오지 못했어요'
		);
		await expect(card).not.toContainText('private');
		await expect(page.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
		await card.getByRole('button', { name: '다시 확인하기' }).click();
		await expect(card.getByRole('heading')).toHaveText(dailyMessageFixture().title);
	});
}

test('AUTH_EXPIRED는 갱신·재요청 없이 로그인으로 이동한다', async ({ page }) => {
	let calls = 0;
	await page.route(endpoint, (route) => {
		calls++;
		return route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_EXPIRED', message: 'private' } }
		});
	});
	await page.goto('/home');
	await expect(page).toHaveURL('/login');
	expect(calls).toBe(1);
});

test('403은 재시도를 제공하지 않는다', async ({ page }) => {
	await page.route(endpoint, (route) =>
		route.fulfill({
			status: 403,
			json: { error: { code: 'HEALTH_DATA_FORBIDDEN', message: 'private' } }
		})
	);
	await page.goto('/home');
	const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
	await expect(card.getByRole('alert')).toContainText('권한이 없어요');
	await expect(card.getByRole('button')).toHaveCount(0);
});

for (const width of [1920, 1366, 1024, 1023, 820, 768, 767, 640, 390, 360]) {
	test(`${width}px: 독립 로딩·긴 문구·200% 텍스트·여러 notices·시각 제거·오류의 줄바꿈과 접근성`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
		let release = () => {};
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		const data = dailyMessageFixture();
		data.title = '<strong>오늘의 건강 기록을 돌아보며 나를 응원해 주세요</strong>'.repeat(2);
		data.description =
			'차곡차곡 쌓인 기록을 돌아보며 오늘도 나를 위한 작은 걸음을 이어 가세요. '.repeat(3);
		data.status = 'PARTIAL';
		data.area = 'activity';
		data.code = 'ACTIVE_DAY';
		data.scoreComputedAt = null;
		data.dataAsOf.sleeps = null;
		data.notices = [
			{
				metric: 'steps',
				code: 'SYNC_FAILED',
				message: '최근 동기화에 실패해 이전에 저장된 기록을 보여 드려요.'
			},
			{
				metric: 'sleeps',
				code: 'LOOKUP_FAILED',
				message: '수면 기록은 불러오지 못했어요. '.repeat(4)
			}
		];
		await page.route(endpoint, async (route) => {
			await gate;
			await route.fulfill({ json: { data } });
		});
		await page.goto('/home');
		const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
		await expect(card.getByRole('status')).toBeVisible();
		await expect(card.locator('.message-image')).toBeVisible();
		await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
		release();
		await expect(card.getByRole('heading')).toHaveText(data.title);
		await expect(card.locator('strong')).toHaveCount(0);
		for (const notice of data.notices)
			await expect(card.getByRole('listitem').filter({ hasText: notice.message })).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		await expect(card.getByLabel('기록이 반영된 시각', { exact: true })).toHaveCount(0);
		await expect(card.locator('time')).toHaveCount(0);
		await expect(card.getByRole('button', { name: /기록이 반영된 시각/ })).toHaveCount(0);
		for (const element of await card.locator('h2,p,li,dl,button').all()) {
			expect(
				await element.evaluate(
					(el) => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 1
				)
			).toBe(true);
		}
		const visibleImage = await visibleMessageImage(card);
		const geometry = await card.evaluate((element, visibleImage) => {
			const content = element.querySelector('.message-content');
			const image = element.querySelector('img');
			if (!content || !image) throw new Error('텍스트 또는 이미지가 없습니다.');
			const textBox = content.getBoundingClientRect();
			const cardBox = element.getBoundingClientRect();
			return {
				gap: visibleImage.left - textBox.right,
				bottom: Math.max(textBox.bottom, visibleImage.bottom) - cardBox.bottom,
				inside:
					visibleImage.left >= cardBox.left &&
					visibleImage.right <= cardBox.right &&
					visibleImage.top >= cardBox.top
			};
		}, visibleImage);
		expect(geometry.gap).toBeGreaterThanOrEqual(12);
		expect(geometry.bottom).toBeLessThanOrEqual(0);
		expect(geometry.inside).toBe(true);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		await card.getByRole('button', { name: '다시 확인하기' }).focus();
		await expect(card.getByRole('button', { name: '다시 확인하기' })).toBeFocused();
		await page.evaluate(() => scrollTo(0, 0));
		await page.screenshot({
			path: testInfo.outputPath(`message-long-${width}.png`),
			fullPage: true
		});
		// 화면·이미지 크기를 유지하고 실제 텍스트의 글자 크기와 행간만 두 배로 확대한다.
		const coachHeightBeforeTextZoom = await page
			.locator('.coaching-highlight')
			.evaluate((element) => element.getBoundingClientRect().height);
		await page.locator('.home-highlights').evaluate((root) => {
			const text = [...root.querySelectorAll<HTMLElement>('*')]
				.filter((element) =>
					[...element.childNodes].some(
						(node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim()
					)
				)
				.map((element) => ({
					element,
					size: parseFloat(getComputedStyle(element).fontSize),
					line: parseFloat(getComputedStyle(element).lineHeight)
				}));
			for (const { element, size, line } of text) {
				element.style.fontSize = `${size * 2}px`;
				if (Number.isFinite(line)) element.style.lineHeight = `${line * 2}px`;
			}
		});
		for (const element of await page
			.locator(
				'.home-highlights h2, .home-highlights p, .home-highlights li, .home-highlights button, .home-highlights a'
			)
			.all()) {
			expect(
				await element.evaluate((el) => {
					if (el.scrollWidth > el.clientWidth + 1) return false;
					const range = document.createRange();
					range.selectNodeContents(el);
					const text = range.getBoundingClientRect();
					const container = (el.closest('.message-content') ??
						el.closest('section'))!.getBoundingClientRect();
					// leading-none의 글자 잉크는 행 박스를 넘을 수 있다. 실제 잘림·인접 요소 겹침을 확인한다.
					if (text.top < container.top || text.bottom > container.bottom) return false;
					if (el.scrollHeight > el.clientHeight + 1 && getComputedStyle(el).overflowY !== 'visible')
						return false;
					const next = el.nextElementSibling?.getBoundingClientRect();
					return !next || text.bottom <= next.top || text.right <= next.left;
				})
			).toBe(true);
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		const enlargedImage = await visibleMessageImage(card);
		const bubble = await card.locator('.message-content').boundingBox();
		if (!bubble) throw new Error('확대한 말풍선이 없습니다.');
		expect(enlargedImage.left - bubble.x - bubble.width).toBeGreaterThanOrEqual(12);
		const coach = page.locator('.coaching-highlight');
		if (width < 1024) {
			expect(
				await coach.evaluate((element) => element.getBoundingClientRect().height)
			).toBeGreaterThan(coachHeightBeforeTextZoom);
		}
		const group = await coach.locator('.coaching-content').boundingBox();
		const image = await coach.locator('img').boundingBox();
		if (!group || !image) throw new Error('확대한 코칭 배너가 없습니다.');
		expect(image.x >= group.x + group.width || image.y >= group.y + group.height).toBe(true);
		await page.screenshot({
			path: testInfo.outputPath(`message-text-200-${width}.png`),
			fullPage: true
		});
		await page.route(endpoint, (route) =>
			route.fulfill({
				status: 500,
				json: { error: { code: 'INTERNAL_ERROR', message: 'private' } }
			})
		);
		await page.reload();
		await expect(card.getByRole('alert')).toBeVisible();
		await expect(card.getByRole('button')).toHaveCount(0);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	});
}

test('KST 자정은 단발 갱신하며 전날 늦은 응답과 역전된 재시도를 적용하지 않는다', async ({
	page
}) => {
	await mockHomeApi(page, ['2026-10-26', '2026-10-27']);
	await page.clock.install({ time: new Date('2026-10-26T14:00:00Z') });
	await page.clock.pauseAt(new Date('2026-10-26T14:59:59Z'));
	await page.addInitScript(() => {
		const original = window.fetch;
		let calls = 0;
		window.fetch = async (input, options) => {
			if (!String(input).endsWith('/health/daily-message')) return original(input, options);
			const call = ++calls;
			const response = await original(input, { ...options, signal: undefined });
			if (call === 1 || call === 3)
				await new Promise<void>((resolve) =>
					window.addEventListener(`release-message-${call}`, () => resolve(), { once: true })
				);
			return response;
		};
	});
	let calls = 0;
	await page.route(endpoint, (route) => {
		calls++;
		const data = dailyMessageFixture(calls === 1 ? '2026-10-26' : '2026-10-27');
		data.title = `응답 ${calls}`;
		data.description = `서버 설명 ${calls}`;
		if (calls === 2) {
			data.status = 'PARTIAL';
			data.code = 'ACTIVE_DAY';
			data.area = 'activity';
			data.scoreComputedAt = null;
			data.dataAsOf.sleeps = null;
			data.notices = [{ metric: 'sleeps', code: 'LOOKUP_FAILED', message: '수면 조회 실패' }];
		}
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	await expect.poll(() => calls).toBe(1);
	await page.clock.fastForward(1100);
	const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
	await expect(card.getByRole('heading')).toHaveText('응답 2');
	await page.evaluate(() => window.dispatchEvent(new Event('release-message-1')));
	await expect(card.getByRole('heading')).toHaveText('응답 2');
	await card.getByRole('button', { name: '다시 확인하기' }).evaluate((button) => {
		button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	});
	// 취소 전에 실제 요청이 도착했음을 확인해 늦은 3번 응답과 빠른 4번 응답을 만든다.
	await expect.poll(() => calls).toBe(3);
	await card.getByRole('button', { name: '다시 확인하는 중' }).evaluate((button) => {
		button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	});
	await expect(card.getByRole('heading')).toHaveText('응답 4');
	await expect(card.locator('.message-description')).toHaveText('서버 설명 4');
	await expect(card.locator('time')).toHaveCount(0);
	await page.evaluate(() => window.dispatchEvent(new Event('release-message-3')));
	await expect(card.getByRole('heading')).toHaveText('응답 4');
	await page.clock.fastForward(3600000);
	expect(calls).toBe(4);
});

test.describe('기록 시각 제거 후 접근성', () => {
	test.use({ hasTouch: true, viewport: { width: 360, height: 800 } });
	test('모바일에서 시각 없이 메시지를 표시하고 터치로 재조회하지 않으며 Tab은 다음 링크로 이동한다', async ({
		page
	}) => {
		let calls = 0;
		page.on('request', (request) => {
			if (request.url().endsWith('/daily-message')) calls++;
		});
		await page.goto('/home');
		const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
		await expect(card.getByLabel('기록이 반영된 시각', { exact: true })).toHaveCount(0);
		await expect(card.locator('time')).toHaveCount(0);
		await expect(card.getByRole('heading')).toHaveText(dailyMessageFixture().title);
		await expect(card.getByRole('button')).toHaveCount(0);
		await card.locator('.message-description').tap();
		await expect(card.locator('.message-description')).toHaveText(
			dailyMessageFixture().description
		);
		await page.getByRole('link', { name: '안정 시 심박수 64.5 bpm', exact: true }).focus();
		await page.keyboard.press('Tab');
		await expect(page.getByRole('link', { name: 'AI 코치에게 물어보기' })).toBeFocused();
		expect(calls).toBe(1);
	});
});

test('백그라운드 복귀는 KST 날짜가 달라졌을 때만 갱신하고 전날 응답은 재조회한다', async ({
	page
}) => {
	await mockHomeApi(page, ['2026-10-26', '2026-10-27']);
	let calls = 0;
	await page.route(endpoint, (route) => {
		calls++;
		const data = dailyMessageFixture(calls < 3 ? '2026-10-26' : '2026-10-27');
		data.title = `응답 ${calls}`;
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	const card = page.getByRole('region', { name: '오늘의 한마디', exact: true });
	await expect(card.getByRole('heading')).toHaveText('응답 1');
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	expect(calls).toBe(1);
	await page.clock.setFixedTime(new Date('2026-10-26T15:10:00Z'));
	await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
	await expect(card.getByRole('heading')).toHaveText('응답 3');
	await expect(card).not.toContainText('응답 2');
	expect(calls).toBe(3);
});

test('페이지 이탈 시 대기 요청을 취소하고 재진입하면 새로 조회한다', async ({ page }) => {
	let calls = 0,
		release = () => {};
	const gate = new Promise<void>((resolve) => {
		release = resolve;
	});
	await page.route(endpoint, async (route) => {
		if (++calls === 1) await gate;
		await route.fulfill({ json: { data: dailyMessageFixture() } }).catch(() => {});
	});
	await page.goto('/home');
	await expect(
		page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('status')
	).toBeVisible();
	const aborted = page.waitForEvent('requestfailed', (r) =>
		r.url().endsWith('/health/daily-message')
	);
	await page.getByRole('link', { name: 'AI 코치에게 물어보기', exact: true }).click();
	await aborted;
	release();
	await page.goto('/home');
	await expect(
		page.getByRole('region', { name: '오늘의 한마디', exact: true }).getByRole('heading')
	).toHaveText(dailyMessageFixture().title);
	expect(calls).toBe(2);
});
