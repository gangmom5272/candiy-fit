import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import { coachingEvents, mockCoachingServer } from '../../../lib/testing/e2e-coaching';

/** 입력창 안내 문구가 칸에서 잘리지 않게 (S15P31A403-240) */
test.beforeEach(({ page }) => mockSignedIn(page));

const LONG = '건강 기록에 대해 궁금한 점을 입력해 보세요.';
const SHORT = '궁금한 점을 물어보세요.';

/** 안내 문구 글자 폭과 칸 너비 */
function placeholderFit(page: Page) {
	return page.getByRole('textbox', { name: '질문 입력' }).evaluate((element: HTMLInputElement) => {
		const style = getComputedStyle(element);
		const context = document.createElement('canvas').getContext('2d')!;
		context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
		return {
			text: element.placeholder,
			fits: context.measureText(element.placeholder).width <= element.clientWidth,
			ellipsis: style.textOverflow
		};
	});
}

async function openConversation(page: Page) {
	await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.completed('답변이에요.'));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill('질문');
	await page.getByRole('button', { name: '질문 보내기' }).click();
	await page.getByText('답변이에요.').waitFor();
}

for (const width of [360, 375, 414]) {
	test(`휴대폰(${width}px) 대화 화면에서는 짧은 안내 문구가 칸에 다 들어간다`, async ({ page }) => {
		await page.setViewportSize({ width, height: 800 });
		await openConversation(page);
		await expect.poll(() => placeholderFit(page)).toMatchObject({ text: SHORT, fits: true });
	});
}

test('넓은 화면에서는 긴 안내 문구를 그대로 쓰고, 화면을 줄이면 짧은 문구로 바뀐다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1280, height: 800 });
	await openConversation(page);
	await expect.poll(() => placeholderFit(page)).toMatchObject({ text: LONG, fits: true });

	await page.setViewportSize({ width: 375, height: 800 });
	await expect.poll(() => placeholderFit(page)).toMatchObject({ text: SHORT, fits: true });
});

test('첫 화면은 375px에서도 짧은 문구가 칸에 들어간다', async ({ page }) => {
	await page.setViewportSize({ width: 375, height: 800 });
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await expect.poll(() => placeholderFit(page)).toMatchObject({ text: SHORT, fits: true });
});

// 285에서 좁은 화면 여백을 줄여 320px에서도 짧은 문구가 들어간다. 더 좁은 280px로 "…" 처리를 확인한다
test('짧은 문구도 안 들어가는 280px에서는 끝을 "…"으로 줄인다', async ({ page }) => {
	await page.setViewportSize({ width: 280, height: 700 });
	await openConversation(page);
	await expect
		.poll(() => placeholderFit(page))
		.toMatchObject({ text: SHORT, fits: false, ellipsis: 'ellipsis' });
});
