import type { CoachingRequest, RequestPhase } from './session.svelte';
import type { CoachingMessage, CoachingStage, RequestStatus } from './types';

/** 명세 7.4 status 이벤트의 진행 단계 문구. 시안이 정해지면 바꾼다. */
export const STAGE_LABELS: Record<CoachingStage, string> = {
	reading_history: '이전 대화를 확인하고 있어요',
	fetching_health_data: '건강 기록을 확인하고 있어요',
	generating_answer: '답변을 작성하고 있어요'
};

const PHASE_STATUS: Record<RequestPhase, RequestStatus> = {
	// 접수 전에도 화면에서는 답변을 기다리는 중으로 보여 준다
	sending: 'accepted',
	accepted: 'accepted',
	generating: 'generating',
	completed: 'completed',
	failed: 'failed',
	rejected: 'failed'
};

/** 진행 중인 질문을 지난 메시지(명세 6.3)와 같은 형태로 바꿔 AssistantMessage로 그린다. */
export function toAssistantMessage(request: CoachingRequest): CoachingMessage {
	const { error } = request;
	return {
		id: `live-${request.clientRequestId}`,
		requestId: request.requestId ?? '',
		role: 'assistant',
		sequenceNo: 0,
		content: request.text,
		requestStatus: PHASE_STATUS[request.phase],
		errorCode: error?.code ?? null,
		createdAt: '',
		evidence: request.evidence,
		errorContext: error?.reason ? { reason: error.reason, metrics: error.metrics ?? [] } : null
	};
}

/**
 * 공개 메시지(명세 1.3)만으로는 할 일을 알기 어려운 오류에 붙이는 안내.
 * 앱 연동 안내 화면이 생기면 그 화면으로 가는 링크로 바꾼다.
 */
const ERROR_HINTS: Partial<Record<string, string>> = {
	CANDIY_USER_NOT_LINKED: '휴대폰의 LiNK 앱에서 로그인한 뒤 건강 기록 연동을 마쳐 주세요.'
};

/** 오류 문구 뒤에 할 일 안내를 붙인다. 지난 메시지의 저장된 실패에도 쓴다. */
export function withErrorHint(code: string, message: string): string {
	const hint = ERROR_HINTS[code];
	return hint ? `${message} ${hint}` : message;
}

/** 실패·미접수 질문에 보여 줄 안내. 오류가 없으면 undefined */
export function getErrorText(request: CoachingRequest): string | undefined {
	const { error } = request;
	if (!error) return undefined;
	return withErrorHint(
		error.code,
		request.phase === 'rejected' ? `질문을 보내지 못했어요. ${error.message}` : error.message
	);
}

/** 답변 대기 중 보여 줄 문구. 단계 이벤트가 오기 전에는 기본 문구를 쓴다. */
export function getProgressText(request: CoachingRequest): string {
	if (request.phase === 'sending') return '질문을 보내고 있어요';
	return request.stage ? STAGE_LABELS[request.stage] : '답변 작성 중';
}
