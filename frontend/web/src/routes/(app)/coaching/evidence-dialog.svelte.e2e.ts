import { expect, test, type Page, type Route } from '@playwright/test';
import {
	evidenceCases,
	kindEvidenceJson,
	overlappingSleepEvidence,
	sleepSpo2Month,
	spo2Month
} from '../../../lib/features/coaching/fixtures';
import { calendarExtremes } from '../../../lib/features/coaching/evidence-view';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { CONVERSATION_ID, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/**
 * 합성 대화 하나와 근거만 돌려준다. 실제 대화·건강데이터가 아니다.
 * beforeTimeline 은 시간대별 걸음 응답 앞에 끼어든다(늦은 응답·실패 검사). 달력이 열리자마자 그 달을 부르기 때문에
 * 창을 연 뒤에 route 를 걸면 늦다. 직접 응답했으면 true 를 돌려준다.
 * secondEvidence 를 주면 같은 대화에 답변을 하나 더 둔다(다른 답변의 근거로 넘어가는 검사). 여는 창은 첫 답변의 것이다
 */
async function openEvidence(
	page: Page,
	evidence: unknown[] = evidenceCases.month,
	beforeTimeline?: (route: Route) => Promise<boolean> | boolean,
	secondEvidence?: unknown[]
) {
	await mockSignedIn(page);
	await mockCoachingServer(page, () => {});
	const now = '2026-10-28T05:00:00Z';
	const conversation = {
		id: CONVERSATION_ID,
		title: '이번 달 걸음 수 알려줘',
		summary: null,
		createdAt: now,
		updatedAt: now
	};
	const message = {
		requestId: '33333333-3333-4333-8333-333333333333',
		requestStatus: 'completed',
		errorCode: null,
		createdAt: now,
		errorContext: null
	};
	await page.route('**/api/v1/coaching/conversations**', (route) =>
		route.request().url().includes('/messages')
			? route.fulfill({
					json: {
						data: {
							conversation,
							items: [
								{
									...message,
									id: 'u1',
									role: 'user',
									sequenceNo: 1,
									content: conversation.title,
									evidence: []
								},
								{
									...message,
									id: 'a1',
									role: 'assistant',
									sequenceNo: 2,
									content: '정리했어요.',
									evidence
								},
								...(secondEvidence
									? [
											{
												...message,
												id: 'u2',
												role: 'user',
												sequenceNo: 3,
												content: '다른 기록도 알려줘',
												evidence: []
											},
											{
												...message,
												id: 'a2',
												role: 'assistant',
												sequenceNo: 4,
												content: '이어서 정리했어요.',
												evidence: secondEvidence
											}
										]
									: [])
							],
							nextBeforeSequenceNo: null
						}
					}
				})
			: route.fulfill({
					json: {
						data: { items: [{ ...conversation, lastRequestStatus: 'completed' }], nextCursor: null }
					}
				})
	);
	// 활동 달력의 시간대별 걸음(S15P31A403-438). 합성 값: 0~6시 0, 낮에는 날짜마다 다른 값, 18시가 가장 많다.
	// 2026-10-10 은 시간대 기록이 없는 날이다. 달 단위로 부르므로(S15P31A403-439) start~end 의 모든 날을 돌려준다
	await page.route('**/api/v1/health/timeline**', async (route) => {
		if (await beforeTimeline?.(route)) return;
		const params = new URL(route.request().url()).searchParams;
		const days = [];
		const last = Date.parse(`${params.get('end')}T00:00:00Z`);
		for (
			let time = Date.parse(`${params.get('start')}T00:00:00Z`);
			time <= last;
			time += 86400000
		) {
			const date = new Date(time).toISOString().slice(0, 10);
			const day = Number(date.slice(8));
			days.push({
				date,
				hourlySteps:
					date === '2026-10-10'
						? null
						: Array.from({ length: 24 }, (_, hour) =>
								hour < 7 ? 0 : hour === 18 ? 1500 + day * 10 : 200 + ((hour * 37 + day * 11) % 600)
							)
			});
		}
		return route.fulfill({ json: { data: { days } } });
	});
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	await page
		.getByRole('region', { name: '이 답변에서 사용한 기록' })
		.first()
		.getByRole('button')
		.click();
	const dialog = page.getByRole('dialog', { name: '참조 데이터 상세' });
	await expect(dialog).toBeVisible();
	return dialog;
}

for (const [device, viewport] of [
	['데스크톱', { width: 1440, height: 900 }],
	['모바일', { width: 375, height: 812 }]
] as const) {
	test(`${device}: 겹치는 수면 근거도 차트 열기·닫기·재조회가 정상이다`, async ({ page }) => {
		const errors: string[] = [];
		page.on('pageerror', (error) => errors.push(error.message));
		await page.setViewportSize(viewport);
		const dialog = await openEvidence(page, overlappingSleepEvidence);
		await expect(dialog).toContainText('42일 기간');
		await expect(dialog).toContainText('42일 평균이에요.');
		// 6주 전체에 통째로 겹치는 지난주·이번 주는 기간 목록에 섞지 않고 머리말에 따로 적는다(S15P31A403-418)
		await expect(dialog.locator('[data-evidence-detail="parts"]')).toHaveCount(0);
		await expect(dialog.locator('[data-evidence-overlap]')).toHaveCount(2);
		// 날짜별 산소포화도는 따로 두지 않고 날짜별 기록 달력에 넣는다(S15P31A403-430)
		await expect(dialog.locator('[data-evidence-detail="spo2-low"]')).toHaveCount(0);
		await expect(dialog.locator('[data-day-calendar]')).toBeVisible();
		await dialog.getByRole('button', { name: '닫기' }).first().click();
		await page.reload();
		await page.getByRole('region', { name: '이 답변에서 사용한 기록' }).getByRole('button').click();
		await expect(dialog).toBeVisible();
		await expect(dialog.locator('[data-day-calendar]')).toBeVisible();
		expect(errors).toEqual([]);
	});

	test(`${device}: 근거 창은 스크롤바 없이 위 막대가 내린 만큼 왼쪽부터 차오른다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		const dialog = await openEvidence(page);
		const bar = dialog.locator('[data-scroll-progress]');
		const fill = bar.locator('div');
		const fillRatio = async () => {
			const [track, filled] = await Promise.all([bar.boundingBox(), fill.boundingBox()]);
			return (filled?.width ?? 0) / (track?.width ?? 1);
		};
		const scrollTo = (ratio: number) =>
			dialog.evaluate((element, value) => {
				element.scrollTop = (element.scrollHeight - element.clientHeight) * value;
			}, ratio);

		// 창 안 오른쪽 스크롤바가 자리를 차지하지 않는다
		expect(
			await dialog.evaluate((element: HTMLElement) => element.offsetWidth - element.clientWidth)
		).toBe(0);
		await expect(bar).toBeVisible();
		await expect(bar).toHaveAttribute('aria-hidden', 'true');
		expect(await fillRatio()).toBe(0);

		await scrollTo(0.5);
		await expect.poll(fillRatio).toBeCloseTo(0.5, 1);
		// 막대는 맨 위에 붙어 있다
		const [dialogBox, barBox] = await Promise.all([dialog.boundingBox(), bar.boundingBox()]);
		expect(Math.abs((barBox?.y ?? 0) - (dialogBox?.y ?? 0))).toBeLessThanOrEqual(1);

		await scrollTo(1);
		await expect.poll(fillRatio).toBeCloseTo(1, 2);
	});
}

test('다시 열면 맨 위에서 빈 막대로 시작하고, 내릴 내용이 없으면 막대를 두지 않는다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const dialog = await openEvidence(page);
	await dialog.evaluate((element) => (element.scrollTop = element.scrollHeight));
	await dialog.getByRole('button', { name: '닫기' }).first().click();
	await page.getByRole('region', { name: '이 답변에서 사용한 기록' }).getByRole('button').click();
	expect(await dialog.evaluate((element) => element.scrollTop)).toBe(0);

	await page.setViewportSize({ width: 1440, height: 2400 });
	await expect(dialog.locator('[data-scroll-progress]')).toHaveCount(0);
});

// 248: 답변이 말한 측정 최고·수면 단계·운동 기록을 근거 창에서 찾을 수 있고, 휴대폰 폭에서도 넘치지 않는다
for (const [name, evidence, section, texts] of [
	[
		'심박 측정 최저·최고',
		evidenceCases.heartOneDay,
		'measured',
		['측정 최고 · 10.27(화)', '138bpm']
	],
	[
		'수면 단계 평균',
		evidenceCases.sleepStagesWeek,
		'sleep-stages',
		['깊은 잠', '1시간 16분', '7일 중 6일의 평균이에요.']
	],
	[
		'운동 횟수·칼로리·거리',
		evidenceCases.exerciseActivityWeek,
		'exercise-activity',
		['이 기간 합계', '4회', '하루 평균 0.8회', '1,170kcal', '12.5km', '칼로리 수집에 실패']
	],
	// 388: 산소포화도는 한 상자에 평균·최저·최고를 나란히 두고 최저·최고의 날짜를 값 아래에 쓴다
	[
		'산소포화도 요약 한 상자',
		spo2Month,
		'spo2-summary',
		['산소포화도', '평균', '최저', '88.0%', '10.07(수)', '최고', '99.5%', '10.19(월)']
	]
] as const) {
	test(`근거 창 상세: ${name}`, async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 812 });
		const dialog = await openEvidence(page, evidence);
		const detail = dialog.locator(`[data-evidence-detail="${section}"]`);
		for (const text of texts) await expect(detail).toContainText(text);
		expect(
			await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
		).toBe(0);
	});
}

// 262: 요일로 묻는 답변("지지난주 금요일")을 근거에서 맞춰 볼 수 있게 하루 단위 날짜에 요일을 붙인다
test('근거 창 날짜에 요일이 붙고, 휴대폰 폭에서 날짜 칸이 넘치지 않는다', async ({ page }) => {
	await page.setViewportSize({ width: 320, height: 720 });
	const dialog = await openEvidence(page, evidenceCases.month);
	// 막대 이름(툴팁·보조기기)과 달력 날짜 이름에 요일이 붙는다. 가장 많이 걸은 날은 달력 칸 이름에 붙는다(S15P31A403-435)
	await expect(dialog.locator('figure [data-bar-title^="10.01(목) "]')).toHaveCount(1);
	await expect(
		dialog.getByRole('button', { name: /^10월 \d+일 [일월화수목금토]요일, .*가장 많이 걸은 날$/ })
	).toHaveCount(1);
	// 긴 기간의 날짜별 기록은 달력이다(S15P31A403-430). 날짜 이름에 요일이 붙고, 오늘은 수집 중이라고 읽힌다
	await expect(dialog.getByText('날짜별 전체 보기 (31일)')).toHaveCount(0);
	await expect(dialog.getByRole('button', { name: /^10월 1일 목요일, / })).toBeVisible();
	await expect(dialog.getByRole('button', { name: /^10월 31일 토요일, .*수집 중$/ })).toBeVisible();
	expect(
		await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
	).toBe(0);
});

// 278: "수집 중"이 날짜 뒤에 붙어 좁은 칸에서 날짜가 두 줄이 되면 그 칸의 값만 아래로 밀렸다.
// 걸음 일주일은 이제 달력으로 보여서(S15P31A403-439), 날짜 목록이 남는 신체 지표(체중) 일주일로 본다
for (const viewport of [
	{ width: 1440, height: 900 },
	{ width: 768, height: 1024 },
	{ width: 375, height: 812 }
]) {
	test(`날짜별 기록은 오늘(수집 중) 칸도 날짜가 한 줄이고 값 위치가 같다 (${viewport.width}px)`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		const dialog = await openEvidence(page, evidenceCases.bodyWeek);
		const days = dialog.locator('[data-day-list] > li');
		await expect(days).toHaveCount(7);
		await expect(days.last()).toContainText('수집 중');
		const cells = await days.evaluateAll((items) =>
			items.map((item) => {
				const [date, value] = Array.from(item.children) as HTMLElement[];
				return {
					row: Math.round(item.getBoundingClientRect().top),
					dateHeight: date.getBoundingClientRect().height,
					lineHeight: parseFloat(getComputedStyle(date).lineHeight),
					valueTop: Math.round(value.getBoundingClientRect().top)
				};
			})
		);
		for (const cell of cells) {
			expect(cell.dateHeight).toBeLessThan(cell.lineHeight * 1.5);
			// 같은 줄에 놓인 칸끼리는 값 위치가 같다
			const sameRow = cells.filter((other) => other.row === cell.row);
			expect(new Set(sameRow.map((other) => other.valueTop)).size).toBe(1);
		}
	});
}

/**
 * S15P31A403-312: 서버가 보내는 근거 JSON 그대로(수면 세션·시간대 걸음 + 아직 모르는 운동 구간)를 받아도
 * 대화가 열리고, 아는 종류만 탭으로 보인다. 360px 에서 시간대 표가 창을 넘지 않는다
 */
for (const width of [360, 1366]) {
	test(`${width}px 모양이 다른 근거: 아는 종류는 탭으로, 모르는 종류는 숨긴다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		const dialog = await openEvidence(page, [
			kindEvidenceJson.exerciseIntervals,
			kindEvidenceJson.sleepSessions,
			kindEvidenceJson.hourlySteps
		]);
		const tabs = dialog.getByRole('tab');
		await expect(tabs).toHaveText(['수면 기록', '시간대 걸음']);
		await expect(dialog.getByText('11월 4일 밤', { exact: true })).toBeVisible();
		await expect(dialog.getByText('주 수면 2회 · 추가 수면 1회')).toBeVisible();
		await expect(dialog.getByText('11월 5일 낮', { exact: false })).toContainText('추가 수면');
		await expect(dialog.getByText('2회 (시각 정보 없음)', { exact: false })).toBeVisible();
		await expect(dialog.getByText('측정 없음', { exact: false })).toBeVisible();

		await tabs.nth(1).click();
		const table = dialog.getByRole('table', { name: '날짜별 시간대 걸음 수' });
		await expect(table).toBeVisible();
		await expect(table.getByRole('columnheader')).toHaveText([
			'날짜',
			'6시',
			'7시',
			'8시',
			'9시',
			'합계'
		]);
		const overflow = await dialog.evaluate((element) => element.scrollWidth - element.clientWidth);
		expect(overflow).toBeLessThanOrEqual(1);
	});
}

test('360px 24시간 시간대 표는 표 안에서만 가로로 밀리고 키보드로도 민다', async ({ page }) => {
	await page.setViewportSize({ width: 360, height: 800 });
	const dialog = await openEvidence(page, [kindEvidenceJson.hourlyStepsFullDay]);
	const table = dialog.getByRole('table', { name: '날짜별 시간대 걸음 수' });
	await expect(table.getByRole('columnheader')).toHaveCount(26);
	await expect(dialog.getByText('기록 없음', { exact: true })).toBeVisible();
	const region = dialog.getByRole('region', { name: '시간대 걸음 표' });
	const sizes = await region.evaluate((element) => ({
		inner: element.scrollWidth,
		outer: element.clientWidth
	}));
	expect(sizes.inner).toBeGreaterThan(sizes.outer);
	expect(
		await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)
	).toBeLessThanOrEqual(1);
	await region.focus();
	await page.keyboard.press('ArrowRight');
	await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
});

test('지표·근거 종류 탭을 방향키로 오가고, 산소포화도 시각은 펼치면 전체가 한 번만 보인다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, [
		...evidenceCases.sleepStagesWeek,
		kindEvidenceJson.sleepSessions,
		kindEvidenceJson.hourlySteps
	]);
	const tabs = dialog.getByRole('tab');
	await expect(tabs).toHaveText(['수면', '수면 기록', '시간대 걸음']);
	await tabs.first().focus();
	await page.keyboard.press('ArrowRight');
	await expect(tabs.nth(1)).toBeFocused();
	await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
	await page.keyboard.press('ArrowLeft');
	await page.keyboard.press('ArrowLeft');
	await expect(tabs.nth(2)).toHaveAttribute('aria-selected', 'true');
	await tabs.nth(1).click();

	const summary = dialog.getByText('외 2회 보기');
	await expect(summary).toBeVisible();
	await summary.click();
	await expect(
		dialog.getByText('02:02, 02:47, 03:15, 04:31, 05:10', { exact: true })
	).toBeVisible();
	await expect(dialog.getByText('외 2회 보기')).toBeHidden();
	await expect(dialog.getByText('접기')).toBeVisible();
});

test('수면 세션이 없으면 안내 한 줄만 보이고 대화는 그대로 열린다', async ({ page }) => {
	const dialog = await openEvidence(page, [kindEvidenceJson.sleepSessionsEmpty]);
	await expect(dialog.getByText('이 기간에는 수면 기록이 없어요.')).toBeVisible();
	await expect(dialog.getByRole('tab')).toHaveCount(0);
});

test('시간대 걸음 기간 합계: 기록 없는 날이 있으면 합계 대신 이유를, 다 있으면 오늘 포함 합계를 보여 준다', async ({
	page
}) => {
	const dialog = await openEvidence(page, [kindEvidenceJson.hourlySteps]);
	const sum = dialog.locator('dl').filter({ hasText: '걸음 합계' });
	await expect(sum.locator('dd')).toHaveText('-');
	await expect(dialog.getByText('기록 없는 날이 있어 합계를 내지 않았어요.')).toBeVisible();
	await expect(dialog.getByText('오늘 기록은 수집 중이에요.')).toBeVisible();

	await page.keyboard.press('Escape');
	const complete = await openEvidence(page, [kindEvidenceJson.hourlyStepsComplete]);
	await expect(complete.locator('dl').filter({ hasText: '걸음 합계' }).locator('dd')).toHaveText(
		'3,400걸음'
	);
	await expect(complete.getByText('기록 없는 날이 있어 합계를 내지 않았어요.')).toHaveCount(0);
	await expect(
		complete.getByText('오늘 기록은 수집 중이며, 지금까지 걸음도 합계에 들어가요.')
	).toBeVisible();
});

test('근거 창을 열면 첫 포커스가 제목에 있어 닫기 버튼에 테두리가 먼저 뜨지 않고, Tab 을 누르면 닫기 버튼으로 간다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 800 });
	const dialog = await openEvidence(page);
	const title = dialog.getByRole('heading', { level: 2, name: '참조 데이터 상세' });
	// 창에는 닫기가 둘 있다(위의 X, 아래의 닫기 버튼). 첫 번째가 X다
	const close = dialog.getByRole('button', { name: '닫기' }).first();

	// 마우스로 열어도 프로그램이 준 포커스는 테두리가 보인다. 누르는 요소가 아닌 제목에 두고 테두리는 숨긴다
	await expect(title).toBeFocused();
	await expect(title).toHaveCSS('outline-style', 'none');
	await expect(close).toHaveCSS('outline-style', 'none');

	await page.keyboard.press('Tab');
	await expect(close).toBeFocused();
	await expect(close).toHaveCSS('outline-style', 'solid');
});

for (const width of [320, 360]) {
	test(`${width}px에서 글자를 200%로 키워도 근거 요약의 기간이 카드 밖으로 나가지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		const dialog = await openEvidence(page);
		await dialog.getByRole('button', { name: '닫기' }).first().click();
		const card = page.getByRole('region', { name: '이 답변에서 사용한 기록' });

		// Tailwind 글자 크기는 rem 이라 루트 글자 크기를 키우면 브라우저 글자 확대와 같다
		await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
		const overflow = await card.evaluate((element) => {
			const box = element.getBoundingClientRect();
			const lines = [...element.querySelectorAll('p')].map((line) => line.getBoundingClientRect());
			return {
				card: element.scrollWidth - element.clientWidth,
				textRight: Math.max(...lines.map((line) => line.right)) - box.right
			};
		});
		expect(overflow.card).toBeLessThanOrEqual(1);
		expect(overflow.textRight).toBeLessThanOrEqual(0);
	});
}

// 388: 앱(384)과 같은 구성. 산소포화도 차트는 90에서 시작하는 꺾은선이고 휴대폰 폭에서만 가로로 밀린다
for (const [device, viewport] of [
	['모바일', { width: 375, height: 812 }],
	['데스크톱', { width: 1440, height: 900 }]
] as const) {
	test(`${device}: 산소포화도는 꺾은선 차트이고 겹치는 묶음을 두지 않는다`, async ({ page }) => {
		await page.setViewportSize(viewport);
		const dialog = await openEvidence(page, spo2Month);

		// 기록이 있는 28일의 점. 막대는 없다
		await expect(dialog.locator('[data-line-point]')).toHaveCount(28);
		await expect(dialog.getByRole('figure', { name: '일별 산소포화도 꺾은선 차트' })).toBeVisible();
		// 축 눈금은 90·95·100 이다(0·25·50·75·100 이 아니다). 측정 최저 88은 요약에만 있고 날짜별 값은 모두 90 이상이다
		await expect(dialog.locator('figure')).toContainText('9095100');

		const scroller = dialog.locator('[data-line-chart-scroll]');
		const measure = () =>
			scroller.evaluate((element: HTMLElement) => ({
				overflow: element.scrollWidth - element.clientWidth,
				left: element.scrollLeft
			}));
		// 일별 차트 공통 규칙(S15P31A403-418): 어느 폭에서나 한 화면에 일주일만 보이고, 처음에는 가장 최근 날짜(오른쪽 끝)가 보인다
		const size = await measure();
		expect(size.overflow).toBeGreaterThan(0);
		expect(size.left).toBeGreaterThan(size.overflow - 4);
		await expect(dialog.locator('[data-range-bar]')).toBeVisible();
		// 기간 전체 보기로 바꾸면 넘기지 않고 한 화면에 다 그린다(좁은 폭에서는 가장자리 축 글자가 칸보다 넓어 몇 px 가 재어진다)
		await dialog.getByRole('button', { name: '한 달', exact: true }).click();
		await expect.poll(async () => (await measure()).overflow).toBeLessThan(20);
		await expect(dialog.locator('[data-range-bar]')).toHaveCount(0);

		// 위 요약과 겹치는 묶음은 없다: 따로 있던 측정 최저·최고, 가장 높은 날·낮은 날
		await expect(dialog.locator('[data-evidence-detail="measured"]')).toHaveCount(0);
		await expect(dialog.getByText(/가장 높은 날/)).toBeHidden();
		await expect(dialog.getByText('날짜별 전체 보기 (30일)')).toBeVisible();
		// 창 전체는 가로로 넘치지 않는다(차트만 밀린다)
		expect(
			await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
		).toBe(0);
	});
}

// S15P31A403-430 운동 기록·수면 중 산소포화도는 따로 두지 않고 날짜별 기록 달력의 그날 상세에 넣는다
test('운동 횟수·칼로리·거리 표는 따로 두지 않고, 그날 값은 달력 상세에 이유까지 보여 준다', async ({
	page
}) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, evidenceCases.exerciseActivityWeek);
	await expect(dialog.getByRole('region', { name: '날짜별 운동 횟수·칼로리·거리 표' })).toHaveCount(
		0
	);
	await expect(dialog.locator('[data-evidence-detail="exercise-activity"]')).toHaveCount(1);
	const calendar = dialog.locator('[data-day-calendar]');
	const day = calendar.locator('[data-calendar-activity]');
	await calendar.locator('[data-calendar-day="2026-10-24"]').click();
	await expect(day).toContainText('2회');
	await expect(day).toContainText('410kcal');
	await calendar.locator('[data-calendar-day="2026-10-25"]').click();
	await expect(day).toContainText('기록 없음');
	await calendar.locator('[data-calendar-day="2026-10-28"]').click();
	await expect(calendar.locator('[data-calendar-detail]')).toContainText('수집 중');
	// 휴대폰 폭에서 기간 합계의 보조 글은 한 줄로 이어 쓴다
	await expect(dialog.locator('[data-evidence-detail="exercise-activity"]')).toContainText(
		/하루 평균 0\.8회\s*·\s*기록 5일/
	);
	expect(
		await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
	).toBe(0);
});

test('운동 탭은 운동 기록 묶음 대신 달력 상세에 그날 운동 기록과 표시를 보여 준다', async ({
	page
}) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, evidenceCases.exerciseSessionsMonth);
	await expect(dialog.locator('[data-evidence-detail="sessions"]')).toHaveCount(0);
	const calendar = dialog.locator('[data-day-calendar]');
	const detail = calendar.locator('[data-calendar-detail]');
	await calendar.locator('[data-calendar-day="2026-10-23"]').click();
	await expect(detail).toContainText('19:00');
	await expect(detail).toContainText('가장 길게');
	await calendar.locator('[data-calendar-day="2026-10-15"]').click();
	await expect(detail).toContainText('운동 미인정');
	await expect(detail).toContainText('운동으로 인정되지 않은 기록은 운동 시간에서 뺐어요.');
	expect(
		await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
	).toBe(0);
});

test('수면 탭은 산소포화도 묶음 대신 달력 상세에 그 밤의 평균·최저·최고·90% 미만을 보여 준다', async ({
	page
}) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, sleepSpo2Month);
	await expect(dialog.locator('[data-evidence-detail="spo2-low"]')).toHaveCount(0);
	const calendar = dialog.locator('[data-day-calendar]');
	const detail = calendar.locator('[data-calendar-detail]');
	const low = calendar
		.locator('[data-calendar-day]', { has: page.locator('[data-spo2-low-dot]') })
		.first();
	await low.click();
	await expect(detail).toContainText('수면 중 평균 산소포화도');
	await expect(detail).toContainText('최저 · 최고');
	await expect(detail).toContainText(/90% 미만\s*\S/);
	await calendar.getByRole('button', { name: '달력 보는 법' }).click();
	await expect(page.getByRole('dialog', { name: '한 바퀴가 하루 평균이에요' })).toContainText(
		'빨간 점은 수면 중 산소포화도가 90% 미만으로 내려간 밤이에요.'
	);
});

// 걸음도 짧은 기간부터 달력으로 보여 준다(S15P31A403-439). 438에서 시간대별 걸음이 달력 상세에 들어가,
// 14일 이하 걸음 근거에서는 그 값을 볼 길이 없었다
test('일주일 걸음·운동·수면·심박 모두 달력으로 보여 주고, 걸음은 그날 시간대별 걸음도 보여 준다', async ({
	page
}) => {
	const sleep = await openEvidence(page, evidenceCases.sleepStagesWeek);
	await expect(sleep.locator('[data-day-calendar]')).toBeVisible();
	const heart = await openEvidence(page, evidenceCases.heartWeek);
	await expect(heart.locator('[data-day-calendar]')).toBeVisible();
	const exercise = await openEvidence(page, evidenceCases.exerciseActivityWeek);
	await expect(exercise.locator('[data-day-calendar]')).toBeVisible();
	const steps = await openEvidence(page, evidenceCases.weekWithToday);
	const calendar = steps.locator('[data-day-calendar]');
	await expect(calendar).toBeVisible();
	// 날짜를 모두 나열하던 목록은 두지 않는다
	await expect(steps.locator('[data-day-list]')).toHaveCount(0);
	// 일주일 모두 달력 칸으로 있고, 수집 중인 오늘도 누를 수 있다
	await expect(calendar.locator('[data-calendar-day]:not([disabled])')).toHaveCount(7);
	await expect(steps.getByRole('button', { name: /^10월 28일 수요일, .*수집 중$/ })).toBeVisible();
	// 날짜를 누르면 그날 시간대별 걸음이 나온다
	await calendar.locator('[data-calendar-day="2026-10-25"]').click();
	const hourly = calendar.locator('[data-hourly-steps]');
	await expect(hourly).toContainText('가장 많이 걸은 시간 18시 · 1,750걸음');
	await expect(hourly.locator('[data-hour]')).toHaveCount(24);
});

// 달력을 쓰는 네 지표라도 원을 그릴 기준이 없으면 달력을 두지 않는다. 하루 기간, 운동한 날이 없는 기간, 지난 기록 없이 오늘 값만 있는 기간이다
test('하루 기간이거나 견줄 기록이 없으면 달력 대신 값과 날짜 목록을 보여 준다', async ({
	page
}) => {
	for (const evidence of [
		evidenceCases.singleDay,
		evidenceCases.todayOnly,
		evidenceCases.todayOnlyWeek,
		evidenceCases.allZeroWeek,
		evidenceCases.noRecords
	]) {
		const dialog = await openEvidence(page, evidence);
		await expect(dialog.locator('[data-day-calendar]')).toHaveCount(0);
	}
});

test('심박 달력 상세는 그날 하루 평균과 실제 측정 최저·최고를 보여 준다', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, evidenceCases.heartWeekMeasured);
	const calendar = dialog.locator('[data-day-calendar]');
	const detail = calendar.locator('[data-calendar-detail]');
	await calendar.locator('[data-calendar-day="2026-10-26"]').click();
	await expect(detail).toContainText('하루 평균');
	const range = detail.locator('[data-calendar-pair="heart-range"]');
	await expect(range).toContainText('측정 최저');
	await expect(range).toContainText('60bpm');
	await expect(range).toContainText('141bpm');
	// 그날 측정이 없으면 두 칸을 두지 않는다
	await calendar.locator('[data-calendar-day="2026-10-28"]').click();
	await expect(range).toHaveCount(0);
	expect(
		await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
	).toBe(0);
});

test('수면 달력 상세는 그 밤에 잠든 시각과 깬 시각을 보여 준다', async ({ page }) => {
	const dialog = await openEvidence(page, evidenceCases.sleepStagesWeek);
	const calendar = dialog.locator('[data-day-calendar]');
	await calendar.locator('[data-calendar-day]').first().click();
	const time = calendar.locator('[data-calendar-pair="sleep-time"]');
	await expect(time).toContainText('잠든 시각');
	await expect(time).toContainText('깬 시각');
	await expect(time).toContainText(/\d{2}:\d{2}[\s\S]*\d{2}:\d{2}/);
});

test('근거 창 맨 아래에 동기화 안내를 두지 않는다', async ({ page }) => {
	const dialog = await openEvidence(page, evidenceCases.sleepStagesWeek);
	await expect(dialog).not.toContainText('아직 동기화되지 않은 날이 있어요');
});

test('날짜별 수면 단계의 구간에 마우스를 올리면 그 단계의 시간이 뜨고, Esc 는 팝업만 닫는다(S15P31A403-418)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.sleepStagesWeek);
	const section = dialog.locator('[data-evidence-detail="sleep-stage-days"]');
	// 묶음 제목은 옆 묶음과 같은 크기다
	await expect(section.getByRole('heading', { name: '날짜별 수면 단계' })).toHaveCSS(
		'font-size',
		'16px'
	);
	await expect(
		dialog.locator('[data-evidence-detail="sleep-stages"]').getByRole('heading')
	).toHaveCSS('font-size', '16px');

	const popup = section.locator('[data-stage-popup]');
	await expect(popup).toHaveCount(0);
	const deep = section.getByRole('button', { name: '10.20(화) 깊은 잠 1시간 18분' });
	const title = dialog.getByRole('heading', { name: '참조 데이터 상세' });
	await deep.hover();
	await expect(popup).toContainText('깊은 잠');
	await expect(popup).toContainText('1시간 18분');
	await expect(popup).toContainText('10.20(화)');

	// 마우스가 떠나면 닫힌다. 눌렀다가 떠나도 남지 않는다
	await title.hover();
	await expect(popup).toHaveCount(0);
	await deep.click();
	await expect(popup).toHaveCount(1);
	await title.hover();
	await expect(popup).toHaveCount(0);

	// 키보드로는 방향키로 옮겨 간 구간의 시간이 뜬다. 좌우는 옆 날의 같은 단계, 위아래는 같은 날의 다른 단계다
	await deep.focus();
	await page.keyboard.press('ArrowRight');
	await expect(popup).toContainText('10.21(수)');
	await expect(popup).toContainText('1시간 10분');
	await page.keyboard.press('ArrowUp');
	await expect(popup).toContainText('렘수면');

	// Esc 는 팝업만 닫고 근거 창은 그대로 둔다
	await page.keyboard.press('Escape');
	await expect(popup).toHaveCount(0);
	await expect(dialog).toBeVisible();

	// 차트 안의 Tab 자리는 하나뿐이다
	await expect(section.locator('button[tabindex="0"]')).toHaveCount(1);
	// 7일은 한 화면에 다 보이므로 넘겨 보는 막대가 없다
	await expect(section.locator('[data-range-bar]')).toHaveCount(0);
});

test('한 달의 날짜별 수면 단계는 최근 7일부터 보이고 가로로 넘겨 본다(S15P31A403-418)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.sleepSpo2MonthClean);
	const section = dialog.locator('[data-evidence-detail="sleep-stage-days"]');
	await section.scrollIntoViewIfNeeded();
	const day = (date: string) => section.locator(`[data-stage-day="${date}"]`);
	const scroller = section.locator('[data-range-scroller]');
	const handle = section.locator('[data-range-handle]');
	const handleLeft = async () => (await handle.boundingBox())!.x;

	// 처음에는 가장 최근 7일이다
	await expect(day('2026-10-30')).toBeInViewport();
	await expect(day('2026-10-24')).toBeInViewport();
	await expect(day('2026-10-23')).not.toBeInViewport({ ratio: 0.1 });
	// 7칸뿐이라 모든 날짜를 적는다
	await expect(day('2026-10-27')).toContainText('10.27');
	// 일주일씩 건너뛰는 날짜 버튼은 두지 않는다. 사용자가 직접 넘긴다
	await expect(section.getByRole('button', { name: /7일/ })).toHaveCount(0);

	// 차트를 마우스로 잡고 끌면 앞의 날이 나오고, 아래 막대의 손잡이가 따라 움직인다
	const before = await handleLeft();
	const area = (await scroller.boundingBox())!;
	await page.mouse.move(area.x + 60, area.y + 40);
	await page.mouse.down();
	await page.mouse.move(area.x + 60 + area.width * 0.5, area.y + 40, { steps: 8 });
	await page.mouse.up();
	await expect(day('2026-10-23')).toBeInViewport();
	await expect.poll(handleLeft).toBeLessThan(before);

	// 손을 뗀 뒤 미끄러지던 것이 멈추기를 기다린다
	await page.waitForTimeout(1500);
	// 날짜 단위로 끊지 않고 민 만큼 그대로 멈춘다(반쯤 걸친 날도 그대로 둔다)
	await scroller.evaluate((element: HTMLElement) => {
		element.scrollLeft = (element.clientWidth / 7) * 2.5;
	});
	await page.waitForTimeout(300);
	expect(
		await scroller.evaluate(
			(element: HTMLElement) => element.scrollLeft / (element.clientWidth / 7)
		)
	).toBeCloseTo(2.5, 1);

	// 아래 막대의 왼쪽 끝을 누르면 맨 앞으로 간다
	const bar = (await section.locator('[data-range-bar]').boundingBox())!;
	await page.mouse.click(bar.x + 2, bar.y + bar.height / 2);
	await expect(day('2026-10-01')).toBeInViewport();
	await expect(day('2026-10-07')).toBeInViewport();
	await expect(day('2026-10-08')).not.toBeInViewport({ ratio: 0.1 });

	// 넘긴 자리에서도 올리면 팝업이 차트 안에 뜬다
	const popup = section.locator('[data-stage-popup]');
	await section.getByRole('button', { name: /^10\.07\(.\) 얕은 잠/ }).hover();
	await expect(popup).toContainText('10.07');
	const [frame, box] = await Promise.all([
		section.locator('[data-sleep-stage-bars]').boundingBox(),
		popup.boundingBox()
	]);
	expect(box!.x).toBeGreaterThanOrEqual(frame!.x);
	expect(box!.x + box!.width).toBeLessThanOrEqual(frame!.x + frame!.width);

	// 방향키로 화면 밖의 날로 가면 차트가 따라 넘어간다
	await section.getByRole('button', { name: /^10\.07\(.\) 깊은 잠/ }).focus();
	await page.keyboard.press('ArrowRight');
	await expect(day('2026-10-08')).toBeInViewport();
	await expect(popup).toContainText('10.08');
});

test('한 달 전과 비교한 수면은 겹치는 두 달 전체를 따로 적고 두 기간을 비교한다(S15P31A403-418)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.sleepMonthCompareOverlap);
	await expect(dialog).toContainText('30일 기간');
	await expect(dialog).toContainText('2026.10.18 ~ 11.16');
	await expect(dialog).toContainText('비교 기간 2026.09.18 ~ 10.17');
	await expect(dialog.locator('[data-evidence-overlap]')).toHaveText(
		/함께 조회한 기간 2026\.09\.20 ~ 11\.16 · 평균 \d+시간/
	);
	// 기간이 겹치는 목록은 두지 않고, 이전 기간 대비와 이전 기간 평균선으로 비교를 보인다
	await expect(dialog.locator('[data-evidence-detail="parts"]')).toHaveCount(0);
	await expect(dialog).toContainText('이전 기간 대비');
	await expect(dialog.locator('[data-previous-average]')).toHaveCount(1);
	await expect(dialog.getByRole('heading', { name: '일별 수면 시간' })).toBeVisible();
	// 수면 중 산소포화도는 날짜별 기록 달력의 그날 상세에 있다(S15P31A403-430)
	await expect(dialog.locator('[data-spo2-summary]')).toHaveCount(0);
	await expect(dialog.locator('[data-day-calendar]')).toBeVisible();
});

test('주 단위로 묶인 차트는 제목을 "주별 평균"으로 쓰고 가장 높은 주와 낮은 주를 알린다(S15P31A403-418)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.weeklySplit);
	await expect(dialog.getByRole('heading', { name: '주별 평균 수면 시간' })).toBeVisible();
	await expect(dialog.locator('[data-extreme="max"]')).toHaveCount(1);
	await expect(dialog.locator('[data-extreme="min"]')).toHaveCount(1);
});

test('일별 막대 차트는 일주일을 넘으면 최근 7일부터 보이고, 보기 전환으로 기간 전체를 볼 수 있다(S15P31A403-418)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page);
	const chart = dialog.getByRole('figure', { name: '일별 걸음 수 막대 차트' });
	const day = (date: string) => chart.locator(`[data-day="${date}"]`);
	const scroller = chart.locator('[data-range-scroller]');

	// 31일 기간: 처음에는 가장 최근 7일이고 7칸 모두 날짜를 적는다
	await expect(day('2026-10-31')).toBeInViewport();
	await expect(day('2026-10-25')).toBeInViewport();
	await expect(day('2026-10-24')).not.toBeInViewport({ ratio: 0.1 });
	await expect(day('2026-10-27').locator('[data-axis-label]')).toHaveText('10.27');
	// 날짜 버튼 없이 직접 넘긴다. 넘길 수 있다는 것은 아래 막대가 알린다
	await expect(chart.getByRole('button', { name: /7일/ })).toHaveCount(0);
	await expect(chart.locator('[data-range-bar]')).toBeVisible();

	// 키보드: 차트에 포커스를 두고 방향키로 넘긴다
	await scroller.focus();
	const start = await scroller.evaluate((element: HTMLElement) => element.scrollLeft);
	await page.keyboard.press('ArrowLeft');
	await expect
		.poll(() => scroller.evaluate((element: HTMLElement) => element.scrollLeft))
		.toBeLessThan(start);

	// 한 주 앞으로 넘긴다
	await scroller.evaluate((element: HTMLElement) => {
		element.scrollLeft = (element.clientWidth / 7) * 17;
	});
	await expect(day('2026-10-18')).toBeInViewport();
	await expect(day('2026-10-24')).toBeInViewport();

	// 넘긴 자리에서도 막대에 올리면 값이 차트 안에 뜬다
	await chart.locator('[data-bar="2026-10-24"]').hover();
	const popup = chart.locator('[data-bar-popup]');
	await expect(popup).toContainText('10.24');
	const [frame, box] = await Promise.all([chart.boundingBox(), popup.boundingBox()]);
	expect(box!.x).toBeGreaterThanOrEqual(frame!.x);
	expect(box!.x + box!.width).toBeLessThanOrEqual(frame!.x + frame!.width);

	// 기간 전체 보기: 넘겨 보는 막대가 사라지고 31칸이 한 화면에 들어온다
	await chart.getByRole('button', { name: '한 달', exact: true }).click();
	await expect(chart.locator('[data-range-bar]')).toHaveCount(0);
	await expect(day('2026-10-01')).toBeInViewport();
	await expect(day('2026-10-31')).toBeInViewport();
	// 다시 일주일로 돌아오면 최근 7일이다
	await chart.getByRole('button', { name: '일주일', exact: true }).click();
	await expect(day('2026-10-31')).toBeInViewport();
	await expect(day('2026-10-24')).not.toBeInViewport({ ratio: 0.1 });
});

test('차트에서 고른 기간 전체 보기는 그 창의 그 탭에만 남고, 창을 다시 열거나 다른 답변의 차트를 열면 일주일 보기로 시작한다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(
		page,
		[...evidenceCases.month, ...sleepSpo2Month],
		undefined,
		evidenceCases.exerciseSessionsMonth
	);
	const range = (name: string) =>
		dialog.getByRole('figure', { name }).locator('[data-chart-range]');
	const week = (name: string) => range(name).getByRole('button', { name: '일주일', exact: true });
	const whole = (name: string) => range(name).getByRole('button', { name: '한 달', exact: true });
	const steps = '일별 걸음 수 막대 차트';
	const sleep = '일별 수면 시간 막대 차트';
	const stages = '날짜별 수면 단계 막대 차트';
	const tab = (name: string) => dialog.getByRole('tab', { name, exact: true });
	const reopen = async (index: number) => {
		await dialog.getByRole('button', { name: '닫기' }).last().click();
		await expect(dialog).toBeHidden();
		await page
			.getByRole('region', { name: '이 답변에서 사용한 기록' })
			.nth(index)
			.getByRole('button')
			.click();
		await expect(dialog).toBeVisible();
	};

	// 처음에는 일주일이다. 걸음 차트를 한 달로 바꾼다
	await expect(week(steps)).toHaveAttribute('aria-pressed', 'true');
	await whole(steps).click();
	await expect(whole(steps)).toHaveAttribute('aria-pressed', 'true');

	// 수면 탭의 차트는 일주일부터다. 전에는 걸음에서 고른 한 달이 따라왔다. 수면 단계 차트만 한 달로 바꾼다
	await tab('수면').click();
	await expect(week(sleep)).toHaveAttribute('aria-pressed', 'true');
	await expect(week(stages)).toHaveAttribute('aria-pressed', 'true');
	await whole(stages).click();
	await expect(whole(stages)).toHaveAttribute('aria-pressed', 'true');

	// 같은 창에서 걸음 탭으로 돌아오면 거기서 고른 한 달이 남아 있다
	await tab('활동').click();
	await expect(whole(steps)).toHaveAttribute('aria-pressed', 'true');
	await expect(dialog.getByRole('figure', { name: steps }).locator('[data-range-bar]')).toHaveCount(
		0
	);
	// 수면 탭도 차트마다 고른 대로다: 수면 시간은 일주일, 수면 단계는 한 달
	await tab('수면').click();
	await expect(week(sleep)).toHaveAttribute('aria-pressed', 'true');
	await expect(whole(stages)).toHaveAttribute('aria-pressed', 'true');

	// 창을 닫았다 다시 열면 같은 답변이어도 모두 일주일이다
	await reopen(0);
	await expect(week(steps)).toHaveAttribute('aria-pressed', 'true');
	await tab('수면').click();
	await expect(week(sleep)).toHaveAttribute('aria-pressed', 'true');
	await expect(week(stages)).toHaveAttribute('aria-pressed', 'true');

	// 한 달로 바꾼 채 닫고 다른 답변의 차트를 열어도 일주일이다
	await tab('활동').click();
	await whole(steps).click();
	await reopen(1);
	const exercise = '일별 운동 시간 막대 차트';
	await expect(week(exercise)).toHaveAttribute('aria-pressed', 'true');
	// 일주일 보기라 넘겨 보는 막대가 있고, 기간 첫날은 화면 밖이다
	const chart = dialog.getByRole('figure', { name: exercise });
	await expect(chart.locator('[data-range-bar]')).toHaveCount(1);
	await expect(chart.locator('[data-bar]').first()).not.toBeInViewport({ ratio: 0.1 });
});

// 개발 서버에서 여러 질문을 넣어 보며 찾은 미흡한 화면들(S15P31A403-439)
test('심박을 28일과 그 끝에 하루 더 걸친 최근 7일로 함께 조회해도 심박 탭은 다른 탭과 같은 28일을 보여 준다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.partialOverlapHeart);
	await expect(dialog).toContainText('2026.09.30 ~ 10.27');
	await dialog.getByRole('tab', { name: '심박', exact: true }).click();
	// 전에는 늦게 끝나는 7일(10.22~10.28)이 차트로 나오고 28일은 한 줄로만 적혔다
	await expect(dialog).toContainText('28일 기간');
	await expect(dialog).toContainText('2026.09.30 ~ 10.27');
	await expect(
		dialog.getByRole('figure', { name: '일별 평균 심박 막대 차트' }).locator('[data-bar]')
	).toHaveCount(28);
	// 짧은 쪽도 버리지 않고 함께 조회한 기간으로 적는다
	await expect(dialog.locator('[data-evidence-overlap]')).toHaveText(
		/함께 조회한 기간 2026\.10\.22 ~ 10\.28/
	);
});

test('하루 수면 기록은 잠든 시각과 깬 시각을 보여 준다', async ({ page }) => {
	const dialog = await openEvidence(page, evidenceCases.sleepStagesOneDay);
	const times = dialog.locator('[data-evidence-detail="sleep-time"]');
	await expect(times.getByRole('heading', { name: '잠든·깬 시각' })).toBeVisible();
	// 합성 밤은 전날 23:30 에 잠들어 07:00 에 깬다
	await expect(times).toContainText('잠든 시각');
	await expect(times).toContainText('23:30');
	await expect(times).toContainText('깬 시각');
	await expect(times).toContainText('07:00');

	// 여러 날이면 달력의 그날 상세에 있어 따로 두지 않는다
	const month = await openEvidence(page, sleepSpo2Month);
	await expect(month.locator('[data-day-calendar]')).toBeVisible();
	await expect(month.locator('[data-evidence-detail="sleep-time"]')).toHaveCount(0);
});

test('운동 기록이 한 건뿐이면 "가장 길게"를 붙이지 않고, 횟수·칼로리·거리가 모두 비면 빈 표를 두지 않는다', async ({
	page
}) => {
	let dialog = await openEvidence(page, evidenceCases.exerciseOneSession);
	await expect(dialog.locator('[data-evidence-detail="sessions"]')).toContainText('근력 운동');
	await expect(dialog.getByText('가장 길게')).toHaveCount(0);
	await expect(dialog.locator('[data-evidence-detail="exercise-activity"]')).toContainText(
		'548kcal'
	);

	// 견줄 기록이 있으면 전처럼 가장 긴 한 건에 붙는다
	dialog = await openEvidence(page, evidenceCases.exerciseSessionsMonth);
	await dialog.locator('[data-calendar-day="2026-10-23"]').click();
	await expect(dialog.locator('[data-calendar-detail]').getByText('가장 길게')).toHaveCount(1);

	dialog = await openEvidence(page, evidenceCases.exerciseTodayEmpty);
	await expect(dialog).toContainText('이 기간에는 운동 기록이 없어요.');
	await expect(dialog.locator('[data-evidence-detail="exercise-activity"]')).toHaveCount(0);
	await expect(dialog.getByText('운동 횟수·칼로리·거리')).toHaveCount(0);
});

test('앞쪽이 앱 연동 전인 긴 기간의 달력은 연동한 달부터 시작하고, 연동 전 날짜는 누르는 칸이 아니다', async ({
	page
}) => {
	const dialog = await openEvidence(page, evidenceCases.stepsBeforeLinkMonths);
	const calendar = dialog.locator('[data-day-calendar]');
	const previous = calendar.getByRole('button', { name: '이전 달' });
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('10월');
	// 기간은 7월 31일부터지만 9월 9일에 연동했다. 전에는 "연동 전"만 나오는 7월·8월까지 넘어갔다
	await previous.click();
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('9월');
	await expect(previous).toBeHidden();
	await expect(calendar.locator('[data-calendar-day="2026-09-08"]')).toHaveCount(0);
	await calendar.locator('[data-calendar-day="2026-09-09"]').click();
	await expect(calendar.locator('[data-calendar-detail]')).toContainText('9월');
	await expect(calendar.locator('[data-calendar-detail]')).not.toContainText('연동 전');
	await expect(calendar.locator('[data-hourly-steps] [data-hour]')).toHaveCount(24);
	// 뺀 날 수는 창 아래에 그대로 알린다
	await expect(dialog).toContainText('40일은 앱 연동 전이라 기간에서 뺐어요.');
});

test('앱 연동 전 하루만 물으면 그 하루를 뺐다고 하지 않고 연동 전이라 기록이 없다고 알린다', async ({
	page
}) => {
	let dialog = await openEvidence(page, evidenceCases.stepsBeforeLinkDay);
	await expect(dialog).toContainText('앱을 연동하기 전이라 활동 기록이 없어요.');
	// 전에는 "이 기간에는 활동 기록이 없어요" 아래에 "1일은 앱 연동 전이라 기간에서 뺐어요"가 붙었다
	await expect(dialog).not.toContainText('이 기간에는');
	await expect(dialog).not.toContainText('기간에서 뺐어요');

	// 기간의 일부만 연동 전이면 전처럼 뺀 날 수를 알린다
	dialog = await openEvidence(page, evidenceCases.beforeLink);
	await expect(dialog).toContainText('4일은 앱 연동 전이라 기간에서 뺐어요.');
	await expect(dialog).not.toContainText('앱을 연동하기 전이라');
});

test('일주일 이하의 일별 차트와 주 단위 차트에는 넘기는 버튼과 보기 전환이 없다(S15P31A403-418)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	let dialog = await openEvidence(page, evidenceCases.sleepStagesWeek);
	await expect(dialog.locator('figure')).toHaveCount(2);
	await expect(dialog.locator('[data-chart-range]')).toHaveCount(0);

	dialog = await openEvidence(page, evidenceCases.quarter);
	const weekly = dialog.getByRole('figure', { name: '주별 평균 걸음 수 막대 차트' });
	await expect(weekly).toBeVisible();
	await expect(weekly.locator('[data-chart-range]')).toHaveCount(0);
});

test('최근 며칠에 기록이 없으면 값이 있는 마지막 날이 오른쪽 끝에 오게 시작한다(S15P31A403-420)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.stepsRecentGap);
	const chart = dialog.getByRole('figure', { name: '일별 걸음 수 막대 차트' });
	const day = (date: string) => chart.locator(`[data-day="${date}"]`);
	const scroller = chart.locator('[data-range-scroller]');

	// 10.23 부터 8일이 동기화 전이다. 처음 화면은 빈 칸 일곱 개가 아니라 10.16 ~ 10.22 다
	await expect(day('2026-10-22')).toBeInViewport();
	await expect(day('2026-10-16')).toBeInViewport();
	await expect(day('2026-10-23')).not.toBeInViewport({ ratio: 0.1 });
	await expect(day('2026-10-22').locator('[data-empty-day]')).toHaveCount(0);
	// 뒤의 빈 날은 숨기지 않는다. 오른쪽으로 넘기면 그대로 보인다
	await scroller.evaluate((element: HTMLElement) => {
		element.scrollLeft = element.scrollWidth;
	});
	await expect(day('2026-10-30')).toBeInViewport();
	await expect(day('2026-10-30').locator('[data-empty-day]')).toHaveCount(1);
});

test('최고·최저 꼬리표는 기간 전체의 가장 높은 날과 낮은 날에 고정되고, 넘겨도 다른 막대로 옮겨 가지 않는다(S15P31A403-428)', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.month);
	const chart = dialog.getByRole('figure', { name: '일별 걸음 수 막대 차트' });
	const scroller = chart.locator('[data-range-scroller]');
	const { daily_max: max, daily_min: min } = evidenceCases.month[0].snapshot.summary;
	const dayOf = (kind: 'max' | 'min') =>
		chart
			.locator('[data-day]')
			.filter({ has: page.locator(`[data-extreme="${kind}"]`) })
			.getAttribute('data-day');

	// 일주일 보기에서도 꼬리표는 기간 전체의 날에 하나씩만 있다
	await expect(chart.locator('[data-extreme="max"]')).toHaveCount(1);
	await expect(chart.locator('[data-extreme="min"]')).toHaveCount(1);
	expect(await dayOf('max')).toBe(max?.date);
	expect(await dayOf('min')).toBe(min?.date);
	// 넘겨도 같은 날에 그대로 있다
	for (const share of [0, 0.5, 1]) {
		await scroller.evaluate((element: HTMLElement, value: number) => {
			element.scrollLeft = (element.scrollWidth - element.clientWidth) * value;
		}, share);
		expect(await dayOf('max')).toBe(max?.date);
		expect(await dayOf('min')).toBe(min?.date);
	}
	await chart.getByRole('button', { name: '한 달', exact: true }).click();
	expect(await dayOf('max')).toBe(max?.date);
	expect(await dayOf('min')).toBe(min?.date);
});

test('기간 전체 보기에서도 꼬리표는 막대 가까이 있고 막대와 서로를 덮지 않는다(S15P31A403-428)', async ({
	page
}) => {
	for (const width of [1366, 390]) {
		await page.setViewportSize({ width, height: 900 });
		const dialog = await openEvidence(page, evidenceCases.month);
		const chart = dialog.getByRole('figure', { name: '일별 걸음 수 막대 차트' });
		await chart.getByRole('button', { name: '한 달', exact: true }).click();
		const problems = await chart.evaluate((root: HTMLElement) => {
			const box = (el: Element) => el.getBoundingClientRect();
			const hit = (a: DOMRect, b: DOMRect) =>
				a.left < b.right - 0.5 &&
				b.left < a.right - 0.5 &&
				a.top < b.bottom - 0.5 &&
				b.top < a.bottom - 0.5;
			const chips = [...root.querySelectorAll('[data-extreme]')].map(box);
			const bars = [...root.querySelectorAll('[data-bar] [data-bar-title]')].map(box);
			const found: string[] = [];
			chips.forEach((chip, i) => {
				if (bars.some((bar) => hit(chip, bar))) found.push('막대와 겹침');
				if (chips.some((other, j) => j > i && hit(chip, other))) found.push('꼬리표끼리 겹침');
				if (chip.top < box(root).top) found.push('차트 상자 밖');
			});
			// 차트 맨 위 줄이 아니라 막대 근처에 있다: 차트 높이(176px)의 절반보다 멀리 뜨지 않는다
			root.querySelectorAll('[data-extreme]').forEach((chip) => {
				const bar = chip.closest('[data-bar]')?.querySelector('[data-bar-title]');
				if (bar && box(bar).top - box(chip).bottom > 88) found.push('막대에서 멂');
			});
			return found;
		});
		expect(problems, `${width}px`).toEqual([]);
	}
});

// S15P31A403-439 꼬리표 폭은 글자 수로 어림해 실제보다 작게 잡힌다. 최고와 최저가 기간 양 끝 날에 있으면
// 계산으로는 떨어져 있는데 화면에서는 1~11px 겹쳤다(걸음은 330·400px 에서 겹쳤다)
test('최고와 최저가 기간의 첫날과 마지막 날이어도 휴대폰 폭에서 꼬리표끼리 겹치지 않는다(S15P31A403-439)', async ({
	page
}) => {
	const evidence = JSON.parse(JSON.stringify(evidenceCases.month[0]));
	const days = evidence.snapshot.days;
	const first = days[0];
	const last = days[days.length - 1];
	// 가운데 날들은 8,000~10,400걸음으로 채워, 양 끝의 두 꼬리표가 비슷한 높이에 놓이게 한다
	days.forEach((day: { value: number; missing: boolean; reason: null }, index: number) => {
		day.value = 8000 + ((index * 37) % 9) * 300;
		day.missing = false;
		day.reason = null;
	});
	first.value = 12113;
	last.value = 5120;
	evidence.snapshot.summary.daily_max = { date: first.date, value: first.value };
	evidence.snapshot.summary.daily_min = { date: last.date, value: last.value };
	const dialog = await openEvidence(page, [evidence]);
	const chart = dialog.getByRole('figure', { name: '일별 걸음 수 막대 차트' });
	await chart.getByRole('button', { name: '한 달', exact: true }).click();
	for (const width of [320, 330, 360, 400, 430]) {
		await page.setViewportSize({ width, height: 900 });
		await page.waitForTimeout(250);
		await expect(chart.locator('[data-extreme]')).toHaveCount(2);
		const overlap = await chart.evaluate((root: HTMLElement) => {
			const [a, b] = [...root.querySelectorAll('[data-extreme]')].map((chip) =>
				chip.getBoundingClientRect()
			);
			return (
				Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
				Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5
			);
		});
		expect(overlap, `${width}px`).toBe(false);
	}
});

// S15P31A403-439 수면·걸음·심박·운동 네 탭의 일별 차트가 모두 최고·최저 꼬리표를 단다
for (const [name, evidence, figure] of [
	['걸음', evidenceCases.month, '일별 걸음 수 막대 차트'],
	['수면', evidenceCases.sleepStagesWeek, '일별 수면 시간 막대 차트'],
	['심박', evidenceCases.heartWeek, '일별 평균 심박 막대 차트'],
	['운동', evidenceCases.exerciseSessionsMonth, '일별 운동 시간 막대 차트']
] as const) {
	test(`${name} 일별 차트는 최고·최저 꼬리표를 달고, 달력 체크와 같은 날을 가리킨다`, async ({
		page
	}) => {
		const dialog = await openEvidence(page, [...evidence]);
		const chart = dialog.getByRole('figure', { name: figure });
		const marks = calendarExtremes(evidence[0].snapshot);
		for (const kind of ['max', 'min'] as const) {
			const chip = chart.locator(`[data-extreme="${kind}"]`);
			await expect(chip).toHaveCount(1);
			const day = chart
				.locator('[data-bar]')
				.filter({ has: page.locator(`[data-extreme="${kind}"]`) });
			await expect(day).toHaveAttribute('data-bar', marks[kind]!.date);
		}
		// 운동은 0분인 날이 있어도 운동한 날 가운데 가장 짧은 날이 최저다
		if (name === '운동') {
			expect(evidence[0].snapshot.summary.daily_min?.value).toBe(0);
			expect(marks.min!.value).toBeGreaterThan(0);
		}
	});
}

test('하루 기록의 수면 단계는 "평균"이라고 부르지 않는다(S15P31A403-420)', async ({ page }) => {
	const dialog = await openEvidence(page, evidenceCases.sleepStagesOneDay);
	const section = dialog.locator('[data-evidence-detail="sleep-stages"]');
	await expect(section.getByRole('heading')).toHaveText('수면 단계');
});

test('휴대폰 폭에서 넘겨 보는 일별 차트는 축 날짜를 하루 걸러 적는다(S15P31A403-420)', async ({
	page
}) => {
	await page.setViewportSize({ width: 390, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.month);
	const chart = dialog.getByRole('figure', { name: '일별 걸음 수 막대 차트' });
	const label = (date: string) => chart.locator(`[data-day="${date}"] [data-axis-label] > span`);

	// 가장 최근 날부터 세어 하루 걸러 적는다
	await expect(label('2026-10-31')).toBeVisible();
	await expect(label('2026-10-30')).toBeHidden();
	await expect(label('2026-10-29')).toBeVisible();
	// 넓은 화면에서는 모든 날짜를 적는다
	await page.setViewportSize({ width: 1366, height: 900 });
	await expect(label('2026-10-30')).toBeVisible();
});

// S15P31A403-430 날짜별 기록 달력: 칸에는 평균 대비 도넛, 날짜를 누르면 그날 기록, 물음표는 가운데 안내 팝업
test('날짜별 기록 달력은 날짜를 누르면 그날 값을 평균과 비교해 보여 주고, 물음표는 안내 팝업만 연다', async ({
	page
}) => {
	const dialog = await openEvidence(page, evidenceCases.month);
	const calendar = dialog.locator('[data-day-calendar]');
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('10월');
	// 앞 달의 날(9월 27~30일)은 누를 수 없는 흐린 칸이다
	await expect(calendar.locator('[data-calendar-day]')).toHaveCount(31);
	await calendar.getByRole('button', { name: /^10월 2일 금요일, / }).click();
	const detail = calendar.locator('[data-calendar-detail]');
	await expect(detail).toContainText('금요일');
	await expect(detail).toContainText(/평균보다 [\d,]+걸음 (많아요|적어요)|평균과 같아요/);
	// 기록 없는 날은 점선 도넛이고, 누르면 이유를 보여 준다
	const missing = calendar.getByRole('button', { name: /^10월 10일 토요일, / });
	await expect(missing.locator('[data-donut="missing"]')).toHaveCount(1);
	await missing.click();
	await expect(detail).toContainText('기록 없음');

	await calendar.getByRole('button', { name: '달력 보는 법' }).click();
	const help = page.getByRole('dialog', { name: '한 바퀴가 하루 평균이에요' });
	await expect(help).toBeVisible();
	await expect(help).toContainText('이 기간 하루 평균');
	// Esc 는 안내만 닫고 근거 창은 그대로다
	await page.keyboard.press('Escape');
	await expect(help).toBeHidden();
	await expect(dialog).toBeVisible();
});

test('평균을 넘은 날은 두 바퀴째가 기간 최고까지 차서, 넘은 날끼리도 모양이 다르다', async ({
	page
}) => {
	const dialog = await openEvidence(page, evidenceCases.exerciseSessionsMonth);
	const calendar = dialog.locator('[data-day-calendar]');
	const laps = await calendar
		.locator('[data-lap="2"]')
		.evaluateAll((circles) => circles.map((circle) => circle.getAttribute('stroke-dasharray')));
	expect(laps.length).toBeGreaterThan(1);
	expect(new Set(laps).size).toBeGreaterThan(1);
	// 가장 오래 운동한 날만 두 바퀴째가 꽉 찬다
	expect(laps.filter((dash) => dash === '100 100')).toHaveLength(1);
});

test('운동 달력은 그날 운동 기록을, 수면 달력은 그날 산소포화도를 보여 주고 달을 넘길 수 있다', async ({
	page
}) => {
	const exercise = await openEvidence(page, evidenceCases.exerciseSessionsMonth);
	const calendar = exercise.locator('[data-day-calendar]');
	await calendar.getByRole('button', { name: /^10월 23일 금요일, / }).click();
	await expect(calendar.locator('[data-calendar-detail] li')).toHaveCount(1);
	await calendar.getByRole('button', { name: '이전 달' }).click();
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('9월');
	await expect(calendar.getByRole('button', { name: '이전 달' })).toBeHidden();

	const sleep = await openEvidence(page, sleepSpo2Month);
	const nights = sleep.locator('[data-day-calendar]');
	await expect(nights.locator('[data-spo2-low-dot]').first()).toBeVisible();
	const low = nights
		.locator('[data-calendar-day]', { has: page.locator('[data-spo2-low-dot]') })
		.first();
	await low.click();
	await expect(nights.locator('[data-calendar-detail]')).toContainText('평균 산소포화도');
	await expect(nights.locator('[data-calendar-detail]')).toContainText('90% 미만');
});

for (const width of [320, 390]) {
	test(`${width}px 에서 날짜별 기록 달력이 창 밖으로 넘치지 않는다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 800 });
		const dialog = await openEvidence(page, evidenceCases.sleepSpo2MonthClean);
		await expect(dialog.locator('[data-day-calendar]')).toBeVisible();
		expect(
			await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
		).toBe(0);
	});
}

// 달력 전수 확인: 처음 고른 날이 보이는 달에 있고, 달을 끝까지 넘기면 기간의 모든 날을 한 번씩 누를 수 있다
for (const [name, evidence, periodDays] of [
	['걸음 31일, 오늘 수집 중', evidenceCases.month, 31],
	['걸음, 최근 8일 동기화 전', evidenceCases.stepsRecentGap, 30],
	['운동 30일, 9월에 걸침', evidenceCases.exerciseSessionsMonth, 30],
	['수면 35일, 두 달에 걸침', evidenceCases.sleepFiveWeeks, 35],
	['운동 6개월, 31일씩 여섯 번 나눠 조회', evidenceCases.exerciseSixMonths, 181],
	['수면 6주, 한 주씩 나눠 조회', evidenceCases.weeklySplit, 42]
] as const) {
	test(`날짜별 기록 달력 전수 확인: ${name}`, async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });
		const dialog = await openEvidence(page, [...evidence]);
		const calendar = dialog.locator('[data-day-calendar]');
		const pressed = calendar.locator('[data-calendar-day][aria-pressed="true"]');
		await expect(pressed).toHaveCount(1);
		await expect(pressed).toBeVisible();
		const detail = calendar.locator('[data-calendar-detail]');
		const pressedDate = (await pressed.getAttribute('data-calendar-day'))!;
		await expect(detail).toContainText(`${Number(pressedDate.slice(5, 7))}월`);

		// 첫 달까지 되감은 뒤 끝까지 넘기며 날짜를 모은다
		const previous = calendar.getByRole('button', { name: '이전 달' });
		while (await previous.isVisible()) await previous.click();
		const seen: string[] = [];
		const next = calendar.getByRole('button', { name: '다음 달' });
		for (;;) {
			seen.push(
				...(await calendar
					.locator('[data-calendar-day]')
					.evaluateAll((buttons) =>
						buttons.map((button) => button.getAttribute('data-calendar-day')!)
					))
			);
			if (!(await next.isVisible())) break;
			await next.click();
		}
		expect(seen).toHaveLength(periodDays);
		expect(new Set(seen).size).toBe(periodDays);
		expect([...seen].sort()).toEqual(seen);

		// 아무 날이나 눌러도 상세가 그날로 바뀌고, 고른 표시는 하나뿐이다
		const last = calendar.locator('[data-calendar-day]').last();
		await last.click();
		await expect(calendar.locator('[data-calendar-day][aria-pressed="true"]')).toHaveCount(1);
		const lastDate = (await last.getAttribute('data-calendar-day'))!;
		await expect(detail).toContainText(String(Number(lastDate.slice(8))));
		expect(
			await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth)
		).toBe(0);
	});
}

test('6개월 운동처럼 나눠 조회한 긴 기간도 달력으로 보여 주고, 운동한 날 평균이라고 알린다', async ({
	page
}) => {
	const dialog = await openEvidence(page, evidenceCases.exerciseSixMonths);
	const calendar = dialog.locator('[data-day-calendar]');
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('11월');
	await expect(dialog.getByText(/날짜별 전체 보기/)).toHaveCount(0);
	// 기간별 평균 목록은 그대로 둔다
	await expect(dialog.locator('[data-evidence-detail="parts"] li')).toHaveCount(6);
	await calendar.getByRole('button', { name: '달력 보는 법' }).click();
	// 운동은 쉰 날을 뺀 운동한 날 평균이 기준이다(S15P31A403-435)
	const help = page.getByRole('dialog', { name: '한 바퀴가 운동한 날 평균이에요' });
	await expect(help).toContainText('이 기간 운동한 날 평균');
	await expect(help).toContainText('운동 기록이 없는 날은 쉬는 날이라 평균과 최저에서 뺐어요.');
	// 쉰 날과 기록 없는 날을 나누지 않아 보기 원도 "기록 없음" 하나다
	await expect(help).not.toContainText('쉰 날');
	await page.keyboard.press('Escape');
	// 나눈 경계 날(7.21 마지막·7.22 첫날)도 그날 운동 기록이 나온다
	for (let step = 0; step < 4; step += 1)
		await calendar.getByRole('button', { name: '이전 달' }).click();
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('7월');
	for (const date of ['2026-07-21', '2026-07-22']) {
		await calendar.locator(`[data-calendar-day="${date}"]`).click();
		await expect(calendar.locator('[data-calendar-detail]')).toContainText(
			`${Number(date.slice(8))}`
		);
	}
});

test('수면 달력은 가장 길게·짧게 잔 날을 상자 대신 원 가운데 체크로 표시한다', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, sleepSpo2Month);
	await expect(dialog.getByText(/가장 길게 잔 날 · /)).toHaveCount(0);
	const calendar = dialog.locator('[data-day-calendar]');
	const longest = calendar.locator('[data-calendar-day]', {
		has: page.locator('[data-extreme-mark="max"]')
	});
	const shortest = calendar.locator('[data-calendar-day]', {
		has: page.locator('[data-extreme-mark="min"]')
	});
	await expect(longest).toHaveCount(1);
	await expect(shortest).toHaveCount(1);
	await expect(longest).toHaveAttribute('aria-label', /, 가장 길게 잔 날$/);
	await longest.click();
	await expect(calendar.locator('[data-calendar-extreme="max"]')).toHaveText('가장 길게 잔 날');
	await shortest.click();
	await expect(calendar.locator('[data-calendar-extreme="min"]')).toHaveText('가장 짧게 잔 날');
});

// S15P31A403-435 수면 말고 다른 달력 탭도 최고·최저를 원 가운데 체크로 표시하고 요약 상자를 두지 않는다
for (const [name, evidence, highest, lowest] of [
	['걸음', evidenceCases.month, '가장 많이 걸은 날', '가장 적게 걸은 날'],
	['심박', evidenceCases.heartWeekMeasured, '가장 높은 날', '가장 낮은 날'],
	['운동 6개월', evidenceCases.exerciseSixMonths, '가장 오래 운동한 날', '가장 짧게 운동한 날']
] as const) {
	test(`${name} 달력도 최고·최저를 체크로 표시하고 요약 상자를 두지 않는다`, async ({ page }) => {
		await page.setViewportSize({ width: 375, height: 812 });
		const dialog = await openEvidence(page, [...evidence]);
		await expect(dialog.getByText(/(가장 .+ 날|오늘\(수집 중\)) · \d{2}\.\d{2}/)).toHaveCount(0);
		await expect(dialog.getByText('기록 없는 날', { exact: true })).toHaveCount(0);
		const calendar = dialog.locator('[data-day-calendar]');
		// 최고 날은 기간 마지막 달이 아닐 수 있어 달을 되감으며 찾는다
		const previous = calendar.getByRole('button', { name: '이전 달' });
		const marked = calendar.locator('[data-calendar-day]', {
			has: page.locator('[data-extreme-mark="max"]')
		});
		while ((await marked.count()) === 0 && (await previous.isVisible())) await previous.click();
		await expect(marked).toHaveCount(1);
		await marked.click();
		await expect(calendar.locator('[data-calendar-extreme="max"]')).toHaveText(highest);
		await calendar.getByRole('button', { name: '달력 보는 법' }).click();
		await expect(page.getByRole('dialog', { name: /^한 바퀴가 .*평균이에요$/ })).toContainText(
			lowest
		);
	});
}

// S15P31A403-435 운동한 날만 세고, 쉰 날(0분)은 최저로 표시하지 않는다. 상세 날짜 줄은 "10월 27 화요일" 순서
test('운동 달력은 쉰 날을 최저로 치지 않고, 상세 날짜는 달을 먼저 쓴다', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, evidenceCases.exerciseSessionsMonth);
	const calendar = dialog.locator('[data-day-calendar]');
	const detail = calendar.locator('[data-calendar-detail]');
	// 0분인 날은 기록 없는 날과 같게 점선 원과 "기록 없음"으로 보인다
	const rest = calendar.locator('[data-calendar-day="2026-10-01"]');
	await expect(rest.locator('[data-donut="missing"]')).toHaveCount(1);
	await expect(rest).toHaveAttribute('aria-label', /, 기록 없음$/);
	await rest.click();
	await expect(detail).toContainText('기록 없음');
	await expect(detail).not.toContainText('평균보다');
	// 가장 짧게 운동한 날(40분, 같으면 이른 9.29)은 앞 달에 있다
	await calendar.getByRole('button', { name: '이전 달' }).click();
	const shortest = calendar.locator('[data-calendar-day]', {
		has: page.locator('[data-extreme-mark="min"]')
	});
	await expect(shortest).toHaveCount(1);
	await expect(shortest).toHaveAttribute('data-calendar-day', '2026-09-29');
	await shortest.click();
	// 가장 짧게 운동한 날은 0분이 아닌 날이다
	await expect(calendar.locator('[data-calendar-extreme="min"]')).toHaveText('가장 짧게 운동한 날');
	await expect(detail).toContainText('40분');
	await expect(detail.locator('p').first()).toHaveText(/^9월\s*29\s*화요일$/);
});

// S15P31A403-430 달력 시나리오 전수: 모든 예시 근거 × 탭 × 달 × 날을 누르고 안내를 열어도 오류·넘침이 없다
const calendarScenarios = Object.entries({
	...evidenceCases,
	sleepSpo2Month,
	spo2Month,
	overlappingSleepEvidence
}).filter(([, items]) =>
	(items as { snapshot: { days?: unknown } }[]).some((item) => item.snapshot.days !== undefined)
);
for (const width of [390, 1366]) {
	test(`${width}px 달력 시나리오 전수: 모든 예시 근거의 모든 탭·달·날`, async ({ page }) => {
		test.setTimeout(240_000);
		const errors: string[] = [];
		page.on('pageerror', (error) => errors.push(error.message));
		page.on('console', (message) => {
			if (message.type() === 'error') errors.push(message.text());
		});
		await page.setViewportSize({ width, height: 900 });
		let calendars = 0;
		for (const [name, evidence] of calendarScenarios) {
			const dialog = await openEvidence(page, [...(evidence as unknown[])]);
			const tabs = dialog.getByRole('tab');
			const tabCount = Math.max(1, await tabs.count());
			for (let index = 0; index < tabCount; index += 1) {
				if ((await tabs.count()) > 0) await tabs.nth(index).click();
				const calendar = dialog.locator('[data-day-calendar]');
				if ((await calendar.count()) === 0) continue;
				calendars += 1;
				const previous = calendar.getByRole('button', { name: '이전 달' });
				while (await previous.isVisible()) await previous.click();
				const next = calendar.getByRole('button', { name: '다음 달' });
				for (;;) {
					const days = calendar.locator('[data-calendar-day]');
					const count = await days.count();
					for (let day = 0; day < count; day += 1) {
						await days.nth(day).click();
						await expect(
							calendar.locator('[data-calendar-day][aria-pressed="true"]'),
							name
						).toHaveCount(1);
						await expect(calendar.locator('[data-calendar-detail]'), name).not.toContainText(
							/undefined|NaN|null/
						);
					}
					if (!(await next.isVisible())) break;
					await next.click();
				}
				await calendar.getByRole('button', { name: '달력 보는 법' }).click();
				const help = page.getByRole('dialog', { name: /^한 바퀴가 .*평균이에요$/ });
				await expect(help, name).toBeVisible();
				await expect(help, name).not.toContainText(/undefined|NaN|도넛/);
				await page.keyboard.press('Escape');
				await expect(help, name).toBeHidden();
				expect(
					await dialog.evaluate(
						(element: HTMLElement) => element.scrollWidth - element.clientWidth
					),
					name
				).toBe(0);
			}
		}
		expect(calendars).toBeGreaterThan(15);
		expect(errors).toEqual([]);
	});
}

// S15P31A403-438 활동 달력에서 날짜를 고르면 그날 시간대별 걸음을 불러와 보여 준다
test('활동 달력 상세는 그날 시간대별 걸음을 보여 주고, 기록이 없는 날은 알린다', async ({
	page
}) => {
	await page.setViewportSize({ width: 375, height: 812 });
	const dialog = await openEvidence(page, evidenceCases.month);
	const calendar = dialog.locator('[data-day-calendar]');
	const hourly = calendar.locator('[data-hourly-steps]');
	await calendar.locator('[data-calendar-day="2026-10-05"]').click();
	await expect(hourly).toContainText('가장 많이 걸은 시간 18시 · 1,550걸음');
	await expect(hourly.locator('[data-hour]')).toHaveCount(24);
	// 값은 막대에 마우스를 올리면 팝업으로 보인다(S15P31A403-439)
	await hourly.locator('[data-hour="18"]').hover();
	await expect(hourly.locator('[data-hourly-popup]')).toContainText('1,550걸음');
	await expect(hourly).toContainText('지금 저장된 기록 기준이에요.');
	// 시간대 기록이 없는 날
	await calendar.locator('[data-calendar-day="2026-10-10"]').click();
	await expect(hourly).toContainText('이날은 시간대별 걸음 기록이 없어요.');
	// 다른 탭에는 두지 않는다
	const sleep = await openEvidence(page, evidenceCases.sleepStagesWeek);
	await expect(sleep.locator('[data-hourly-steps]')).toHaveCount(0);
});

// 달 단위로 부르므로(S15P31A403-439) 실패는 그 달 전체의 실패다. 다시 불러오면 그 달이 한 번에 온다
test('시간대별 걸음을 불러오지 못하면 알리고, 다시 불러오면 그 달 값을 보여 준다', async ({
	page
}) => {
	let fail = true;
	const dialog = await openEvidence(page, evidenceCases.month, async (route) => {
		if (!fail) return false;
		await route.fulfill({
			status: 500,
			json: { error: { code: 'INTERNAL_ERROR', message: 'x' } }
		});
		return true;
	});
	const calendar = dialog.locator('[data-day-calendar]');
	const hourly = calendar.locator('[data-hourly-steps]');
	await calendar.locator('[data-calendar-day="2026-10-06"]').click();
	await expect(hourly).toContainText('시간대별 걸음을 불러오지 못했어요.');
	fail = false;
	await hourly.getByRole('button', { name: '다시 불러오기' }).click();
	await expect(hourly).toContainText('가장 많이 걸은 시간 18시 · 1,560걸음');
	await calendar.locator('[data-calendar-day="2026-10-05"]').click();
	await expect(hourly).toContainText('가장 많이 걸은 시간 18시 · 1,550걸음');
});

// S15P31A403-438 대화를 드래그해 복사하면 코치 캐릭터 이미지는 빠지고 글만 복사된다
test('대화를 복사하면 코치 캐릭터 이미지는 빠지고 답 글만 남는다', async ({ page }) => {
	const dialog = await openEvidence(page, evidenceCases.month);
	await dialog.getByRole('button', { name: '닫기' }).first().click();
	const copied = await page.evaluate(() => {
		const article = document.querySelector('article')!;
		const range = document.createRange();
		range.selectNodeContents(article.parentElement!);
		const selection = window.getSelection()!;
		selection.removeAllRanges();
		selection.addRange(range);
		const clipboardData = new DataTransfer();
		article.dispatchEvent(
			new ClipboardEvent('copy', { clipboardData, bubbles: true, cancelable: true })
		);
		return { html: clipboardData.getData('text/html'), text: clipboardData.getData('text/plain') };
	});
	expect(copied.html).not.toContain('<img');
	expect(copied.html).not.toContain('character-coach');
	expect(copied.text).toContain('정리했어요.');
	expect(copied.text).not.toContain('AI 코치](');
});

// S15P31A403-438 피드백: 달력은 기본 5주라 달을 넘겨도 높이·닫기 버튼 위치가 그대로이고, 6주인 달만 한 줄 높다
for (const width of [390, 1366]) {
	test(`${width}px 달력은 5주인 달끼리 높이가 같고 6주인 달만 한 줄 높다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 900 });
		// 2026년 5월(6주)·6월·7월·8월(6주)·9월·10월·11월에 걸친 6개월 운동
		const dialog = await openEvidence(page, evidenceCases.exerciseSixMonths);
		const calendar = dialog.locator('[data-day-calendar]');
		const box = calendar.locator('> div').first();
		const cells = calendar.locator('ol[aria-label$="날짜별 기록"] > li');
		const close = dialog.getByRole('button', { name: '닫기', exact: true }).last();
		const measure = async () => {
			await dialog.evaluate((element) => (element.scrollTop = element.scrollHeight));
			const [boxRect, closeRect, scroll, count] = await Promise.all([
				box.boundingBox(),
				close.boundingBox(),
				dialog.evaluate((element) => element.scrollHeight),
				cells.count()
			]);
			return {
				weeks: count / 7,
				size: { height: boxRect!.height, close: closeRect!.y, scroll }
			};
		};
		const months = [await measure()];
		const previous = calendar.getByRole('button', { name: '이전 달' });
		while (await previous.isVisible()) {
			await previous.click();
			months.push(await measure());
		}
		// 11월부터 5월까지 거꾸로
		expect(months.map((month) => month.weeks)).toEqual([5, 5, 5, 6, 5, 5, 6]);
		const five = months.filter((month) => month.weeks === 5).map((month) => month.size);
		const six = months.filter((month) => month.weeks === 6).map((month) => month.size);
		for (const size of five) expect(size).toEqual(five[0]);
		for (const size of six) expect(size).toEqual(six[0]);
		// 6주인 달은 한 줄(날짜 칸 하나 높이)만큼만 높다
		const row = await cells.first().evaluate((cell) => cell.getBoundingClientRect().height);
		expect(six[0].height - five[0].height).toBeGreaterThan(row - 1);
		expect(six[0].height - five[0].height).toBeLessThan(row + 8);
	});
}

// S15P31A403-438 피드백 점검: 여러 화면 폭에서 달력 안 요소가 상자 밖으로 나가거나 글이 잘리지 않는다
const LAYOUT_CASES = [
	['걸음', evidenceCases.month],
	['운동 2주', evidenceCases.exerciseActivityTwoWeeks],
	['운동 6개월', evidenceCases.exerciseSixMonths],
	['수면 한 달', sleepSpo2Month],
	['심박 일주일', evidenceCases.heartWeekMeasured]
] as const;
for (const width of [320, 360, 480, 600, 640, 700, 768, 900, 1024, 1920]) {
	test(`${width}px 달력 상자 안 요소가 넘치거나 잘리지 않는다`, async ({ page }) => {
		test.setTimeout(120_000);
		await page.setViewportSize({ width, height: 900 });
		for (const [name, evidence] of LAYOUT_CASES) {
			const dialog = await openEvidence(page, [...evidence]);
			const calendar = dialog.locator('[data-day-calendar]');
			const days = calendar.locator('[data-calendar-day]');
			// 최고 날(꼬리표가 붙어 가장 넓다)·첫날·마지막 날을 차례로 눌러 본다
			const targets = [
				calendar.locator('[data-calendar-day]', { has: page.locator('[data-extreme-mark="max"]') }),
				days.first(),
				days.last()
			];
			for (const target of targets) {
				if ((await target.count()) === 0) continue;
				await target.first().click();
				const problems = await calendar.evaluate((root) => {
					const found: string[] = [];
					for (const box of root.querySelectorAll<HTMLElement>(
						':scope > div, [data-calendar-detail], [data-evidence-detail="exercise-activity"]'
					)) {
						const outer = box.getBoundingClientRect();
						for (const element of box.querySelectorAll<HTMLElement>('*')) {
							if (element.closest('.sr-only') || element.getClientRects().length === 0) continue;
							const rect = element.getBoundingClientRect();
							if (rect.width === 0) continue;
							if (rect.left < outer.left - 1 || rect.right > outer.right + 1)
								found.push(`넘침 ${element.tagName} "${element.textContent?.trim().slice(0, 20)}"`);
							if (
								getComputedStyle(element).overflow !== 'visible' &&
								element.scrollWidth > element.clientWidth + 1
							)
								found.push(`잘림 ${element.tagName} "${element.textContent?.trim().slice(0, 20)}"`);
						}
					}
					return found;
				});
				expect(problems, `${name} ${width}px`).toEqual([]);
			}
			expect(
				await dialog.evaluate((element: HTMLElement) => element.scrollWidth - element.clientWidth),
				name
			).toBe(0);
		}
	});
}

// 낮은 화면(가로로 든 휴대폰 등)에서도 안내 팝업의 확인 버튼까지 닿는다
for (const [width, height] of [
	[740, 360],
	[390, 480]
]) {
	test(`${width}x${height} 안내 팝업이 화면보다 길어도 끝까지 볼 수 있다`, async ({ page }) => {
		await page.setViewportSize({ width, height });
		const dialog = await openEvidence(page, evidenceCases.month);
		await dialog
			.locator('[data-day-calendar]')
			.getByRole('button', { name: '달력 보는 법' })
			.click();
		const help = page.getByRole('dialog', { name: /^한 바퀴가 .*평균이에요$/ });
		const confirm = help.getByRole('button', { name: '확인' });
		await confirm.scrollIntoViewIfNeeded();
		await expect(confirm).toBeInViewport();
		const box = await help.boundingBox();
		expect(box!.y).toBeGreaterThanOrEqual(0);
		expect(box!.y + box!.height).toBeLessThanOrEqual(height + 1);
		await confirm.click();
		await expect(help).toBeHidden();
	});
}

// 키보드만으로 달을 넘기고 날짜를 고를 수 있고, 달 끝에서 넘김 버튼이 사라져도 포커스를 잃지 않는다
test('키보드로 달을 넘기고 날짜를 고를 수 있다', async ({ page }) => {
	const dialog = await openEvidence(page, evidenceCases.exerciseSixMonths);
	const calendar = dialog.locator('[data-day-calendar]');
	const previous = calendar.getByRole('button', { name: '이전 달' });
	await previous.focus();
	for (let step = 0; step < 6; step += 1) await page.keyboard.press('Enter');
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('5월');
	// 첫 달에서 이전 달 버튼은 사라지고, 포커스는 다음 달 버튼으로 옮겨 간다
	await expect(previous).toBeHidden();
	await expect(calendar.getByRole('button', { name: '다음 달' })).toBeFocused();
	await page.keyboard.press('Enter');
	await expect(calendar.locator('[data-calendar-month]')).toHaveText('6월');
	const day = calendar.locator('[data-calendar-day="2026-06-10"]');
	await day.focus();
	await page.keyboard.press('Enter');
	await expect(day).toHaveAttribute('aria-pressed', 'true');
	await expect(calendar.locator('[data-calendar-detail]')).toContainText('6월');
});

// 글자를 200%로 키워도(브라우저 글자 확대) 달력이 창 밖으로 넘치지 않고 날짜 칸끼리 겹치지 않는다
for (const [name, evidence, day] of [
	['수면 한 달', sleepSpo2Month, null],
	['걸음(시간대별 걸음)', evidenceCases.month, '2026-10-09'],
	['운동 2주(횟수·칼로리·거리)', evidenceCases.exerciseActivityTwoWeeks, null],
	['운동 30일(운동 기록)', evidenceCases.exerciseSessionsMonth, '2026-10-15'],
	['심박(측정 최저·최고)', evidenceCases.heartWeekMeasured, '2026-10-26']
] as const)
	test(`390px 에서 글자를 200%로 키워도 달력이 넘치거나 칸이 겹치지 않는다: ${name}`, async ({
		page
	}) => {
		await page.setViewportSize({ width: 390, height: 844 });
		const dialog = await openEvidence(page, [...evidence]);
		await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
		const calendar = dialog.locator('[data-day-calendar]');
		await (
			day
				? calendar.locator(`[data-calendar-day="${day}"]`)
				: calendar.locator('[data-calendar-day]').first()
		).click();
		if (day === '2026-10-09') await expect(calendar.locator('[data-hour]')).toHaveCount(24);
		const wide = await dialog.evaluate((element: HTMLElement) => {
			const right = element.getBoundingClientRect().right;
			// 가로로 넘겨 보는 차트 안에서 화면 밖으로 넘겨진 날은 잘려서 보이지 않는다. 창 밖으로 삐져나온 것이 아니라 세지 않는다.
			// 글자를 키우면 차트가 보던 자리를 지키기도 하고 맨 끝으로 붙기도 해서, 세면 결과가 실행마다 갈린다
			const clipped = (item: HTMLElement) => {
				for (
					let parent = item.parentElement;
					parent && parent !== element;
					parent = parent.parentElement
				) {
					if (
						getComputedStyle(parent).overflowX !== 'visible' &&
						parent.getBoundingClientRect().right <= right + 1
					)
						return true;
				}
				return false;
			};
			return [...element.querySelectorAll<HTMLElement>('*')]
				.filter(
					(item) =>
						!item.closest('.sr-only') &&
						item.getBoundingClientRect().right > right + 1 &&
						!clipped(item)
				)
				.filter(
					(item) =>
						![...item.children].some((child) => child.getBoundingClientRect().right > right + 1)
				)
				.map(
					(item) =>
						`${item.tagName} ${item.getAttribute('class')?.slice(0, 50)} ${item.textContent?.trim().slice(0, 15)}`
				);
		});
		expect(wide).toEqual([]);
		const overlaps = await calendar.evaluate((root) => {
			// 칸 상자뿐 아니라 날짜 숫자 글자끼리도 겹치지 않아야 한다
			const cells = [...root.querySelectorAll('ol > li span:first-child')].map((cell) =>
				cell.getBoundingClientRect()
			);
			let count = 0;
			for (let index = 1; index < cells.length; index += 1) {
				const [a, b] = [cells[index - 1], cells[index]];
				if (Math.abs(a.top - b.top) < 1 && a.right > b.left + 1) count += 1;
			}
			return count;
		});
		expect(overlaps).toBe(0);
	});

// 차트가 창 맨 위 진행 막대 밑을 지나갈 때 차트 안 흐림·막대·팝업이 진행 막대를 덮지 않는다(S15P31A403-438)
for (const [name, evidence, selector] of [
	['걸음 일별 막대', evidenceCases.month, '[data-bar]'],
	['수면 단계 막대', evidenceCases.sleepFiveWeeks, '[data-sleep-stage-bars] [data-stage-segment]']
] as const) {
	test(`${name} 차트를 내려 진행 막대 밑에 두고 올려 봐도 진행 막대가 끊기지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width: 1280, height: 720 });
		const dialog = await openEvidence(page, [...evidence]);
		const progress = dialog.locator('[data-scroll-progress]');
		// 차트 안 막대 하나가 진행 막대 바로 밑에 오게 내리고 그 막대에 마우스를 올린다
		const target = dialog.locator(selector).first();
		await target.scrollIntoViewIfNeeded();
		await dialog.evaluate(
			(element, box) => {
				element.scrollTop += box - element.getBoundingClientRect().top - 2;
			},
			(await target.boundingBox())!.y
		);
		await target.hover({ force: true });
		const covered = await progress.evaluate((bar) => {
			bar.style.pointerEvents = 'auto';
			const rect = bar.getBoundingClientRect();
			const misses: string[] = [];
			// 양 끝 24px 은 창의 둥근 모서리(rounded-3xl)가 잘라 내는 자리라 뺀다
			for (let x = rect.left + 26; x < rect.right - 26; x += 8) {
				const top = document.elementFromPoint(x, rect.top + rect.height / 2);
				if (!top || !bar.contains(top))
					misses.push(
						`${Math.round(x)}: ${top?.tagName}.${top?.getAttribute('class')?.slice(0, 40)}`
					);
			}
			bar.style.pointerEvents = '';
			return misses;
		});
		expect(covered).toEqual([]);
	});
}

// ── 438 MR 전 점검 ──

// 시간대별 걸음: 앞 날 응답이 늦게 와도 새로 고른 날 화면을 덮지 않는다
test('시간대별 걸음은 응답이 늦게 도착해도 지금 고른 날 값을 보여 준다', async ({ page }) => {
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	const dialog = await openEvidence(page, evidenceCases.month, async () => {
		await held;
		return false;
	});
	const calendar = dialog.locator('[data-day-calendar]');
	const hourly = calendar.locator('[data-hourly-steps]');
	// 그 달 응답이 오기 전에 날짜를 바꾼다
	await calendar.locator('[data-calendar-day="2026-10-03"]').click();
	await calendar.locator('[data-calendar-day="2026-10-05"]').click();
	await expect(hourly).toHaveAttribute('data-hourly-status', 'loading');
	release();
	await expect(hourly).toContainText('1,550걸음');
	await page.waitForTimeout(300);
	await expect(hourly).toContainText('1,550걸음');
	await expect(hourly).not.toContainText('1,530걸음');
});

// S15P31A403-439 날짜를 아주 빠르게 이리저리 눌러도 시간대별 걸음이 깜빡이지 않는다
test('활동 달력에서 날짜를 빠르게 이리저리 눌러도 시간대별 걸음 상자가 깜빡이거나 높이가 바뀌지 않는다', async ({
	page
}) => {
	let requests = 0;
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	const dialog = await openEvidence(page, evidenceCases.month, async () => {
		requests += 1;
		await held;
		return false;
	});
	const calendar = dialog.locator('[data-day-calendar]');
	const hourly = calendar.locator('[data-hourly-steps]');
	const height = async () => Math.round((await hourly.boundingBox())!.height);
	// 불러오는 중의 높이가 값이 온 뒤, 기록 없는 날과 같다
	await expect(hourly).toHaveAttribute('data-hourly-status', 'loading');
	const loadingHeight = await height();
	release();
	await expect(hourly).toHaveAttribute('data-hourly-status', 'ready');
	expect(await height()).toBe(loadingHeight);
	await calendar.locator('[data-calendar-day="2026-10-10"]').click();
	await expect(hourly).toContainText('이날은 시간대별 걸음 기록이 없어요.');
	expect(await height()).toBe(loadingHeight);

	// 화면이 그려질 때마다 상태와 높이를 적으며 60번을 쉬지 않고 누른다
	const seen = await calendar.evaluate(async (root) => {
		const box = root.querySelector('[data-hourly-steps]')!;
		const days = [...root.querySelectorAll<HTMLButtonElement>('[data-calendar-day]')].filter(
			(day) => !day.disabled
		);
		const statuses = new Set<string>();
		const heights = new Set<number>();
		let running = true;
		const sample = () => {
			statuses.add(box.getAttribute('data-hourly-status') ?? '');
			heights.add(Math.round(box.getBoundingClientRect().height));
			if (running) requestAnimationFrame(sample);
		};
		requestAnimationFrame(sample);
		for (let index = 0; index < 60; index += 1) {
			days[(index * 7 + 3) % days.length].click();
			await new Promise((resolve) => setTimeout(resolve, 20));
		}
		await new Promise((resolve) => setTimeout(resolve, 300));
		running = false;
		return { statuses: [...statuses], heights: [...heights], days: days.length };
	});
	expect(seen.days).toBeGreaterThan(20);
	expect(seen.statuses).toEqual(['ready']);
	expect(seen.heights).toEqual([loadingHeight]);
	// 한 달은 한 번만 부른다
	expect(requests).toBe(1);
});

// S15P31A403-439 시간대별 걸음 막대에도 일별 막대 차트처럼 팝업을 띄운다
test('시간대별 걸음 막대에 마우스를 올리거나 누르면 그 시간의 걸음을 팝업으로 보여 준다', async ({
	page
}) => {
	const dialog = await openEvidence(page, evidenceCases.month);
	const calendar = dialog.locator('[data-day-calendar]');
	const hourly = calendar.locator('[data-hourly-steps]');
	const popup = hourly.locator('[data-hourly-popup]');
	await calendar.locator('[data-calendar-day="2026-10-05"]').click();
	await expect(hourly.locator('[data-hour]')).toHaveCount(24);
	await expect(popup).toHaveCount(0);
	// 마우스: 올리면 뜨고 벗어나면 닫힌다. 0걸음인 시간도 칸 전체가 반응한다
	await hourly.locator('[data-hour="18"]').hover();
	await expect(popup).toContainText('1,550걸음');
	await expect(popup).toContainText('18시');
	await hourly.locator('[data-hour="3"]').hover();
	await expect(popup).toContainText('0걸음');
	await expect(popup).toContainText('3시');
	await hourly.locator('h4').hover();
	await expect(popup).toHaveCount(0);
	// 터치: 누르면 뜨고, 손을 떼도 남아 있다가 바깥을 누르면 닫힌다
	const noon = hourly.locator('[data-hour="12"]');
	await noon.dispatchEvent('pointerenter', { pointerType: 'touch' });
	await noon.dispatchEvent('pointerleave', { pointerType: 'touch' });
	await expect(popup).toContainText('699걸음');
	await expect(popup).toContainText('12시');
	await hourly.locator('h4').dispatchEvent('pointerdown', { pointerType: 'touch' });
	await expect(popup).toHaveCount(0);
	// 날짜를 바꾸면 닫힌다(키보드로 바꿔 바깥 누르기와 겹치지 않게 본다)
	await noon.dispatchEvent('pointerenter', { pointerType: 'touch' });
	await expect(popup).toBeVisible();
	await calendar.locator('[data-calendar-day="2026-10-06"]').focus();
	await page.keyboard.press('Enter');
	await expect(hourly).toContainText('1,560걸음');
	await expect(popup).toHaveCount(0);
});

for (const width of [320, 390, 768, 1280]) {
	test(`${width}px 시간대별 걸음 팝업은 어느 시간에서도 상자 밖으로 나가지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		const dialog = await openEvidence(page, evidenceCases.month);
		const calendar = dialog.locator('[data-day-calendar]');
		const hourly = calendar.locator('[data-hourly-steps]');
		await calendar.locator('[data-calendar-day="2026-10-05"]').click();
		await expect(hourly.locator('[data-hour]')).toHaveCount(24);
		const outside: string[] = [];
		for (let hour = 0; hour < 24; hour += 1) {
			await hourly.locator(`[data-hour="${hour}"]`).hover();
			await expect(hourly.locator('[data-hourly-popup]')).toContainText(`${hour}시`);
			const where = await hourly.evaluate((root: HTMLElement) => {
				const box = root.getBoundingClientRect();
				const popup = root.querySelector('[data-hourly-popup]')!.getBoundingClientRect();
				const cell = root.querySelector('[data-hourly-active]')!.getBoundingClientRect();
				const found: string[] = [];
				if (popup.left < box.left - 0.5 || popup.right > box.right + 0.5) found.push('가로로 나감');
				if (popup.top < box.top - 0.5) found.push('위로 나감');
				// 팝업이 가리키는 막대 위에 있다
				if (popup.right < cell.left || popup.left > cell.right) found.push('막대와 어긋남');
				return found;
			});
			if (where.length > 0) outside.push(`${hour}시 ${where.join(', ')}`);
		}
		expect(outside).toEqual([]);
	});
}

// 복사: 대화 목록에서 시작한 선택도 캐릭터를 빼고, 질문 입력칸 안 복사는 건드리지 않는다
test('대화 목록부터 답변까지 선택해 복사해도 캐릭터가 빠지고, 입력칸 복사는 그대로다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1366, height: 900 });
	const dialog = await openEvidence(page, evidenceCases.month);
	await dialog.getByRole('button', { name: '닫기' }).first().click();
	const wide = await page.evaluate(() => {
		const start = document.querySelector('h1')!;
		const end = document.querySelector('article')!;
		const range = document.createRange();
		range.setStartBefore(start);
		range.setEndAfter(end);
		const selection = window.getSelection()!;
		selection.removeAllRanges();
		selection.addRange(range);
		const clipboardData = new DataTransfer();
		const event = new ClipboardEvent('copy', { clipboardData, bubbles: true, cancelable: true });
		start.dispatchEvent(event);
		return { html: clipboardData.getData('text/html'), prevented: event.defaultPrevented };
	});
	expect(wide.prevented).toBe(true);
	expect(wide.html).not.toContain('<img');

	const input = page.getByRole('textbox').first();
	await input.fill('복사할 질문');
	await input.selectText();
	const inside = await input.evaluate((element) => {
		const clipboardData = new DataTransfer();
		const event = new ClipboardEvent('copy', { clipboardData, bubbles: true, cancelable: true });
		element.dispatchEvent(event);
		return { prevented: event.defaultPrevented, html: clipboardData.getData('text/html') };
	});
	expect(inside).toEqual({ prevented: false, html: '' });
});

// 차트를 자기 층(isolate)에 가둔 뒤에도 막대 팝업이 아래 달력·다른 내용에 가려지지 않는다
for (const width of [390, 1366]) {
	test(`${width}px 모든 막대의 팝업이 다른 내용에 가려지지 않는다`, async ({ page }) => {
		test.setTimeout(120_000);
		await page.setViewportSize({ width, height: 900 });
		const dialog = await openEvidence(page, evidenceCases.month);
		const figure = dialog.locator('figure').first();
		await figure
			.getByRole('button', { name: /한 달|기간 전체/ })
			.first()
			.click()
			.catch(() => {});
		const bars = figure.locator('[data-bar]');
		const count = await bars.count();
		for (let index = 0; index < count; index += 1) {
			const bar = bars.nth(index);
			if (!(await bar.isVisible())) continue;
			await bar.hover({ force: true });
			const popup = figure.locator('[data-bar-popup]');
			if ((await popup.count()) === 0) continue;
			const hidden = await popup.first().evaluate((element) => {
				element.style.pointerEvents = 'auto';
				const rect = element.getBoundingClientRect();
				const points = [
					[rect.left + 4, rect.top + 4],
					[rect.right - 4, rect.bottom - 4],
					[(rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2]
				];
				const misses = points.filter(([x, y]) => {
					if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return false;
					const top = document.elementFromPoint(x, y);
					return !top || !element.contains(top);
				});
				element.style.pointerEvents = '';
				return misses.length;
			});
			expect(hidden, `${index}번째 막대`).toBe(0);
		}
	});
}
