import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { coachingEvents, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/**
 * 질문·답변 글자 위계(S15P31A403-300·315, 멘토링 피드백). 앱 AI 코치 탭과 같은 기준이다.
 * 질문 말풍선과 답변 본문은 같은 16px, 답변 첫 줄만 17px 굵게. 둘은 크기가 아니라 말풍선으로 구분한다. 짧은 질문은 말풍선이 글 길이만큼만 차지한다. 합성 값이다.
 */
test.beforeEach(({ page }) => mockSignedIn(page));

const QUESTION = '어제 몇 걸음 걸었어?';
const ANSWER =
	'어제는 평소보다 조금 더 걸었어요.\n\n오후에 걷기가 많았고, 저녁에는 활동이 줄었어요.';

for (const width of [360, 1366]) {
	test(`${width}px에서 질문과 답변 본문은 같은 크기이고 답변 첫 줄만 크고 굵다`, async ({
		page
	}) => {
		await page.setViewportSize({ width, height: 800 });
		await page.goto('/coaching', { waitUntil: 'networkidle' });
		await mockCoachingServer(page, (question, { send }) => {
			const events = coachingEvents(question);
			send(events.accepted());
			send(events.completed(ANSWER));
		});
		await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
		await page.getByRole('button', { name: '질문 보내기' }).click();

		const log = page.getByRole('log', { name: '대화 내용' });
		const question = log.getByText(QUESTION, { exact: true });
		const headline = log.getByText('어제는 평소보다 조금 더 걸었어요.', { exact: true });
		const body = log.getByText('오후에 걷기가 많았고', { exact: false });
		await expect(headline).toBeVisible();

		await expect(question).toHaveCSS('font-size', '16px');
		await expect(body).toHaveCSS('font-size', '16px');
		await expect(headline).toHaveCSS('font-size', '17px');
		await expect(headline).toHaveCSS('font-weight', '600');
		// 질문만 말풍선(배경)이 있고 답변은 배경 없이 본문으로 읽힌다
		await expect(question).toHaveCSS('background-color', 'rgb(255, 244, 243)');
		await expect(headline).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');

		// 짧은 질문은 말풍선이 글 길이에 맞춰 줄어든다(예전 최소 폭 19.75rem 없음)
		const widths = await question.evaluate((element) => ({
			bubble: element.getBoundingClientRect().width,
			log: element.closest('[role="log"]')!.getBoundingClientRect().width
		}));
		expect(widths.bubble).toBeLessThan(widths.log * 0.6);
		expect(widths.bubble).toBeLessThan(316);
	});
}
