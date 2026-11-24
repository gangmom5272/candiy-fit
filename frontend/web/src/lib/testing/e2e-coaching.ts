import type { Page, WebSocketRoute } from '@playwright/test';
import { sleepAndStepsEvidence } from '../features/coaching/fixtures';

/** e2e 전용 합성 값. 실제 대화·건강데이터가 아니다. */
export const CONVERSATION_ID = '22222222-2222-4222-8222-222222222222';
export const REQUEST_ID = '33333333-3333-4333-8333-333333333333';
export const evidence = sleepAndStepsEvidence;

type Question = { clientRequestId: string; conversationId: string | null; content: string };

/** 질문 하나에 대한 서버 이벤트를 만든다. 공통 필드(ID·eventSeq)는 send가 채운다. */
export function coachingEvents(question: Question, requestId = REQUEST_ID) {
	let eventSeq = 0;
	const base = () => ({
		clientRequestId: question.clientRequestId,
		requestId,
		conversationId: CONVERSATION_ID,
		eventSeq: ++eventSeq
	});
	const now = '2026-10-26T01:00:00Z';
	return {
		accepted: () => ({
			...base(),
			type: 'accepted',
			data: {
				userMessageId: 'u1',
				assistantMessageId: 'a1',
				userSequenceNo: 1,
				assistantSequenceNo: 2,
				conversation: {
					id: CONVERSATION_ID,
					title: question.content,
					createdAt: now,
					updatedAt: now
				}
			}
		}),
		status: (stage: string) => ({ ...base(), type: 'status', data: { stage } }),
		delta: (text: string) => ({ ...base(), type: 'delta', data: { text } }),
		evidence: () => ({ ...base(), type: 'evidence', data: { items: evidence } }),
		completed: (content: string) => ({
			...base(),
			type: 'completed',
			data: {
				assistantMessage: {
					id: 'a1',
					role: 'assistant',
					sequenceNo: 2,
					content,
					createdAt: now
				},
				evidence,
				finishedAt: now
			}
		}),
		error: (code: string, message: string, accepted: boolean) => ({
			...base(),
			...(accepted ? {} : { requestId: null, conversationId: null, eventSeq: 0 }),
			type: 'error',
			data: { code, message, retryable: true, accepted, reason: null, metrics: null }
		})
	};
}

/**
 * 가짜 코칭 서버(명세 7장). 웹은 auth 프레임 없이 핸드셰이크의 세션 쿠키로 인증하므로 연결 즉시 ready를 보내고,
 * 질문마다 onQuestion을 부른다.
 * 대화 목록·지난 메시지(6.2·6.3)는 기본으로 404(불러오기 실패)를 준다. 응답이 필요한 테스트는 page.route로 덮어쓴다.
 */
export async function mockCoachingServer(
	page: Page,
	onQuestion: (
		question: Question,
		ws: { send: (event: unknown) => void; route: WebSocketRoute }
	) => void
) {
	const questions: Question[] = [];
	await page.route('**/api/v1/coaching/conversations**', (route) =>
		route.fulfill({ status: 404, json: { detail: 'Not Found' } })
	);
	await page.routeWebSocket('**/api/v1/coaching/ws', (route) => {
		const send = (event: unknown) => route.send(JSON.stringify(event));
		send({ type: 'ready', data: { protocolVersion: '1.0' } });
		route.onMessage((raw) => {
			const message = JSON.parse(String(raw));
			if (message.type === 'question') {
				questions.push(message);
				onQuestion(message, { send, route });
			}
		});
	});
	return questions;
}
