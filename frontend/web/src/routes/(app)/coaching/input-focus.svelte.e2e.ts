import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import {
	CONVERSATION_ID,
	REQUEST_ID,
	coachingEvents,
	mockCoachingServer
} from '../../../lib/testing/e2e-coaching';

const FIRST = '지난주보다 얼마나 더 걸었어?';
const SECOND = '잠은 충분히 잤어?';

test.beforeEach(({ page }) => mockSignedIn(page));

/** 질문마다 답변을 잡아 두었다가 release()로 끝낸다. 두 번째 질문은 다른 요청 ID를 쓴다 */
async function mockAnswers(page: Page) {
	const releases: Array<() => void> = [];
	const questions = await mockCoachingServer(page, async (question, { send }) => {
		const index = releases.length;
		const held = new Promise<void>((resolve) => releases.push(resolve));
		const events = coachingEvents(
			question,
			index === 0 ? REQUEST_ID : '44444444-4444-4444-8444-444444444444'
		);
		send(events.accepted());
		send(events.delta(`${index + 1}번째 답변`));
		await held;
		send(events.completed(`${index + 1}번째 답변`));
	});
	return { questions, release: (index: number) => releases[index]?.() };
}

function input(page: Page) {
	return page.getByRole('textbox', { name: '질문 입력' });
}

test('첫 화면에서 질문하면 대화 화면 입력창이 커서를 이어받고, 답변 뒤 누르지 않고 바로 다음 질문을 보낸다', async ({
	page
}) => {
	const { questions, release } = await mockAnswers(page);
	await page.goto('/coaching', { waitUntil: 'networkidle' });

	await input(page).fill(FIRST);
	await input(page).press('Enter');
	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);

	// 답변을 쓰는 동안에도 커서는 남아 있고 입력은 잠겨 있다
	await expect(input(page)).toBeFocused();
	await expect(input(page)).toBeDisabled();

	release(0);
	await expect(page.getByText('1번째 답변')).toBeVisible();
	await expect(input(page)).toBeEnabled();
	await expect(input(page)).toBeFocused();

	// 클릭 없이 바로 입력하고 보낸다
	await page.keyboard.type(SECOND);
	await page.keyboard.press('Enter');
	await expect.poll(() => questions.map((question) => question.content)).toEqual([FIRST, SECOND]);
	await expect(input(page)).toBeFocused();
});

test('보내기 버튼을 눌러 질문해도 커서는 입력창에 남는다', async ({ page }) => {
	const { release } = await mockAnswers(page);
	await page.goto('/coaching', { waitUntil: 'networkidle' });

	await input(page).fill(FIRST);
	await page.getByRole('button', { name: '질문 보내기' }).click();
	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	release(0);
	await expect(input(page)).toBeEnabled();
	await expect(input(page)).toBeFocused();

	await page.keyboard.type(SECOND);
	await page.getByRole('button', { name: '질문 보내기' }).click();
	await expect(input(page)).toBeFocused();
});

test('답변을 기다리는 동안 다른 곳을 누르면, 답변이 끝나도 커서를 입력창으로 끌어오지 않는다', async ({
	page
}) => {
	const { release } = await mockAnswers(page);
	await page.goto('/coaching', { waitUntil: 'networkidle' });

	await input(page).fill(FIRST);
	await input(page).press('Enter');
	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	await expect(input(page)).toBeFocused();

	await page.getByRole('log', { name: '대화 내용' }).click();
	await expect(input(page)).not.toBeFocused();

	release(0);
	await expect(input(page)).toBeEnabled();
	await expect(input(page)).not.toBeFocused();
});
