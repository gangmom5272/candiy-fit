import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { coachingEvents, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/**
 * 답변 읽기 폭(S15P31A403-326, 멘토링 피드백). 넓은 화면에서도 답변 한 줄이 화면 끝까지 늘어나지 않게
 * 대화를 가운데 64rem 열에 두고(한국어 한 줄 약 55~60자), 입력창도 같은 열에 맞춘다. 합성 값이다.
 */
test.beforeEach(({ page }) => mockSignedIn(page));

const ANSWER = `${'수면 시간은 지난주와 비슷하고 걸음 수는 조금 늘었어요. '.repeat(6)}\n\n${'저녁 산책을 이어 가면 좋아요. '.repeat(8)}`;

async function ask(page: import('@playwright/test').Page) {
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.completed(ANSWER));
	});
	await page.getByRole('textbox', { name: '질문 입력' }).fill('지난주와 비교해 줘');
	await page.getByRole('button', { name: '질문 보내기' }).click();
	const body = page
		.getByRole('log', { name: '대화 내용' })
		.getByText('저녁 산책을 이어 가면', { exact: false });
	await expect(body).toBeVisible();
	return body;
}

for (const width of [1366, 1920]) {
	test(`${width}px 답변 한 줄은 약 60자 이하이고 대화·입력창은 같은 가운데 열이다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 900 });
		const body = await ask(page);
		const sizes = await body.evaluate((element) => {
			const log = element.closest('[role="log"]')!.getBoundingClientRect();
			const text = element.getBoundingClientRect();
			const fontSize = parseFloat(getComputedStyle(element).fontSize);
			return { text: text.width, fontSize, logCenter: log.left + log.width / 2 };
		});
		// 한글 한 글자는 대략 글자 크기 1em 폭이다. 본문 폭이 62em 이하면 한 줄 60자 안팎이다(예전에는 화면 끝까지 늘어났다)
		expect(sizes.text / sizes.fontSize).toBeLessThanOrEqual(62);
		expect(sizes.text / sizes.fontSize).toBeGreaterThanOrEqual(50);
		const column = await page.evaluate(() => {
			const content = document.querySelector('[role="log"] > div')!.getBoundingClientRect();
			const form = document.querySelector('form')!.getBoundingClientRect();
			return { content, form };
		});
		// 대화 열과 입력창이 같은 폭·같은 위치(가운데)다
		expect(Math.abs(column.content.left - column.form.left)).toBeLessThanOrEqual(1);
		expect(Math.abs(column.content.width - column.form.width)).toBeLessThanOrEqual(1);
		expect(
			Math.abs(column.content.left + column.content.width / 2 - sizes.logCenter)
		).toBeLessThanOrEqual(8);
	});
}

test('390px 휴대폰 폭에서는 대화가 원래처럼 화면 폭을 쓴다', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 800 });
	await ask(page);
	const widths = await page.evaluate(() => ({
		content: document.querySelector('[role="log"] > div')!.getBoundingClientRect().width,
		log: document.querySelector('[role="log"]')!.clientWidth
	}));
	// 로그 안쪽 여백(px-4 = 좌우 32px)만큼 작고 그 외에는 줄지 않는다
	expect(widths.log - widths.content).toBeLessThanOrEqual(33);
});
