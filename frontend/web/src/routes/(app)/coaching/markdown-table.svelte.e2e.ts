import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { coachingEvents, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/** AI 답변 표 (S15P31A403-236). 합성 값이다 */
test.beforeEach(({ page }) => mockSignedIn(page));

const HEADER =
	'| 지표 | 10월 13일 주 | 10월 20일 주 | 차이 | 평소 범위 | 메모 |\n| --- | --- | --- | --- | --- | --- |\n';
const ROWS = [
	'| 평균 걸음 | 8,120걸음 | 9,430걸음 | +1,310걸음 | 7,000~10,000걸음 | 주말 산책이 늘었어요 |',
	'| 평균 수면 | 6시간 40분 | 6시간 55분 | +15분 | 6시간 30분~7시간 30분 | 잠든 시각은 비슷해요 |'
];
const ANSWER = `${HEADER}${ROWS.join('\n')}\n\n수면도 조금 늘었어요.`;

test('휴대폰 폭에서 스트리밍 중 덜 들어온 표 줄은 숨기고, 넓은 표는 표 상자만 가로로 밀린다', async ({
	page
}) => {
	await page.setViewportSize({ width: 375, height: 800 });
	let step: () => void = () => {};
	const next = () => new Promise<void>((resolve) => (step = resolve));
	let waiting = next();
	await mockCoachingServer(page, async (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		// 표로 시작하는 답변. 머리줄 앞부분만 들어온 상태
		send(events.delta('| 지표 | 10월 13일'));
		await waiting;
		send(events.delta(HEADER.slice('| 지표 | 10월 13일'.length) + ROWS[0].slice(0, 20)));
		await waiting;
		send(events.delta(ROWS[0].slice(20) + '\n' + ROWS[1] + '\n\n수면도 조금 늘었어요.'));
		send(events.completed(ANSWER));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill('지난 두 주 비교해 줘');
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const log = page.getByRole('log', { name: '대화 내용' });
	const answer = log.locator('article').last();
	// 입력 중 표시(커서 "|", aria-hidden)를 뺀 답변 글자
	const answerText = () =>
		answer.evaluate((element) => {
			const copy = element.cloneNode(true) as HTMLElement;
			copy.querySelectorAll('[aria-hidden="true"]').forEach((hidden) => hidden.remove());
			return copy.textContent ?? '';
		});
	await expect(answer.getByRole('status')).toBeVisible();
	// 머리줄만 온 상태: 기호째 보이지도, 큰 요약 줄로 보이지도 않는다
	await expect.poll(answerText).not.toContain('|');
	expect(await answerText()).not.toContain('지표');

	step();
	waiting = next();
	// 구분선까지 온 표는 그리되, 칸이 덜 찬 행은 아직 숨긴다
	await expect(answer.getByRole('table')).toBeVisible();
	await expect(answer.getByRole('row')).toHaveCount(1);
	expect(await answerText()).not.toContain('|');
	expect(await answerText()).not.toContain('8,120');

	step();
	await expect(answer.getByRole('status')).toHaveCount(0);
	await expect(answer.getByRole('rowheader')).toHaveText(['평균 걸음', '평균 수면']);
	await expect(answer).toContainText('수면도 조금 늘었어요.');
	expect(await answerText()).not.toContain('|');

	// 화면 전체는 가로로 넘치지 않고, 표 상자만 밀린다
	const region = answer.getByRole('region', { name: '비교 표' });
	const sizes = await region.evaluate((element) => ({
		scroll: element.scrollWidth,
		client: element.clientWidth,
		page: document.documentElement.scrollWidth,
		viewport: window.innerWidth
	}));
	expect(sizes.scroll).toBeGreaterThan(sizes.client);
	expect(sizes.page).toBeLessThanOrEqual(sizes.viewport);

	// 오른쪽에 열이 더 있으면 가장자리를 흐리게 해 밀어 볼 수 있음을 보여 준다
	await expect(answer.locator('[data-more="right"]')).toBeVisible();
	await expect(answer.locator('[data-more="left"]')).toHaveCount(0);

	// 키보드로도 표 상자를 밀 수 있다
	await region.focus();
	await page.keyboard.press('ArrowRight');
	await expect.poll(() => region.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
	await expect(answer.locator('[data-more="left"]')).toBeVisible();

	// 끝까지 밀면 오른쪽 흐림이 사라진다
	await region.evaluate((element) => element.scrollTo({ left: element.scrollWidth }));
	await expect(answer.locator('[data-more="right"]')).toHaveCount(0);
});

test('완성된 넓은 표가 한 번에 들어와도 처음부터 오른쪽 가장자리가 흐려진다', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 800 });
	await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.completed(ANSWER));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill('지난 두 주 비교해 줘');
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const answer = page.getByRole('log', { name: '대화 내용' }).locator('article').last();
	await expect(answer.getByRole('table')).toBeVisible();
	await expect(answer.locator('[data-more="right"]')).toBeVisible();
	await expect(answer.locator('[data-more="left"]')).toHaveCount(0);
});

test('넓은 화면에서 표가 다 보이면 표 폭만 차지하고 Tab이 표에 멈추지 않는다', async ({ page }) => {
	await page.setViewportSize({ width: 1920, height: 900 });
	const small = '| 날짜 | 걸음 |\n| --- | --- |\n| 10월 24일 (토) | 11,650걸음 |';
	await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.completed(small));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill('비교해 줘');
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const answer = page.getByRole('log', { name: '대화 내용' }).locator('article').last();
	const region = answer.getByRole('region', { name: '비교 표' });
	await expect(answer.getByRole('table')).toBeVisible();
	await expect(answer.locator('[data-more]')).toHaveCount(0);
	await expect(region).not.toHaveAttribute('tabindex');
	// 답변 폭 전체로 늘어나지 않는다
	const [regionWidth, answerWidth] = await Promise.all([
		region.evaluate((element) => element.getBoundingClientRect().width),
		answer.evaluate((element) => element.getBoundingClientRect().width)
	]);
	expect(regionWidth).toBeLessThan(answerWidth / 2);
});
