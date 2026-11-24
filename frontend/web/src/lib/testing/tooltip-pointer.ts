import type { Locator, Page } from '@playwright/test';

type Metric = 'steps' | 'exercises' | 'sleeps' | 'heart_rates';
type Bounds = { x: number; y: number; width: number; height: number };

export function healthSummaryChart(page: Page, metric: Metric) {
	const names = {
		steps: '걸음 수 주간 기록',
		exercises: '운동 시간 주간 기록',
		sleeps: '수면 주간 기록',
		heart_rates: '일별 심박수 주간 기록'
	};
	return page.getByRole('figure', { name: names[metric], exact: true });
}

/** 날짜 링크가 아닌 실제 데이터 표시 위로 마우스를 이동한다. */
export async function hoverSummaryPoint(
	page: Page,
	chart: Locator,
	point: Locator,
	metric: Metric
) {
	const index = await point.getAttribute('data-chart-point');
	const visual = chart.locator(
		metric === 'steps' || metric === 'sleeps'
			? `[data-summary-bar="${index}"]`
			: `[data-summary-point="${index}"]`
	);
	await visual.scrollIntoViewIfNeeded();
	const box = await visual.boundingBox();
	if (!box) throw new Error('원본 데이터 표시 없음');
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
}

/** 막대는 보이는 선택 테두리, 선은 데이터 지점의 경계를 검사한다. */
export function tooltipPointerSelection(chart: Locator, point: Locator, metric: Metric) {
	return metric === 'steps' || metric === 'sleeps' ? chart.locator('[data-bar-selection]') : point;
}

export function tooltipBridge(bounds: Bounds, panel: Bounds) {
	const above = panel.y + panel.height <= bounds.y;
	const below = panel.y >= bounds.y + bounds.height;
	return {
		above,
		below,
		x: bounds.x + bounds.width / 2,
		fromY: above ? bounds.y + 1 : bounds.y + bounds.height - 1,
		toY: above ? panel.y + panel.height - 2 : panel.y + 2,
		gapY: above
			? (bounds.y + panel.y + panel.height) / 2
			: (bounds.y + bounds.height + panel.y) / 2,
		outsideX: bounds.x - 0.25
	};
}

export function pointerEdgePositions(box: Bounds, edge: 'left' | 'right' | 'top' | 'bottom') {
	const x = box.x + box.width / 2;
	const y = box.y + box.height / 2;
	const inside: [number, number] =
		edge === 'left'
			? [box.x + 1, y]
			: edge === 'right'
				? [box.x + box.width - 1, y]
				: edge === 'top'
					? [x, box.y + 1]
					: [x, box.y + box.height - 1];
	const outside: [number, number] =
		edge === 'left'
			? [box.x - 0.25, y]
			: edge === 'right'
				? [box.x + box.width + 0.25, y]
				: edge === 'top'
					? [x, box.y - 1]
					: [x, box.y + box.height + 1];
	return { inside, outside };
}
