import { expect, test } from '@playwright/test';
import { mockSignedIn } from '../../../lib/testing/e2e-auth';
import {
	CONVERSATION_ID,
	REQUEST_ID,
	coachingEvents,
	evidence,
	mockCoachingServer
} from '../../../lib/testing/e2e-coaching';

test.beforeEach(({ page }) => mockSignedIn(page));

const QUESTION = '지난주보다 얼마나 더 걸었어?';
const ANSWER = '지난주보다 하루 평균 1,200보 더 걸었어요.\n주말에 걸음이 특히 늘었어요.';

test('첫 화면에서 질문하면 대화 화면으로 넘어가 답변을 스트리밍하고 근거와 함께 완료한다', async ({
	page
}) => {
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	const questions = await mockCoachingServer(page, async (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.status('fetching_health_data'));
		send(events.delta('지난주보다 하루 평균 1,200보 더 걸었어요.'));
		await held;
		send(events.delta('\n주말에 걸음이 특히 늘었어요.'));
		send(events.evidence());
		send(events.completed(ANSWER));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });

	await page.getByRole('textbox', { name: '질문 입력' }).fill(`  ${QUESTION}  `);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByText(QUESTION, { exact: true })).toBeVisible();
	await expect(log.getByRole('status')).toHaveText('건강 기록을 확인하고 있어요');
	await expect(log.getByText('지난주보다 하루 평균 1,200보 더 걸었어요.')).toBeVisible();
	await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeDisabled();

	release();
	await expect(log.getByText('주말에 걸음이 특히 늘었어요.')).toBeVisible();
	await expect(log.getByRole('status')).toHaveCount(0);
	await expect(log.getByRole('region', { name: '이 답변에서 사용한 기록' })).toBeVisible();
	await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeEnabled();
	// 탭 제목에는 질문 문장을 넣지 않는다(방문 기록에 남는다, S15P31A403-439)
	await expect(page).toHaveTitle('AI 코치 | 캔디핏');

	expect(questions).toHaveLength(1);
	expect(questions[0]).toMatchObject({ type: 'question', conversationId: null, content: QUESTION });
	expect(evidence.length).toBeGreaterThan(0);
});

test('질문 예시를 누르면 입력 없이 바로 질문하고 대화 화면으로 넘어간다', async ({ page }) => {
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	const questions = await mockCoachingServer(page, async (question, { send }) => {
		await held;
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.completed(ANSWER));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });

	const example = page.getByRole('button', { name: new RegExp(QUESTION) });
	// 누를 수 있는 카드라는 것을 손가락 커서로 보여 준다
	expect(await example.evaluate((element) => getComputedStyle(element).cursor)).toBe('pointer');

	await example.click();
	// 접수 전에는 카드를 잠가 같은 질문을 두 번 보내지 않는다
	await expect(example).toBeDisabled();
	await expect(page.getByRole('status')).toHaveText('질문을 보내고 있어요');
	release();

	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	await expect(
		page.getByRole('log', { name: '대화 내용' }).getByText(QUESTION, { exact: true })
	).toBeVisible();
	expect(questions).toHaveLength(1);
	expect(questions[0]).toMatchObject({ type: 'question', conversationId: null, content: QUESTION });
});

test('대화 화면의 이어지는 질문은 같은 대화 ID로 보낸다', async ({ page }) => {
	const questions = await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question, `${REQUEST_ID.slice(0, -1)}${questions.length}`);
		send(events.accepted());
		send(events.completed(`답변 ${questions.length}`));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();
	await expect(page.getByText('답변 1', { exact: true })).toBeVisible();

	await page.getByRole('textbox', { name: '질문 입력' }).fill('수면은 어땠어?');
	await page.getByRole('button', { name: '질문 보내기' }).click();

	await expect(page.getByText('답변 2', { exact: true })).toBeVisible();
	expect(questions[1]).toMatchObject({
		conversationId: CONVERSATION_ID,
		content: '수면은 어땠어?'
	});
	expect(questions[1].clientRequestId).not.toBe(questions[0].clientRequestId);
});

test('답변 생성에 실패하면 부분 답변을 남기고 다시 질문할 수 있다', async ({ page }) => {
	const questions = await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question, `${REQUEST_ID.slice(0, -1)}${questions.length}`);
		send(events.accepted());
		if (questions.length === 1) {
			send(events.delta('부분 답변'));
			send(events.error('AI_UNAVAILABLE', '답변을 생성하지 못했습니다.', true));
		} else {
			send(events.completed('다시 받은 답변'));
		}
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByText('부분 답변')).toBeVisible();
	await expect(log.getByRole('alert')).toContainText('답변을 생성하지 못했습니다.');

	await log.getByRole('button', { name: '다시 질문하기' }).click();
	await expect(log.getByText('다시 받은 답변')).toBeVisible();
	expect(questions[1]).toMatchObject({ conversationId: CONVERSATION_ID, content: QUESTION });
	expect(questions[1].clientRequestId).not.toBe(questions[0].clientRequestId);
});

test('접수되지 않은 질문은 첫 화면에서 안내하고 다시 보낼 수 있다', async ({ page }) => {
	const questions = await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question);
		if (questions.length === 1) {
			send(events.error('AI_UNAVAILABLE', '답변을 생성하지 못했습니다.', false));
		} else {
			send(events.accepted());
			send(events.completed('답변'));
		}
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const toast = page.getByRole('alert');
	await expect(toast).toContainText('답변을 생성하지 못했습니다.');
	// 화면을 밀어내지 않는 토스트로 띄운다
	await expect(toast.locator('..')).toHaveCSS('position', 'fixed');
	await expect(page).toHaveURL('/coaching');
	await toast.getByRole('button', { name: '다시 보내기' }).click();

	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	expect(questions).toHaveLength(2);
});

test('첫 화면 오류 토스트는 페이지를 옮기지 않아도 잠시 뒤 사라진다', async ({ page }) => {
	const questions = await mockCoachingServer(page, (question, { send }) => {
		send(
			coachingEvents(question).error('PERSISTENCE_FAILED', '요청을 처리하지 못했습니다.', false)
		);
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const toast = page.getByRole('alert');
	await expect(toast).toContainText('요청을 처리하지 못했습니다.');
	await expect(toast.getByRole('button', { name: '다시 보내기' })).toBeVisible();
	// 다시 보내기 버튼이 있으면 6초 동안 보여 준다
	await expect(toast).toBeHidden({ timeout: 8000 });
	await expect(page).toHaveURL('/coaching');
	expect(questions).toHaveLength(1);
});

test('특정 질문에 붙일 수 없는 오류는 공통 오류로 안내한다', async ({ page }) => {
	await mockCoachingServer(page, (_, { send }) => {
		send({
			type: 'error',
			clientRequestId: null,
			requestId: null,
			conversationId: null,
			eventSeq: 0,
			data: {
				code: 'INVALID_MESSAGE',
				message: '요청 형식을 확인해 주세요.',
				retryable: false,
				accepted: false,
				reason: null,
				metrics: null
			}
		});
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const toast = page.getByRole('alert');
	await expect(toast).toContainText('요청 형식을 확인해 주세요.');
	await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeEnabled();
	await expect(toast).toBeHidden({ timeout: 5000 });
});

test('접수 뒤 연결이 끊기면 상태 조회로 완료된 답변을 복구한다', async ({ page }) => {
	await mockCoachingServer(page, (question, { send, route }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(events.delta('끊기기 전 답변'));
		route.close({ code: 1011 });
	});
	let polls = 0;
	await page.route(`**/api/v1/coaching/requests/${REQUEST_ID}`, (route) => {
		polls += 1;
		return route.fulfill({
			json: {
				data: {
					requestId: REQUEST_ID,
					clientRequestId: '11111111-1111-4111-8111-111111111111',
					conversationId: CONVERSATION_ID,
					status: polls === 1 ? 'generating' : 'completed',
					userMessageId: 'u1',
					assistantMessage: {
						id: 'a1',
						role: 'assistant',
						sequenceNo: 2,
						content: polls === 1 ? '' : '복구한 최종 답변',
						createdAt: '2026-10-26T01:00:00Z'
					},
					evidence: polls === 1 ? [] : evidence,
					errorCode: null,
					createdAt: '2026-10-26T01:00:00Z',
					finishedAt: polls === 1 ? null : '2026-10-26T01:00:05Z',
					errorContext: null
				}
			}
		});
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByText('끊기기 전 답변')).toBeVisible();
	await expect(log.getByText('복구한 최종 답변')).toBeVisible({ timeout: 8000 });
	expect(polls).toBe(2);
});

test('지난 메시지를 불러올 수 없고 진행 중인 질문도 없으면 안내한다', async ({ page }) => {
	await mockCoachingServer(page, () => {});
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

	await expect(page.getByText('이전 대화를 불러오지 못했어요.')).toBeVisible();
	await expect(
		page.getByRole('navigation', { name: '대화 목록' }).getByText('대화 목록을 불러오지 못했어요.')
	).toBeVisible();
});

// 282: 지난 대화·대화 목록을 불러오지 못하면 오류 상자와 다시 불러오기로 다시 읽는다
test('지난 대화·대화 목록 불러오기 실패는 다시 불러오기로 다시 읽는다', async ({ page }) => {
	await mockCoachingServer(page, () => {});
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	const log = page.getByRole('log', { name: '대화 내용' });
	const list = page.getByRole('navigation', { name: '대화 목록' });
	await expect(log.getByRole('alert')).toContainText('이전 대화를 불러오지 못했어요.');

	const NOW = '2026-10-28T03:00:00Z';
	const conversation = {
		id: CONVERSATION_ID,
		title: '다시 읽은 대화',
		summary: null,
		createdAt: NOW,
		updatedAt: NOW
	};
	await page.route(
		(url) => url.pathname.startsWith('/api/v1/coaching/conversations'),
		(route) => {
			const url = new URL(route.request().url());
			if (url.pathname === '/api/v1/coaching/conversations')
				return route.fulfill({
					json: {
						data: { items: [{ ...conversation, lastRequestStatus: 'completed' }], nextCursor: null }
					}
				});
			return route.fulfill({
				json: {
					data: {
						conversation,
						items: [
							{
								id: 'u1',
								requestId: REQUEST_ID,
								role: 'user',
								sequenceNo: 1,
								content: '다시 읽은 질문',
								requestStatus: 'completed',
								errorCode: null,
								createdAt: NOW,
								evidence: [],
								errorContext: null
							}
						],
						nextBeforeSequenceNo: null
					}
				}
			});
		}
	);
	await log.getByRole('button', { name: '다시 불러오기' }).click();
	await expect(log.getByText('다시 읽은 질문')).toBeVisible();
	await expect(log.getByRole('alert')).toHaveCount(0);

	await list.getByRole('button', { name: '다시 불러오기' }).click();
	await expect(list.getByText('다시 읽은 대화')).toBeVisible();
	await expect(list.getByText('대화 목록을 불러오지 못했어요.')).toHaveCount(0);
});

test('다시 보내도 같은 결과인 오류는 다시 보내기·다시 질문하기를 보여 주지 않는다', async ({
	page
}) => {
	const questions = await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question, `${REQUEST_ID.slice(0, -1)}${questions.length}`);
		if (questions.length === 1) {
			// 접수 전 실패(재시도 불가)
			send({
				...events.error('INVALID_QUESTION', '질문 내용을 확인해 주세요.', false),
				data: {
					code: 'INVALID_QUESTION',
					message: '질문 내용을 확인해 주세요.',
					retryable: false,
					accepted: false,
					reason: null,
					metrics: null
				}
			});
			return;
		}
		send(events.accepted());
		send({
			...events.error('HEALTH_DATA_FORBIDDEN', '건강데이터 조회 권한을 확인해 주세요.', true),
			data: {
				code: 'HEALTH_DATA_FORBIDDEN',
				message: '건강데이터 조회 권한을 확인해 주세요.',
				retryable: false,
				accepted: true,
				reason: null,
				metrics: null
			}
		});
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	const input = page.getByRole('textbox', { name: '질문 입력' });

	await input.fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();
	await expect(page.getByRole('alert')).toContainText('질문 내용을 확인해 주세요.');
	await expect(page.getByRole('button', { name: '다시 보내기' })).toHaveCount(0);

	await input.fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();
	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByRole('alert')).toContainText('건강데이터 조회 권한을 확인해 주세요.');
	await expect(log.getByRole('button', { name: '다시 질문하기' })).toHaveCount(0);
});

test('앱 미연동 오류는 연동할 곳을 함께 안내하고 다시 질문할 수 있다', async ({ page }) => {
	await mockCoachingServer(page, (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		send(
			events.error(
				'CANDIY_USER_NOT_LINKED',
				'앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.',
				true
			)
		);
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const alert = page.getByRole('log', { name: '대화 내용' }).getByRole('alert');
	await expect(alert).toContainText('앱 연동을 확인해 주세요.');
	await expect(alert).toContainText(
		'휴대폰의 LiNK 앱에서 로그인한 뒤 건강 기록 연동을 마쳐 주세요.'
	);
	await expect(alert.getByRole('button', { name: '다시 질문하기' })).toBeVisible();
});

test('코칭 서버가 로그인 자격을 거절하면 로그인 화면으로 보낸다', async ({ page }) => {
	await page.route('**/api/v1/coaching/conversations**', (route) =>
		route.fulfill({ status: 404, json: { detail: 'Not Found' } })
	);
	// 웹은 auth 프레임을 보내지 않으므로 서버가 핸드셰이크 쿠키를 보고 바로 거절한다(명세 7.2)
	await page.routeWebSocket('**/api/v1/coaching/ws', (route) => {
		route.send(
			JSON.stringify({
				type: 'connection_error',
				data: { code: 'AUTH_REQUIRED', message: '로그인이 필요합니다.' }
			})
		);
		route.close({ code: 1008 });
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	await expect(page).toHaveURL('/login');
});

test('세션 확인이 일시 장애(AUTH_UNAVAILABLE)면 로그아웃하지 않고 다시 보내기 안내를 보여 준다', async ({
	page
}) => {
	await page.route('**/api/v1/coaching/conversations**', (route) =>
		route.fulfill({ status: 404, json: { detail: 'Not Found' } })
	);
	let connections = 0;
	await page.routeWebSocket('**/api/v1/coaching/ws', (route) => {
		connections++;
		route.send(
			JSON.stringify({
				type: 'connection_error',
				data: { code: 'AUTH_UNAVAILABLE', message: '인증 처리를 일시적으로 이용할 수 없습니다.' }
			})
		);
		route.close({ code: 1013 });
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	// 첫 질문 화면에서는 보내지 못한 질문의 오류가 입력창 쪽 알림으로 나온다
	const alert = page.getByRole('alert');
	await expect(alert).toContainText('인증 처리를 일시적으로 이용할 수 없습니다.');
	await expect(alert.getByRole('button', { name: '다시 보내기' })).toBeVisible();
	await expect(page).not.toHaveURL('/login');
	await expect(page.getByRole('banner')).toBeVisible();
	// 자동으로 다시 연결하지 않는다. 다시 보내는 것은 사용자가 정한다
	expect(connections).toBe(1);
});

test('호환 처리: 연결된 뒤 connection_error AUTH_REQUIRED를 받아도 로그인 화면으로 보낸다', async ({
	page
}) => {
	// v47 계약에서 질문 시점 인증 실패는 요청 단위 error로 온다. 서버가 예전 방식(connection_error 뒤 종료)으로
	// 알려도 웹이 멈추지 않는지 확인한다
	await mockCoachingServer(page, (_question, { send, route }) => {
		send({
			type: 'connection_error',
			data: { code: 'AUTH_REQUIRED', message: '로그인이 필요합니다.' }
		});
		route.close({ code: 1008 });
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	await expect(page).toHaveURL('/login');
});

test('호환 처리: 연결된 뒤 connection_error AUTH_UNAVAILABLE을 받아도 자동으로 다시 보내지 않고 다시 보내기를 안내한다', async ({
	page
}) => {
	const connections = new Set<unknown>();
	const questions = await mockCoachingServer(page, (question, { send, route }) => {
		connections.add(route);
		if (questions.length === 1) {
			// 예전 방식: 질문을 저장하지 않고 connection_error를 보낸 뒤 연결을 닫는다
			send({
				type: 'connection_error',
				data: { code: 'AUTH_UNAVAILABLE', message: '인증 처리를 일시적으로 이용할 수 없습니다.' }
			});
			route.close({ code: 1011 });
		} else {
			const events = coachingEvents(question);
			send(events.accepted());
			send(events.completed('답변'));
		}
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const alert = page.getByRole('alert');
	await expect(alert).toContainText('인증 처리를 일시적으로 이용할 수 없습니다.');
	await expect(page).not.toHaveURL('/login');
	await expect(page.getByRole('banner')).toBeVisible();
	// 연결이 닫혀도 같은 질문을 자동으로 다시 보내지 않는다. 다시 보내는 것은 사용자가 정한다
	await page.waitForTimeout(500);
	expect(questions).toHaveLength(1);

	await alert.getByRole('button', { name: '다시 보내기' }).click();
	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	expect(questions).toHaveLength(2);
	// 거절된 연결을 다시 쓰지 않고 새 연결로 보낸다
	expect(connections.size).toBe(2);
});

test('질문 시점에 세션이 거절되면(요청 단위 AUTH_REQUIRED) 로그인 화면으로 보낸다', async ({
	page
}) => {
	// 연결은 됐지만 그 사이 로그아웃·만료된 경우. 서버는 소켓을 닫지 않고 접수 전 error로 알린다(명세 v47 7.2·7.5)
	const questions = await mockCoachingServer(page, (question, { send }) => {
		send(coachingEvents(question).error('AUTH_REQUIRED', '로그인이 필요합니다.', false));
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	await expect(page).toHaveURL('/login');
	// 거절된 질문을 자동으로 다시 보내지 않는다
	await page.waitForTimeout(500);
	expect(questions).toHaveLength(1);
});

test('질문 시점의 일시 장애(요청 단위 AUTH_UNAVAILABLE)는 로그아웃하지 않고 같은 연결로 다시 보낸다', async ({
	page
}) => {
	// 질문이 들어온 연결을 모은다. 다시 연결했다면 두 개가 된다
	const connections = new Set<unknown>();
	const questions = await mockCoachingServer(page, (question, { send, route }) => {
		connections.add(route);
		const events = coachingEvents(question);
		if (questions.length === 1) {
			send(events.error('AUTH_UNAVAILABLE', '인증 처리를 일시적으로 이용할 수 없습니다.', false));
		} else {
			send(events.accepted());
			send(events.completed('답변'));
		}
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const alert = page.getByRole('alert');
	await expect(alert).toContainText('인증 처리를 일시적으로 이용할 수 없습니다.');
	await expect(page).not.toHaveURL('/login');
	// 자동으로 다시 보내지 않는다. 다시 보내는 것은 사용자가 정한다
	await page.waitForTimeout(500);
	expect(questions).toHaveLength(1);
	await alert.getByRole('button', { name: '다시 보내기' }).click();

	await expect(page).toHaveURL(`/coaching/${CONVERSATION_ID}`);
	expect(questions).toHaveLength(2);
	// 연결을 유지한 채 같은 소켓으로 다시 보낸다
	expect(connections.size).toBe(1);
});

test('지난 메시지 조회에 생성 중인 첫 질문이 들어 있어도 실시간 답변을 끝까지 보여 준다', async ({
	page
}) => {
	let historyLoaded: () => void = () => {};
	const loaded = new Promise<void>((resolve) => (historyLoaded = resolve));
	await mockCoachingServer(page, async (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		// 서버는 접수 때 질문과 빈 답변(generating)을 저장하므로 6.3 응답에 이미 들어 있다
		await loaded;
		send(events.delta('실시간으로 받은 답변'));
		send(events.evidence());
		send(events.completed('실시간으로 받은 답변'));
	});
	await page.route(`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages**`, (route) => {
		const now = '2026-10-26T01:00:00Z';
		const message = (id: string, role: string, content: string, sequenceNo: number) => ({
			id,
			requestId: REQUEST_ID,
			role,
			sequenceNo,
			content,
			requestStatus: 'generating',
			errorCode: null,
			createdAt: now,
			evidence: [],
			errorContext: null
		});
		void route
			.fulfill({
				json: {
					data: {
						conversation: {
							id: CONVERSATION_ID,
							title: QUESTION,
							summary: null,
							createdAt: now,
							updatedAt: now
						},
						items: [message('u1', 'user', QUESTION, 1), message('a1', 'assistant', '', 2)],
						nextBeforeSequenceNo: null
					}
				}
			})
			.then(historyLoaded);
	});
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByText('실시간으로 받은 답변')).toBeVisible();
	await expect(log.getByRole('region', { name: '이 답변에서 사용한 기록' })).toBeVisible();
	await expect(log.getByRole('status')).toHaveCount(0);
	// 같은 질문이 저장본과 실시간으로 두 번 보이지 않는다
	await expect(log.getByText(QUESTION, { exact: true })).toHaveCount(1);
	await expect(log.getByRole('article')).toHaveCount(1);
});

const NOW_ISO = '2026-10-27T01:00:00Z';
const listItem = (lastRequestStatus: string) => ({
	id: CONVERSATION_ID,
	title: QUESTION,
	summary: null,
	createdAt: NOW_ISO,
	updatedAt: NOW_ISO,
	lastRequestStatus
});

test('답변이 끝나면 대화 목록을 다시 읽어 상태를 갱신한다', async ({ page }) => {
	let serverStatus = 'generating';
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	await mockCoachingServer(page, async (question, { send }) => {
		const events = coachingEvents(question);
		send(events.accepted());
		await held;
		serverStatus = 'completed';
		send(events.completed('완료된 답변'));
	});
	await page.route('**/api/v1/coaching/conversations?**', (route) =>
		route.fulfill({ json: { data: { items: [listItem(serverStatus)], nextCursor: null } } })
	);
	await page.route('**/api/v1/coaching/conversations', (route) =>
		route.fulfill({ json: { data: { items: [listItem(serverStatus)], nextCursor: null } } })
	);
	await page.goto('/coaching', { waitUntil: 'networkidle' });
	await page.getByRole('textbox', { name: '질문 입력' }).fill(QUESTION);
	await page.getByRole('button', { name: '질문 보내기' }).click();

	const list = page.getByRole('navigation', { name: '대화 목록' });
	const current = list.getByRole('link', { name: QUESTION });
	await expect(current).toContainText('답변 작성 중');

	release();
	await expect(page.getByText('완료된 답변')).toBeVisible();
	await expect(current).toContainText('AI 답변 완료');
});

test('새로고침 뒤 생성 중으로 저장된 질문은 상태 조회로 완료까지 따라가고 그동안 입력을 잠근다', async ({
	page
}) => {
	await mockCoachingServer(page, () => {});
	let polls = 0;
	// 목록은 ?limit=20 을 붙여 읽는다(첫 페이지 20개)
	await page.route('**/api/v1/coaching/conversations?**', (route) =>
		route.fulfill({
			json: {
				data: { items: [listItem(polls >= 2 ? 'completed' : 'generating')], nextCursor: null }
			}
		})
	);
	await page.route(`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages**`, (route) => {
		const message = (id: string, role: string, sequenceNo: number) => ({
			id,
			requestId: REQUEST_ID,
			role,
			sequenceNo,
			content: role === 'user' ? QUESTION : '',
			requestStatus: 'generating',
			errorCode: null,
			createdAt: NOW_ISO,
			evidence: [],
			errorContext: null
		});
		return route.fulfill({
			json: {
				data: {
					conversation: {
						id: CONVERSATION_ID,
						title: QUESTION,
						summary: null,
						createdAt: NOW_ISO,
						updatedAt: NOW_ISO
					},
					items: [message('u1', 'user', 1), message('a1', 'assistant', 2)],
					nextBeforeSequenceNo: null
				}
			}
		});
	});
	await page.route(`**/api/v1/coaching/requests/${REQUEST_ID}`, (route) => {
		polls += 1;
		const done = polls >= 2;
		return route.fulfill({
			json: {
				data: {
					requestId: REQUEST_ID,
					clientRequestId: '11111111-1111-4111-8111-111111111111',
					conversationId: CONVERSATION_ID,
					status: done ? 'completed' : 'generating',
					userMessageId: 'u1',
					assistantMessage: {
						id: 'a1',
						role: 'assistant',
						sequenceNo: 2,
						content: done ? '새로고침 뒤 받은 답변' : '',
						createdAt: NOW_ISO
					},
					evidence: done ? evidence : [],
					errorCode: null,
					createdAt: NOW_ISO,
					finishedAt: done ? NOW_ISO : null,
					errorContext: null
				}
			}
		});
	});

	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });
	const log = page.getByRole('log', { name: '대화 내용' });
	await expect(log.getByText(QUESTION, { exact: true })).toHaveCount(1);
	await expect(log.getByRole('status')).toBeVisible();
	await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeDisabled();

	await expect(log.getByText('새로고침 뒤 받은 답변')).toBeVisible({ timeout: 8000 });
	await expect(log.getByRole('region', { name: '이 답변에서 사용한 기록' })).toBeVisible();
	await expect(page.getByRole('textbox', { name: '질문 입력' })).toBeEnabled();
	await expect(page.getByRole('navigation', { name: '대화 목록' })).toContainText('AI 답변 완료');
	expect(polls).toBe(2);
});

test('대화 화면 머리말은 AI 누적 요약(summary)이 있어도 제목(title)을 보여 준다', async ({
	page
}) => {
	await mockCoachingServer(page, () => {});
	const summary = '사용자는 걸음 수 변화를 물었다.\n- 지난주 대비 증가\n- 주말 영향 확인';
	await page.route(`**/api/v1/coaching/conversations/${CONVERSATION_ID}/messages**`, (route) =>
		route.fulfill({
			json: {
				data: {
					conversation: {
						id: CONVERSATION_ID,
						title: QUESTION,
						summary,
						createdAt: NOW_ISO,
						updatedAt: NOW_ISO
					},
					items: [],
					nextBeforeSequenceNo: null
				}
			}
		})
	);
	await page.goto(`/coaching/${CONVERSATION_ID}`, { waitUntil: 'networkidle' });

	const header = page
		.getByRole('heading', { level: 1, name: 'AI 코치' })
		.locator('xpath=ancestor::header[1]');
	await expect(header).toContainText(QUESTION);
	await expect(header).not.toContainText('사용자는 걸음 수 변화를 물었다');
	// 탭 제목에는 질문 문장을 넣지 않는다(방문 기록에 남는다, S15P31A403-439)
	await expect(page).toHaveTitle('AI 코치 | 캔디핏');
});
