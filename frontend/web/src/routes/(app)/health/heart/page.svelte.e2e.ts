import { mockDayDetailApi } from '../../../../lib/testing/day-detail-api-fixture';
import { expect, test } from '@playwright/test';
import { mockHeartApi, openCompletedHeartWeek } from '../../../../lib/testing/heart-api-fixture';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';

test.beforeEach(async ({ page }) => {
	await mockSignedIn(page);
	await mockHeartApi(page);
	await mockDayDetailApi(page);
});

for (const viewport of [
	{ width: 1920, height: 1080 },
	{ width: 1366, height: 768 },
	{ width: 1440, height: 900 },
	{ width: 1536, height: 864 }
]) {
	test(`${viewport.width}×${viewport.height}에서 완료·현재·빈 기간의 날짜와 안내를 유지하고 불필요한 넘침이 없다`, async ({
		page
	}, testInfo) => {
		await page.setViewportSize(viewport);
		await openCompletedHeartWeek(page);
		await expect(page.getByRole('link', { name: 'AI 코치로 이동' })).toBeVisible();
		await page.evaluate(() => document.fonts.ready);
		for (const state of ['completed', 'current', 'empty']) {
			if (state === 'current') {
				await page.getByRole('button', { name: '다음 주' }).click();
				await expect(page.locator('[data-heart-point]')).toHaveCount(1);
			}
			if (state === 'empty') {
				await page.getByRole('button', { name: '이전 주' }).click();
				await expect(page.locator('[data-heart-point]')).toHaveCount(6);
				await page.getByRole('button', { name: '이전 주' }).click();
				await expect(
					page.getByRole('region', { name: '일별 심박수' }).getByRole('status')
				).toHaveText('선택한 기간에 심박 기록이 없어요.');
			}

			const geometry = await page.evaluate(() => {
				const main = document.querySelector('.heart-page')!;
				const content = main.querySelector('.heart-content')!;
				const card = content.querySelector('.heart-card')!.getBoundingClientRect();
				const cta = content.lastElementChild!.getBoundingClientRect();
				return {
					scrollHeight: document.documentElement.scrollHeight,
					expectedHeight: Math.max(
						innerHeight,
						Math.ceil(cta.bottom + scrollY + parseFloat(getComputedStyle(main).paddingBottom))
					),
					cardGap: cta.top - card.bottom,
					contentGap: parseFloat(getComputedStyle(content).rowGap),
					scrollWidth: document.documentElement.scrollWidth,
					viewportWidth: innerWidth
				};
			});
			if (state === 'current') {
				const guides = page.getByRole('list', { name: '심박 기록 안내' });
				await expect(guides.getByRole('listitem')).toHaveCount(1);
				const bounds = await guides.boundingBox();
				if (!bounds) throw new Error('결측 안내 영역 없음');
				await page.getByRole('link', { name: 'AI 코치로 이동' }).scrollIntoViewIfNeeded();
			}
			// 문서 길이는 실제 CTA 하단과 페이지 패딩으로 설명되어야 한다.
			expect(Math.abs(geometry.scrollHeight - geometry.expectedHeight), state).toBeLessThanOrEqual(
				1
			);
			expect(geometry.contentGap).toBe(viewport.height <= 900 ? 16 : 24);
			expect(Math.abs(geometry.cardGap - geometry.contentGap)).toBeLessThanOrEqual(1);
			await page.getByRole('link', { name: 'AI 코치로 이동' }).scrollIntoViewIfNeeded();
			expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.viewportWidth);
			await expect(page.getByRole('link', { name: 'AI 코치로 이동' })).toBeInViewport({
				ratio: 1
			});
			const chart = page.getByRole('region', { name: '일별 심박수' });
			for (const date of await chart.getByRole('link').all()) {
				await expect(date).toBeInViewport({ ratio: 1 });
			}
			const axisLabels = await chart.locator('.heart-axis-label').evaluateAll((labels) =>
				labels
					.map((label) => {
						const box = label.getBoundingClientRect();
						return { top: box.top, bottom: box.bottom };
					})
					.sort((a, b) => a.top - b.top)
			);
			expect(axisLabels.every((label, i) => i === 0 || axisLabels[i - 1].bottom <= label.top)).toBe(
				true
			);
			await page.screenshot({
				path: testInfo.outputPath(`heart-fit-${state}.png`),
				fullPage: true
			});
		}
	});
}

test('완료 주·현재 주·빈 기간, 날짜 상세 및 코칭 이동', async ({ page }) => {
	await openCompletedHeartWeek(page);
	const chart = page.getByRole('region', { name: '일별 심박수' });
	const period = page.getByRole('group', { name: '주간 기간 선택' });
	const displayedPeriod = period.getByRole('button', { name: /^날짜 선택,/ });
	await expect(displayedPeriod).toBeVisible();
	await expect(displayedPeriod).toHaveText('2026.10.19 ~ 10.25');
	await expect(chart.getByText('75.68 bpm', { exact: true })).toBeVisible();
	await expect(chart.locator('[data-average-line]')).toHaveCount(0);
	await expect(chart.getByText('평균 75.68', { exact: true })).toHaveCount(0);
	await expect(chart.getByText('주간 평균', { exact: true })).toHaveCount(0);
	await expect(chart.getByRole('link')).toHaveCount(7);
	await expect(chart.locator('[data-heart-point]')).toHaveCount(6);
	await expect(chart.locator('[data-heart-range]')).toHaveCount(6);
	await expect(
		chart.getByRole('link', { name: '2026-10-21 하루 상세' })
	).toHaveAccessibleDescription('심박 기록이 없어요.');
	await expect(
		page
			.getByRole('navigation', { name: '건강 데이터', exact: true })
			.getByRole('link', { name: '심박' })
	).toHaveAttribute('aria-current', 'page');
	await expect(page.getByRole('link', { name: 'AI 코치로 이동' })).toHaveAttribute(
		'href',
		'/coaching'
	);
	await expect(page.getByRole('tooltip')).toHaveCount(0);
	await page.getByRole('button', { name: '다음 주' }).click();
	await expect(displayedPeriod).toBeVisible();
	await expect(displayedPeriod).toHaveText('2026.10.26 ~ 11.01');
	await expect(page.getByRole('button', { name: '다음 주' })).toBeDisabled();
	await expect(chart.getByRole('link')).toHaveCount(7);
	await expect(chart.locator('[data-heart-point]')).toHaveCount(1);
	await expect(chart.locator('[data-heart-range]')).toHaveCount(1);
	await expect(chart.locator('[data-average-line]')).toHaveCount(0);
	await expect(chart.getByText('집계된 평균 없음', { exact: true })).toBeVisible();
	await expect(chart.getByRole('status')).toHaveCount(0);

	await page.getByRole('button', { name: '이전 주' }).click();
	await expect(page.getByRole('button', { name: '이전 주' })).toBeEnabled();
	await chart.getByRole('link', { name: '2026-10-19 하루 상세' }).focus();
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL('/health/day/2026-10-19');
	await page.goBack();
	await expect(page).toHaveURL('/health/heart');
	await expect(displayedPeriod).toHaveText('2026.10.19 ~ 10.25');
	await chart.getByRole('link', { name: '2026-10-19 하루 상세' }).click();
	await expect(page).toHaveURL('/health/day/2026-10-19');
	const back = page.getByRole('link', { name: '건강 데이터로', exact: true });
	await expect(back).toHaveAttribute('href', '/health/heart');
	await back.click();
	await expect(page).toHaveURL('/health/heart');
	await expect(displayedPeriod).toHaveText('2026.10.19 ~ 10.25');
	await page.getByRole('link', { name: 'AI 코치로 이동' }).click();
	await expect(page).toHaveURL('/coaching');
});

for (const width of [1920, 1366, 768, 360]) {
	test(`${width}px에서 overflow와 날짜 정렬 및 가독성`, async ({ page }, testInfo) => {
		await page.setViewportSize({ width, height: 1080 });
		await openCompletedHeartWeek(page);
		await page.evaluate(() => document.fonts.ready);
		const chart = page.getByRole('region', { name: '일별 심박수' });
		await expect(chart.getByRole('link')).toHaveCount(7);
		for (const link of await chart.getByRole('link').all()) {
			await expect(link).toBeVisible();
		}
		const geometry = await chart.evaluate((element) => {
			const box = (node: Element) => node.getBoundingClientRect();
			const links = Array.from(element.querySelectorAll('a'));
			return {
				overflow: document.documentElement.scrollWidth > window.innerWidth,
				labelsOverlap: links.some((link, i) => i > 0 && box(links[i - 1]).right > box(link).left),
				misaligned: Array.from(element.querySelectorAll<HTMLElement>('[data-heart-point]')).some(
					(point) => {
						const date = point.dataset.heartPoint;
						const range = element.querySelector(`[data-heart-range="${date}"]`);
						const link = element.querySelector(`a[aria-label="${date} 하루 상세"]`);
						if (!range || !link) return true;
						const center = (node: Element) => box(node).left + box(node).width / 2;
						return (
							Math.abs(center(point) - center(link)) > 0.1 ||
							Math.abs(center(point) - center(range)) > 0.1
						);
					}
				)
			};
		});
		expect(geometry).toEqual({ overflow: false, labelsOverlap: false, misaligned: false });
		await page.screenshot({ path: testInfo.outputPath(`heart-${width}.png`), fullPage: true });

		const completedMonday = await chart
			.getByRole('link', { name: '2026-10-19 하루 상세' })
			.boundingBox();
		await page.getByRole('button', { name: '다음 주' }).click();
		await expect(page.getByRole('button', { name: '다음 주' })).toBeDisabled();
		await expect(chart.getByRole('link')).toHaveCount(7);
		await expect(chart.locator('[data-heart-point]')).toHaveCount(1);
		await expect(chart.locator('[data-heart-range]')).toHaveCount(1);
		const monday = chart.getByRole('link', { name: '2026-10-26 하루 상세' });
		await expect(monday).toBeVisible();
		const currentMonday = await monday.boundingBox();
		expect(completedMonday).not.toBeNull();
		expect(currentMonday).not.toBeNull();
		if (!completedMonday || !currentMonday) throw new Error('월요일 날짜 링크가 없습니다.');
		expect(
			Math.abs(
				completedMonday.x + completedMonday.width / 2 - currentMonday.x - currentMonday.width / 2
			)
		).toBeLessThan(1);
		const currentGeometry = await chart.evaluate((element) => {
			const point = element.querySelector('[data-heart-point="2026-10-26"]');
			const range = element.querySelector('[data-heart-range="2026-10-26"]');
			const date = element.querySelector('a[aria-label="2026-10-26 하루 상세"]');
			if (!point || !range || !date || !point.parentElement)
				throw new Error('월요일 차트 요소가 없습니다.');
			const plot = point.parentElement.getBoundingClientRect();
			const center = (node: Element) => {
				const box = node.getBoundingClientRect();
				return box.left + box.width / 2;
			};
			const expected = plot.left + plot.width / 14;
			return {
				overflow: document.documentElement.scrollWidth > window.innerWidth,
				inMondaySlot: [point, range, date].every((node) => Math.abs(center(node) - expected) < 0.1)
			};
		});
		expect(currentGeometry).toEqual({ overflow: false, inMondaySlot: true });
		await page.screenshot({
			path: testInfo.outputPath(`heart-current-${width}.png`),
			fullPage: true
		});
	});
}
