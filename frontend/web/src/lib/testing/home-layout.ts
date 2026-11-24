import { expect, type Page } from '@playwright/test';

export const homeLayoutViewports = [
	[1920, 1080],
	[1440, 900],
	[1366, 768],
	[1280, 720],
	[768, 1024],
	[360, 800]
] as const;

/** Read all geometry together after the font metrics have settled. */
export async function measureHomeCardLayout(page: Page, cardSelector: string) {
	await page.evaluate(() => document.fonts.ready);
	return page.evaluate(
		(selector) => ({
			viewport: { width: innerWidth, height: innerHeight },
			scrollHeight: document.documentElement.scrollHeight,
			scrollWidth: document.documentElement.scrollWidth,
			overflow: document.documentElement.scrollWidth > innerWidth,
			cards: [...document.querySelectorAll(selector)].map((card) => {
				const { x, y, width, height } = card.getBoundingClientRect();
				const heading = card.querySelector('h2');
				const group = heading?.parentElement?.getBoundingClientRect();
				const value = card.querySelector('.metric-value')?.getBoundingClientRect();
				const chart = card.querySelector('svg')?.getBoundingClientRect();
				return {
					x,
					y,
					width,
					height,
					overflow: card.scrollWidth > card.clientWidth || card.scrollHeight > card.clientHeight,
					gap: group && value ? value.top - group.bottom : null,
					centerOffset:
						group && value
							? Math.abs(group.left + group.width / 2 - value.left - value.width / 2)
							: null,
					chart: chart && { width: chart.width, height: chart.height }
				};
			})
		}),
		cardSelector
	);
}

export function expectNoHomePageOverflow(
	layout: Awaited<ReturnType<typeof measureHomeCardLayout>>
) {
	expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport.width);
}
