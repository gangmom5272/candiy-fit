import { expect, test, type Page } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import {
	CONVERSATION_ID,
	REQUEST_ID,
	coachingEvents,
	mockCoachingServer
} from '../../../lib/testing/e2e-coaching';

test.beforeEach(({ page }) => mockSignedIn(page));

const NOW_ISO = '2026-10-27T01:00:00Z';
const LONG_ANSWER = Array.from({ length: 6 }, (_, i) => `${i + 1}번째 줄 설명입니다.`).join('\n');

/** 지난 메시지 n쌍(질문·완료 답변)이 있는 대화. 화면보다 길게 만든다 */
async function mockHistory(page: Page, pairs: number) {
	await page.route(`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages**`, (route) => {
		const items = Array.from({ length: pairs }, (_, i) => {
			const requestId = `44444444-4444-4444-8444-${String(i).padStart(12, '0')}`;
			const base = {
				requestId,
				requestStatus: 'completed',
				errorCode: null,
				createdAt: NOW_ISO,
				evidence: [],
				errorContext: null
			};
			return [
				{
					...base,
					id: `u${i}`,
					role: 'user',
					sequenceNo: i * 2 + 1,
					content: `지난 질문 ${i + 1}`
				},
				{
					...base,
					id: `a${i}`,
					role: 'assistant',
					sequenceNo: i * 2 + 2,
					content: `지난 답변 ${i + 1}\n${LONG_ANSWER}`
				}
			];
		}).flat();
		return route.fulfill({
			json: {
				data: {
					conversation: {
						id: CONVERSATION_ID,
						title: '긴 대화',
						summary: null,
						createdAt: NOW_ISO,
						updatedAt: NOW_ISO
					},
					items,
					nextBeforeSequenceNo: null
				}
			}
		});
	});
}

/** 질문 하나를 받아 스트리밍하다가 release 뒤 근거와 함께 완료하는 가짜 서버 */
async function mockStreamingAnswer(page: Page) {
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	await mockCoachingServer(page, async (question, { send }) => {
		const events = coachingEvents(question, REQUEST_ID);
		send(events.accepted());
		send(events.delta('첫 부분 답변\n'));
		await held;
		send(events.delta(LONG_ANSWER));
		send(events.evidence());
		send(events.completed(`첫 부분 답변\n${LONG_ANSWER}`));
	});
	return () => release();
}

/** 실제 사용자처럼 마우스 휠로 위로 올린다. 코드로 바꾼 위치는 사용자가 올린 것으로 보지 않는다 */
async function wheelUp(page: Page, target: ReturnType<Page['getByRole']>) {
	await target.hover();
	for (let i = 0; i < 6; i += 1) await page.mouse.wheel(0, -400);
	await page.waitForTimeout(500);
}

async function ask(page: Page, content: string) {
	await page.getByRole('textbox', { name: '질문 입력' }).fill(content);
	await page.getByRole('button', { name: '질문 보내기' }).click();
}

for (const [device, viewport] of [
	['데스크톱', { width: 1440, height: 900 }],
	['모바일', { width: 375, height: 812 }]
] as const) {
	test(`${device}: 긴 대화를 열면 최신 메시지가 보이고, 답변이 끝나면 근거 카드까지 따라간다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		const release = await mockStreamingAnswer(page);
		await mockHistory(page, 8);
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

		const log = page.getByRole('log', { name: '대화 내용' });
		await expect(log.getByText('지난 답변 8', { exact: true })).toBeInViewport();
		await expect(log.getByText('지난 질문 1', { exact: true })).not.toBeInViewport();

		await ask(page, '이번 주는 어땠어?');
		await expect(log.getByText('첫 부분 답변', { exact: true })).toBeInViewport();

		release();
		const evidenceCard = log.getByRole('region', { name: '이 답변에서 사용한 기록' });
		await expect(evidenceCard).toBeVisible();
		await expect(evidenceCard).toBeInViewport();
	});
}

test('위로 올려 지난 내용을 읽는 중에는 새 답변이 와도 끌어내리지 않고, 다시 질문하면 내려간다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const release = await mockStreamingAnswer(page);
	await mockHistory(page, 8);
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByText('지난 답변 8', { exact: true })).toBeInViewport();

	await ask(page, '이번 주는 어땠어?');
	await expect(log.getByText('첫 부분 답변', { exact: true })).toBeInViewport();
	await wheelUp(page, log);
	await expect(log.getByText('첫 부분 답변', { exact: true })).not.toBeInViewport();
	const readingTop = await log.evaluate((element) => element.scrollTop);

	release();
	await expect(log.getByRole('region', { name: '이 답변에서 사용한 기록' })).toBeVisible();
	await expect(log.getByRole('region', { name: '이 답변에서 사용한 기록' })).not.toBeInViewport();
	expect(await log.evaluate((element) => element.scrollTop)).toBe(readingTop);
});

for (const [device, viewport] of [
	['데스크톱', { width: 1440, height: 900 }],
	['모바일', { width: 375, height: 812 }]
] as const) {
	test(`${device}: 위로 올려 읽다가 질문하면 생성 중인 최신 답변으로 부드럽게 내려간다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await mockStreamingAnswer(page);
		await mockHistory(page, 8);
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
		const log = page.getByRole('log', { name: '대화 내용' });
		await expect(log.getByText('지난 답변 8', { exact: true })).toBeInViewport();

		await wheelUp(page, device === '데스크톱' ? log : page.locator('main'));
		await expect(log.getByText('지난 답변 8', { exact: true })).not.toBeInViewport();

		// 한 번에 건너뛰지 않고 중간 위치를 거쳐 내려가는지 본다
		await page.evaluate(() => {
			const positions: number[] = [];
			(window as unknown as { positions: number[] }).positions = positions;
			const logElement = document.querySelector('[role="log"]')!;
			const record = () => positions.push(logElement.scrollTop + window.scrollY);
			logElement.addEventListener('scroll', record);
			window.addEventListener('scroll', record);
		});
		await ask(page, '이번 주는 어땠어?');
		await expect(log.getByText('이번 주는 어땠어?', { exact: true })).toBeInViewport();
		await expect(log.getByText('첫 부분 답변', { exact: true })).toBeInViewport();
		const positions = await page.evaluate(
			() => (window as unknown as { positions: number[] }).positions
		);
		expect(new Set(positions).size).toBeGreaterThan(2);
	});
}

test('사용자가 올리지 않았는데 위치가 올라가도(레이아웃 변화) 따라가기를 풀지 않는다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	const release = await mockStreamingAnswer(page);
	await mockHistory(page, 8);
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	const log = page.getByRole('log', { name: '대화 내용' });

	await ask(page, '이번 주는 어땠어?');
	await expect(log.getByText('첫 부분 답변', { exact: true })).toBeInViewport();
	// 휠·터치·키 입력 없이 위치만 올라간 상황
	await log.evaluate((element) => element.scrollBy({ top: -300 }));

	release();
	await expect(log.getByRole('region', { name: '이 답변에서 사용한 기록' })).toBeInViewport();
});

test('실패한 답변에서 다시 질문하기를 여러 번 눌러도 최신 오류 안내가 보이는 맨 아래에 머문다', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	let count = 0;
	await mockCoachingServer(page, async (question, { send }) => {
		count += 1;
		const events = coachingEvents(
			question,
			`${REQUEST_ID.slice(0, -2)}${String(count).padStart(2, '0')}`
		);
		send(events.accepted());
		// 실제 서버처럼 접수 뒤 건강 조회를 거쳐 실패가 온다
		await new Promise((resolve) => setTimeout(resolve, 1200));
		send(
			events.error(
				'HEALTH_DATA_UNAVAILABLE',
				'건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.',
				true
			)
		);
	});
	await mockHistory(page, 8);
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	const log = page.getByRole('log', { name: '대화 내용' });
	const distanceFromBottom = () =>
		log.evaluate((element) => element.scrollHeight - element.scrollTop - element.clientHeight);

	await ask(page, '이번 주는 어땠어?');
	for (let i = 1; i <= 4; i += 1) {
		const retry = log.getByRole('button', { name: '다시 질문하기' }).last();
		await expect(retry).toBeVisible();
		await retry.click();
		await expect(log.getByRole('button', { name: '다시 질문하기' })).toHaveCount(i + 1);
		await expect(log.getByRole('alert').last()).toBeInViewport();
		await expect.poll(distanceFromBottom).toBeLessThan(2);
	}
});

// 278: 휴대폰(768px 미만)에는 공통 하단 메뉴가 화면 아래에 고정돼 있다. 입력창이 그 위에 붙어 가려지지 않아야 한다
for (const viewport of [
	{ width: 767, height: 1024 },
	{ width: 375, height: 812 }
]) {
	test(`${viewport.width}px: 긴 대화를 스크롤해도 입력창·안내문이 하단 메뉴에 가려지지 않는다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await mockStreamingAnswer(page);
		await mockHistory(page, 8);
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

		const navigation = page.getByRole('navigation', { name: '주 메뉴' });
		const send = page.getByRole('button', { name: '질문 보내기' });
		// 좁은 화면은 짧은 문구("AI 코치는 참고용이며"), 넓은 화면은 전체 문구
		const notice = page.getByText(/AI 코치는 (생활 습관 )?참고용이며/).locator('visible=true');
		for (const position of [0, 0.5]) {
			await page.evaluate(
				(ratio) => scrollTo(0, document.documentElement.scrollHeight * ratio),
				position
			);
			await page.waitForTimeout(100);
			const nav = (await navigation.boundingBox())!;
			for (const target of [send, notice]) {
				const box = (await target.boundingBox())!;
				expect(box.y + box.height).toBeLessThanOrEqual(nav.y + 0.5);
			}
		}
		// 보내기 버튼 가운데를 누르면 하단 메뉴가 아니라 버튼이 눌린다
		const box = (await send.boundingBox())!;
		const hit = await page.evaluate(
			([x, y]) =>
				document.elementFromPoint(x, y)?.closest('button')?.getAttribute('aria-label') ?? null,
			[box.x + box.width / 2, box.y + box.height - 2]
		);
		expect(hit).toBe('질문 보내기');
	});
}

// 278: 데스크톱에서 오프라인 안내가 떠도 문서 스크롤이 생기지 않고 대화 내용만 스크롤된다
test('데스크톱: 오프라인 안내가 떠도 문서 스크롤이 생기지 않는다', async ({ page, context }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await mockStreamingAnswer(page);
	await mockHistory(page, 8);
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

	await context.setOffline(true);
	await expect(page.getByRole('status', { name: '네트워크 연결 상태' })).toBeVisible();
	const overflow = () => page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
	await expect.poll(overflow).toBeLessThanOrEqual(0);
	await expect(page.getByRole('button', { name: '질문 보내기' })).toBeInViewport({ ratio: 1 });
	await context.setOffline(false);
});

// 283: 휴대폰·태블릿도 데스크톱처럼 페이지는 고정하고 대화 상자 안에서만 스크롤한다.
// 머리말(목록 버튼·제목)과 입력창은 항상 보인다
for (const viewport of [
	{ width: 360, height: 640 },
	{ width: 375, height: 812 },
	{ width: 768, height: 1024 },
	{ width: 1024, height: 768 }
]) {
	test(`${viewport.width}x${viewport.height}: 페이지는 고정하고 대화 상자 안에서만 스크롤한다`, async ({
		page
	}) => {
		await page.setViewportSize(viewport);
		await mockStreamingAnswer(page);
		await mockHistory(page, 8);
		await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

		const log = page.getByRole('log', { name: '대화 내용' });
		// 처음에는 대화 상자 안 맨 아래(최신 메시지)에 있다
		await expect
			.poll(() => log.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight))
			.toBeLessThan(2);
		expect(
			await page.evaluate(() => document.documentElement.scrollHeight - innerHeight)
		).toBeLessThanOrEqual(0);
		expect(await log.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);

		// 안에서 위로 올려도 머리말(데스크톱)·떠 있는 목록 버튼(좁은 화면)·입력창은 그대로 보인다
		await log.hover();
		for (let i = 0; i < 6; i += 1) await page.mouse.wheel(0, -500);
		await expect(log.getByText('지난 질문 1', { exact: true })).toBeInViewport();
		await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeInViewport();
		if (viewport.width >= 1024)
			await expect(page.getByRole('heading', { level: 1, name: 'AI 코치' })).toBeInViewport();
		else
			await expect(page.getByRole('button', { name: '대화 목록 열기' }).first()).toBeInViewport();
	});
}
