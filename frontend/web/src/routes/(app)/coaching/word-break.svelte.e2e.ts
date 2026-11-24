import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { coachingEvents, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/** 휴대폰에서 한국어가 단어 중간에서 줄바꿈되지 않게 (S15P31A403-240). 합성 값이다 */
test.beforeEach(({ page }) => mockSignedIn(page));

const ANSWER =
	'수면과 활동량은 이전 7일보다 모두 늘었어요.\n다만 변화의 원인을 단정하기는 어려우니, 두 기록을 함께 보면서 생활 패턴을 확인해 보세요.';
// 띄어쓰기 없이 긴 질문도 말풍선을 뚫고 나가지 않아야 한다
const LONG_QUESTION = '지난주보다걸음이얼마나늘었는지날짜별로자세하게비교해서알려줄수있어요';

for (const width of [320, 390]) {
	test(`${width}px에서 코칭 글은 단어 단위로 줄바꿈하고 화면을 넘지 않는다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 800 });
		await page.goto('/coaching', { waitUntil: 'networkidle' });
		const intro = page.getByText('질문 예시를 누르면 바로 질문하고');
		await expect(intro).toHaveCSS('word-break', 'keep-all');

		await mockCoachingServer(page, (question, { send }) => {
			const events = coachingEvents(question);
			send(events.accepted());
			send(events.evidence());
			send(events.completed(ANSWER));
		});
		await page.getByRole('textbox', { name: '질문 입력' }).fill(LONG_QUESTION);
		await page.getByRole('button', { name: '질문 보내기' }).click();

		const log = page.getByRole('log', { name: '대화 내용' });
		await expect(log.getByText('두 기록을 함께 보면서')).toHaveCSS('word-break', 'keep-all');
		// 휴대폰은 짧은 안내 문구를 쓴다
		await expect(page.getByText('AI 코치는 참고용이며')).toHaveCSS('word-break', 'keep-all');

		const bubble = log.getByText(LONG_QUESTION);
		await expect(bubble).toHaveCSS('overflow-wrap', 'break-word');
		const sizes = await bubble.evaluate((element) => ({
			right: element.getBoundingClientRect().right,
			page: document.documentElement.scrollWidth,
			viewport: window.innerWidth
		}));
		expect(sizes.right).toBeLessThanOrEqual(sizes.viewport);
		expect(sizes.page).toBeLessThanOrEqual(sizes.viewport);
	});
}
