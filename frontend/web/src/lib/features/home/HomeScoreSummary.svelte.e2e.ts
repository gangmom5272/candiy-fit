import { expect, test, type Locator } from '@playwright/test';
import { mockSignedIn } from '../../testing/e2e-auth';
import { lifestyleScoreFixture, mockHomeApi } from '../../testing/home-api-fixture';

async function expectDescriptionsUnclipped(panel: Locator) {
	const descriptions = panel.locator('li p');
	await expect(descriptions).toHaveCount(3);
	for (const description of await descriptions.all()) {
		expect(
			await description.evaluate((element) => {
				const bounds = element.getBoundingClientRect();
				const range = document.createRange();
				range.selectNodeContents(element);
				return (
					element.scrollWidth <= element.clientWidth + 1 &&
					element.scrollHeight <= element.clientHeight + 1 &&
					[...range.getClientRects()].every(
						(rect) =>
							rect.left >= bounds.left - 1 &&
							rect.right <= bounds.right + 1 &&
							rect.top >= bounds.top - 1 &&
							rect.bottom <= bounds.bottom + 1
					)
				);
			})
		).toBe(true);
	}
}

async function expectOverflowScrollable(panel: Locator) {
	if (await panel.evaluate((element) => element.scrollHeight > element.clientHeight + 1)) {
		expect(await panel.evaluate((element) => getComputedStyle(element).overflowY)).toMatch(
			/^(auto|scroll)$/
		);
		expect(
			await panel.evaluate((element) => {
				element.scrollTop = element.scrollHeight;
				return element.scrollTop > 0;
			})
		).toBe(true);
	}
}

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHomeApi(page);
});

test('요약 카드는 hover로 설명을 보여주고 클릭하면 열린 상태를 유지한다', async ({ page }) => {
	await page.goto('/home');
	const trigger = page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' });
	const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
	await expect(trigger).toHaveAttribute('aria-expanded', 'false');
	await expect(trigger).toHaveText(/활동\s*훌륭\s*수면\s*관심\s*심박\s*안정/);
	await expect(trigger).not.toHaveAttribute('aria-label');
	await expect(trigger).not.toHaveAttribute('aria-labelledby');
	await expect(trigger).toHaveAccessibleName('활동 훌륭 수면 관심 심박 안정');
	await expect(trigger).toHaveAccessibleDescription('오늘의 점수 설명');
	await trigger.hover();
	await expect(panel).toBeVisible();
	await panel.hover();
	await expect(panel).toBeVisible();
	await page.getByRole('heading', { level: 1 }).hover();
	await expect(panel).toHaveCount(0);
	await trigger.click();
	await page.getByRole('heading', { level: 1 }).hover();
	await expect(panel).toBeVisible();
	await expect(panel.getByRole('listitem')).toHaveCount(3);
	await expect(panel).toContainText('85점');
	await expect(panel).toContainText('58점');
	await expect(panel).toContainText('70점');
	await trigger.click();
	await expect(panel).toHaveCount(0);
	await trigger.click();
	await page.getByRole('heading', { level: 1 }).click();
	await expect(panel).toHaveCount(0);
});

test('점수 설명은 지표 카드 값 대신 점수 API의 실제 기록을 사용한다', async ({ page }) => {
	await page.goto('/home');
	const cards = page.getByRole('region', { name: '최근 건강 기록' });
	await expect(cards.getByRole('link', { name: '걸음 수 3,200보', exact: true })).toBeVisible();
	await expect(cards.getByRole('link', { name: '운동 시간 18분', exact: true })).toBeVisible();
	await expect(cards.getByRole('link', { name: '수면 7시간 0분', exact: true })).toBeVisible();
	await page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }).click();
	const rows = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' }).getByRole('listitem');
	const activity = rows.nth(0).locator('p');
	const sleep = rows.nth(1).locator('p');
	const heart = rows.nth(2).locator('p');
	await expect(activity).toContainText('7,200보');
	await expect(activity).toContainText('19분');
	await expect(activity).not.toContainText('3,200보');
	await expect(activity).not.toContainText('18분');
	await expect(sleep).toContainText('6시간 6분');
	await expect(sleep).not.toContainText('7시간 0분');
	await expect(heart).toContainText('64.5 bpm');
	await expect(heart).toContainText('58 bpm');
	await expect(heart).toContainText('6.5 bpm 높');
	for (const description of [activity, sleep, heart])
		await expect(description).not.toContainText(/\d+\s*점|\/\s*\d+|구간|비례|만점/);
});

test('점수와 등급이 같아도 재조회한 측정값에 따라 설명을 갱신한다', async ({ page }) => {
	let updated = false;
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const data = lifestyleScoreFixture();
		if (updated) {
			data.areas.activity.steps.value = 8400;
			data.areas.heart.sleepHrAvg = 64.8;
			data.areas.heart.baselineHrAvg = 58.2;
		}
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	const trigger = page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' });
	const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
	await trigger.click();
	await expect(panel.getByRole('listitem').nth(0).locator('p')).toContainText('7,200보');
	await expect(panel.getByRole('listitem').nth(2).locator('p')).toContainText('64.5 bpm');
	updated = true;
	await page.reload();
	await expect(page.getByLabel('오늘의 점수 100점 만점에 71점')).toBeVisible();
	await trigger.click();
	const rows = panel.getByRole('listitem');
	await expect(rows.nth(0).getByText('85점', { exact: true })).toBeVisible();
	await expect(rows.nth(2).getByText('70점', { exact: true })).toBeVisible();
	await expect(rows.nth(0).locator('p')).toContainText('8,400보');
	await expect(rows.nth(0).locator('p')).not.toContainText('7,200보');
	await expect(rows.nth(2).locator('p')).toContainText('64.8 bpm');
	await expect(rows.nth(2).locator('p')).toContainText('58.2 bpm');
	await expect(rows.nth(2).locator('p')).toContainText('6.6 bpm 높');
	await expect(rows.nth(2).locator('p')).not.toContainText('64.5 bpm');
});

test('360px에서도 실제 0분과 기록 없음·평소 심박 기준 부족을 구분한다', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 800 });
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const data = lifestyleScoreFixture();
		data.total = { score: 0, missingAreas: ['sleep', 'heart'] };
		data.areas.activity = {
			score: 0,
			grade: 4,
			steps: { score: 0, value: 800 },
			exercise: { score: 0, minutes: 0 }
		};
		data.areas.sleep = { score: null, grade: null, sleepMinutes: null };
		data.areas.heart = {
			score: null,
			grade: null,
			sleepHrAvg: 64.5,
			baselineHrAvg: null,
			baselineNights: 0,
			checkWear: false
		};
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	await page.getByRole('button', { name: '활동 관리 수면 기록 없음 심박 기록 없음' }).click();
	const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
	const rows = panel.getByRole('listitem');
	await expect(rows.nth(0).getByText('0점', { exact: true })).toBeVisible();
	await expect(rows.nth(0).locator('p')).toContainText('800보');
	await expect(rows.nth(0).locator('p')).toContainText('0분');
	await expect(rows.nth(0).locator('p')).not.toContainText('기록 없음');
	await expect(rows.nth(1).locator('p')).toContainText(/기록.*확인할 수 없/);
	await expect(rows.nth(1).locator('p')).not.toContainText('0시간 0분');
	await expect(rows.nth(2).locator('p')).toContainText('64.5 bpm');
	await expect(rows.nth(2).locator('p')).toContainText(/평소.*쌓/);
	await expect(rows.nth(2).locator('p')).not.toContainText('0 bpm');
	await expect(rows.nth(2).locator('p')).not.toContainText('착용');
	expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('세 영역의 점수만 있어도 점수와 등급을 유지하고 계산 실패로 설명하지 않는다', async ({
	page
}) => {
	await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
		const data = lifestyleScoreFixture();
		data.areas.activity.steps = { score: null, value: null };
		data.areas.activity.exercise = { score: null, minutes: null };
		data.areas.sleep.sleepMinutes = null;
		data.areas.heart = {
			...data.areas.heart,
			sleepHrAvg: null,
			baselineHrAvg: null,
			baselineNights: null,
			checkWear: null
		};
		return route.fulfill({ json: { data } });
	});
	await page.goto('/home');
	await page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }).click();
	const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
	const rows = panel.getByRole('listitem');
	for (const [index, score] of [85, 58, 70].entries()) {
		await expect(rows.nth(index).getByText(`${score}점`, { exact: true })).toBeVisible();
		const description = rows.nth(index).locator('p');
		await expect(description).toHaveText(/\S/);
		await expect(description).not.toContainText(/계산.*못|점수.*확인할 수 없|계산할 기록.*부족/);
		await expect(description).not.toContainText(/\d+\s*(?:보|분|bpm|점)/);
	}
	await expectDescriptionsUnclipped(panel);
});

test('Enter와 Space로 열고 패널을 탐색한 뒤 Escape와 Tab으로 닫을 수 있다', async ({ page }) => {
	await page.goto('/home');
	const trigger = page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' });
	const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
	await trigger.focus();
	await page.keyboard.press('Enter');
	await expect(panel).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0);
	await expect(trigger).toBeFocused();
	await page.keyboard.press('Space');
	await expect(panel).toBeVisible();
	await page.keyboard.press('Tab');
	await expect(panel).toBeFocused();
	await page.keyboard.press('Tab');
	await expect(panel).toHaveCount(0);
});

for (const width of [360, 768, 1366, 1920]) {
	test(`${width}px에서 세 자리 점수와 단위가 한 줄로 표시된다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 1080 });
		await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
			const data = lifestyleScoreFixture();
			data.total.score = 81;
			data.areas.heart = { ...data.areas.heart, score: 100, grade: 1, sleepHrAvg: 60 };
			return route.fulfill({ json: { data } });
		});
		await page.goto('/home');
		await page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 훌륭' }).click();
		const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
		const score = panel.getByText('100점', { exact: true });
		await expect(score).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		expect(
			await score.evaluate((element) => {
				const range = document.createRange();
				range.selectNodeContents(element);
				return range.getClientRects().length;
			})
		).toBe(1);
		expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
			true
		);
	});
}

for (const viewport of [
	{ width: 360, height: 800 },
	{ width: 1366, height: 768 }
]) {
	for (const scenario of [
		{
			grade: 2,
			label: '안정',
			total: 75,
			sleepMinutes: 390,
			sleepScore: 70,
			sleepDuration: '6시간 30분',
			sleepAdvice: '수면 시간을 조금 더 확보해 보세요.',
			heartAverage: 65,
			heartScore: 70,
			heartDifference: 7,
			heartAdvice: '다음 기록도 가볍게 살펴보세요.'
		},
		{
			grade: 3,
			label: '관심',
			total: 66,
			sleepMinutes: 366,
			sleepScore: 58,
			sleepDuration: '6시간 6분',
			sleepAdvice: '충분히 쉴 수 있도록 수면 시간을 더 확보해 보세요.',
			heartAverage: 65.1,
			heartScore: 55,
			heartDifference: 7.1,
			heartAdvice: '평소보다 높은 기록이 이어지는지 며칠간 살펴보세요.'
		}
	] as const) {
		test(`${viewport.width}px에서 수면·심박 ${scenario.label}의 실측과 등급별 안내를 표시한다`, async ({
			page
		}) => {
			await page.setViewportSize(viewport);
			await page.route('**/api/v1/health/lifestyle-score?*', (route) => {
				const data = lifestyleScoreFixture();
				data.total.score = scenario.total;
				data.areas.sleep = {
					score: scenario.sleepScore,
					grade: scenario.grade,
					sleepMinutes: scenario.sleepMinutes
				};
				data.areas.heart = {
					...data.areas.heart,
					score: scenario.heartScore,
					grade: scenario.grade,
					sleepHrAvg: scenario.heartAverage,
					baselineHrAvg: 58
				};
				return route.fulfill({ json: { data } });
			});
			await page.goto('/home');
			await page.evaluate(() => document.fonts.ready);
			await expect(page.getByLabel(`오늘의 점수 100점 만점에 ${scenario.total}점`)).toBeVisible();
			await page
				.getByRole('button', {
					name: `활동 훌륭 수면 ${scenario.label} 심박 ${scenario.label}`
				})
				.click();
			const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
			const rows = panel.getByRole('listitem');
			const sleep = rows.nth(1);
			const heart = rows.nth(2);
			await expect(sleep.getByText(`수면 · ${scenario.label}`, { exact: true })).toBeVisible();
			await expect(sleep.getByText(`${scenario.sleepScore}점`, { exact: true })).toBeVisible();
			await expect(sleep.locator('p')).toHaveText(
				`${scenario.sleepDuration} 주무셨네요. ${scenario.sleepAdvice}`
			);
			await expect(heart.getByText(`심박 · ${scenario.label}`, { exact: true })).toBeVisible();
			await expect(heart.getByText(`${scenario.heartScore}점`, { exact: true })).toBeVisible();
			await expect(heart.locator('p')).toHaveText(
				`수면 중 평균 심박은 ${scenario.heartAverage} bpm으로, 평소 58 bpm보다 ${scenario.heartDifference} bpm 높아요. ${scenario.heartAdvice}`
			);
			await expectDescriptionsUnclipped(panel);
			await expectOverflowScrollable(panel);
			await rows.last().scrollIntoViewIfNeeded();
			await expect(rows.last()).toBeInViewport();
			expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
				true
			);
			expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
				true
			);
		});
	}
}

test('터치 환경에서는 tap으로 열고 닫는다', async ({ browser, baseURL }) => {
	const context = await browser.newContext({
		baseURL,
		viewport: { width: 360, height: 800 },
		hasTouch: true,
		isMobile: true
	});
	try {
		const page = await context.newPage();
		await mockSignedIn(page);
		await mockHomeApi(page);
		await page.goto('/home');
		await page.evaluate(() => document.fonts.ready);
		const trigger = page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' });
		const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
		await trigger.tap();
		await expect(panel).toBeVisible();
		await expect
			.poll(() =>
				trigger.evaluate((element) => {
					const bounds = element.getBoundingClientRect();
					const hit = document.elementFromPoint(
						bounds.x + bounds.width / 2,
						bounds.y + bounds.height / 2
					);
					return hit !== null && element.contains(hit);
				})
			)
			.toBe(true);
		await trigger.tap();
		await expect(panel).toHaveCount(0);
	} finally {
		await context.close();
	}
});

for (const width of [360, 768, 1366, 1920, 2560]) {
	test(`${width}px에서 긴 설명을 가로 overflow 없이 끝까지 읽을 수 있다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/home', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		await page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }).click();
		const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
		await expect(panel).toBeVisible();
		await panel
			.locator('li p')
			.first()
			.evaluate((element) => {
				element.textContent =
					'긴 설명도 줄바꿈되어야 합니다. '.repeat(20) + 'LongUnbrokenText'.repeat(20);
			});
		const bounds = await panel.boundingBox();
		if (!bounds) throw new Error('설명 패널 위치를 확인할 수 없습니다.');
		expect(bounds.x).toBeGreaterThanOrEqual(0);
		expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
		if (width < 1024) {
			const trigger = await page
				.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' })
				.boundingBox();
			if (!trigger) throw new Error('요약 카드 위치를 확인할 수 없습니다.');
			expect(bounds.y).toBeGreaterThanOrEqual(trigger.y + trigger.height);
		}
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
		expect(await panel.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(
			true
		);
		await expectDescriptionsUnclipped(panel);
		await expectOverflowScrollable(panel);
		const rows = panel.getByRole('listitem');
		for (let index = 0; index < 3; index++) {
			expect(
				await rows.nth(index).evaluate((row) => {
					const description = row.querySelector('p');
					const score = row.querySelector('strong');
					if (!description || !score) return false;
					const textBounds = description.getBoundingClientRect();
					const scoreBounds = score.getBoundingClientRect();
					return textBounds.top >= scoreBounds.bottom || textBounds.left >= scoreBounds.right;
				})
			).toBe(true);
		}
		await rows.last().scrollIntoViewIfNeeded();
		await expect(rows.last()).toBeInViewport();
	});
}

test('연결할 화면이 없는 footer 버튼은 실제 비활성 상태다', async ({ page }) => {
	await page.goto('/home');
	for (const name of ['이용약관', '개인정보처리방침']) {
		await expect(page.getByRole('button', { name, exact: true })).toBeDisabled();
		await expect(page.getByRole('button', { name, exact: true })).not.toHaveCSS(
			'cursor',
			'pointer'
		);
	}
});

for (const viewport of [
	{ width: 360, height: 800 },
	{ width: 768, height: 1024 },
	{ width: 1024, height: 600 },
	{ width: 1280, height: 720 },
	{ width: 1280, height: 600 },
	{ width: 1366, height: 650 },
	{ width: 1366, height: 768 },
	{ width: 1920, height: 1080 },
	{ width: 1920, height: 600 },
	{ width: 2560, height: 1440 },
	{ width: 360, height: 400 },
	{ width: 1280, height: 500 }
]) {
	test(`${viewport.width}×${viewport.height}에서 배지 색상·cursor·필요한 경우만 패널 스크롤을 적용한다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await page.goto('/home', { waitUntil: 'networkidle' });
		await page.evaluate(() => document.fonts.ready);
		const trigger = page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' });
		await expect(trigger).toHaveCSS('cursor', 'pointer');
		await expect(page.getByRole('button', { name: '사용자 메뉴' })).toHaveCSS('cursor', 'pointer');
		await trigger.click();
		const panel = page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });
		await expect(panel.getByText('수면 · 관심', { exact: true })).toHaveCSS(
			'background-color',
			'rgb(255, 242, 215)'
		);
		await expect(panel.getByRole('listitem').nth(1)).toHaveCSS(
			'background-color',
			'rgb(255, 251, 244)'
		);
		if (
			(viewport.width === 360 && viewport.height === 800) ||
			([1280, 1920].includes(viewport.width) && viewport.height === 600)
		)
			await page.screenshot({ path: testInfo.outputPath('score-explanation.png'), fullPage: true });
		await expectDescriptionsUnclipped(panel);
		await expectOverflowScrollable(panel);
		if (viewport.width >= 1280 && viewport.height >= 600) {
			await expect(panel).toHaveCSS('max-height', 'none');
			await expect(panel).toHaveCSS('overflow-y', 'visible');
			// 낮은 화면에서는 기존 페이지 스크롤로 패널 전체에 접근할 수 있어야 한다.
			await panel.scrollIntoViewIfNeeded();
			const bounds = await panel.boundingBox();
			if (!bounds) throw new Error('설명 패널 위치를 확인할 수 없습니다.');
			// 페이지 스크롤의 정수 좌표와 소수점 layout 사이의 반올림 차이만 허용한다.
			expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height + 1);
		}
		const lastRow = panel.getByRole('listitem').last();
		await lastRow.scrollIntoViewIfNeeded();
		await expect(lastRow).toBeInViewport();
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
			true
		);
	});
}
