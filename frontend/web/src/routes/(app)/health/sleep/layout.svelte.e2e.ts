import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../../lib/testing/e2e-auth';
import { mockSleepApi, sleepMetricFixture } from '../../../../lib/testing/sleep-api-fixture';
import type { SleepMetricResult } from '../../../../lib/features/health/sleep';

type MissingReason = SleepMetricResult['days'][number]['reason'];
const scenarios: {
	reasons: MissingReason[];
	labels: string[];
	sleepReasons?: MissingReason[];
	sleepLabels?: string[];
}[] = [
	{ reasons: [], labels: [] },
	{ reasons: [null, 'UNSYNCED'], labels: ['2026-10-29 · 동기화 대기'] },
	{
		reasons: [null, 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED'],
		labels: ['2026-10-29부터 2026-11-03까지 · 동기화 대기']
	},
	{
		reasons: [null, 'UNSYNCED', null, 'UNSYNCED'],
		labels: ['2026-10-29 · 동기화 대기', '2026-10-31 · 동기화 대기']
	},
	{
		reasons: [null, 'UNSYNCED', 'UNSYNCED', 'NO_RECORD', 'NULL_VALUE', 'BEFORE_LINK', 'UNSYNCED'],
		labels: [
			'2026-10-29부터 2026-10-30까지 · 동기화 대기',
			'2026-10-31 · 기록 없음',
			'2026-11-01 · 값 없음',
			'2026-11-02 · 연동 전',
			'2026-11-03 · 동기화 대기'
		]
	},
	{
		reasons: [],
		labels: [],
		sleepReasons: [null, 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED'],
		sleepLabels: ['2026-10-29부터 2026-11-03까지 · 동기화 대기']
	},
	{
		reasons: [null, 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED', 'UNSYNCED'],
		labels: ['2026-10-29부터 2026-11-03까지 · 동기화 대기'],
		sleepReasons: [],
		sleepLabels: []
	}
];

for (const width of [1366, 1024, 768, 360]) {
	test(`${width}px 수면 카드 높이는 안내 개수와 무관하며 안내는 카드별로 묶는다`, async ({
		page
	}) => {
		test.setTimeout(60000);
		await mockSignedIn(page);
		await mockSleepApi(page);
		await page.clock.setFixedTime(new Date('2026-11-03T03:00:00Z'));
		await page.setViewportSize({ width, height: 1000 });
		let selected = scenarios[0];
		await page.route('**/api/v1/health/metrics/*?*', (route) => {
			const url = new URL(route.request().url());
			const metric = url.pathname.split('/').at(-1);
			if (metric !== 'sleeps' && metric !== 'oxygen_saturations') return route.fallback();
			const start = url.searchParams.get('start') ?? '';
			const end = url.searchParams.get('end') ?? '';
			const data = sleepMetricFixture(metric, start, end);
			const template = sleepMetricFixture('sleeps', '2026-10-19', '2026-10-25').detail.nights[0];
			// 안내 배치를 검증하려고 reason·평균을 강제 조합한다. 정상 집계 계약 검사는 별도 fixture spec에서 한다.
			data.days = data.days.map((day, index) => {
				const reasons =
					metric === 'sleeps' ? (selected.sleepReasons ?? selected.reasons) : selected.reasons;
				const reason = reasons[index] ?? null;
				return {
					...day,
					reason,
					missing: reason !== null,
					value: reason ? null : metric === 'sleeps' ? 350 : 96
				};
			});
			data.summary.avg = metric === 'sleeps' ? 350 : 96;
			data.summary.daysWithData = data.summary.daysUsedForSummary = data.days.filter(
				(day) => !day.missing
			).length;
			data.detail.nights =
				metric === 'sleeps'
					? data.days
							.filter((day) => !day.missing)
							.map((day) => ({
								...template,
								date: day.date,
								startAt: `${day.date}T00:00:00Z`,
								endAt: `${day.date}T06:00:00Z`,
								sessionMinutes: 360,
								sleepMinutes: 350
							}))
					: [];
			return route.fulfill({ json: { data } });
		});
		let baseline: { summary: number; oxygen: number } | undefined;
		for (const scenario of scenarios) {
			selected = scenario;
			await page.goto('/health/sleep');
			const summary = page.getByRole('region', { name: '수면 기록 요약', exact: true });
			const oxygen = page.getByRole('region', { name: '수면 중 산소포화도', exact: true });
			await expect(summary).toContainText('5시간 50분');
			await expect(oxygen).toContainText('96%');
			await page.evaluate(() => document.fonts.ready);
			const summaryGroup = page.getByRole('group', { name: '수면 기록 요약 및 안내', exact: true });
			const oxygenGroup = page.getByRole('group', { name: '산소포화도 카드 및 안내', exact: true });
			for (const [group, labels] of [
				[summaryGroup, scenario.sleepLabels ?? scenario.labels],
				[oxygenGroup, scenario.labels]
			] as const) {
				await expect(group.getByRole('listitem')).toHaveCount(labels.length);
				for (const [index, label] of labels.entries())
					await expect(group.getByRole('listitem').nth(index)).toHaveAccessibleName(label);
				expect(await group.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
			}
			const a = await summary.boundingBox();
			const b = await oxygen.boundingBox();
			if (!a || !b) throw new Error('카드 위치 없음');
			baseline ??= { summary: a.height, oxygen: b.height };
			expect(a.height).toBeCloseTo(baseline.summary, 0);
			expect(b.height).toBeCloseTo(baseline.oxygen, 0);
			if (width >= 1024) {
				expect(a.y).toBeCloseTo(b.y, 0);
				expect(a.height).toBeCloseTo(b.height, 0);
				expect(b.x).toBeGreaterThanOrEqual(a.x + a.width);
			} else {
				const bounds = await summaryGroup.boundingBox();
				if (!bounds) throw new Error('수면 영역 없음');
				expect(b.y).toBeGreaterThanOrEqual(bounds.y + bounds.height);
			}
			for (const [group, cardBounds, labels] of [
				[summaryGroup, a, scenario.sleepLabels ?? scenario.labels],
				[oxygenGroup, b, scenario.labels]
			] as const) {
				if (labels.length) {
					const listBounds = await group.getByRole('list').boundingBox();
					if (!listBounds) throw new Error('안내 위치 없음');
					expect(listBounds.y).toBeGreaterThanOrEqual(cardBounds.y + cardBounds.height);
				}
			}
			expect(
				await page.evaluate(
					() => document.documentElement.scrollWidth <= document.documentElement.clientWidth
				)
			).toBe(true);
		}
		await page
			.getByRole('group', { name: '수면 기록 요약 및 안내' })
			.locator('..')
			.screenshot({
				path: `test-results/sleep-card-layout-${width}.png`,
				style: 'header, nav { visibility: hidden !important; }'
			});
	});
}
