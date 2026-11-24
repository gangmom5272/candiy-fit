import { describe, expect, it } from 'vitest';
import { CoachingRequest } from './session.svelte';
import { getErrorText, getProgressText, toAssistantMessage } from './view';

const CLIENT_ID = '11111111-1111-4111-8111-111111111111';

function request() {
	return new CoachingRequest(CLIENT_ID, null, '질문');
}

describe('toAssistantMessage', () => {
	it('전송 중·접수 질문은 답변 대기 상태로 보여 준다', () => {
		const sending = request();
		expect(toAssistantMessage(sending)).toMatchObject({
			id: `live-${CLIENT_ID}`,
			role: 'assistant',
			content: '',
			requestStatus: 'accepted',
			errorCode: null,
			errorContext: null
		});

		sending.phase = 'generating';
		sending.text = '부분 답변';
		expect(toAssistantMessage(sending)).toMatchObject({
			requestStatus: 'generating',
			content: '부분 답변'
		});
	});

	it('실패·미접수는 failed로, 건강 조회 오류는 errorContext로 바꾼다', () => {
		const failed = request();
		failed.phase = 'rejected';
		failed.error = {
			code: 'HEALTH_DATA_UNAVAILABLE',
			message: '건강데이터를 조회하지 못했습니다.',
			retryable: true,
			reason: 'DATA_NOT_READY',
			metrics: ['sleeps']
		};
		expect(toAssistantMessage(failed)).toMatchObject({
			requestStatus: 'failed',
			errorCode: 'HEALTH_DATA_UNAVAILABLE',
			errorContext: { reason: 'DATA_NOT_READY', metrics: ['sleeps'] }
		});
	});
});

describe('getProgressText', () => {
	it('전송 중 → 기본 문구 → 단계 문구 순으로 바꾼다', () => {
		const item = request();
		expect(getProgressText(item)).toBe('질문을 보내고 있어요');
		item.phase = 'accepted';
		expect(getProgressText(item)).toBe('답변 작성 중');
		item.stage = 'fetching_health_data';
		expect(getProgressText(item)).toBe('건강 기록을 확인하고 있어요');
	});
});

describe('getErrorText', () => {
	const error = (code: string, message: string) => ({
		code,
		message,
		retryable: true,
		reason: null,
		metrics: null
	});

	it('오류가 없으면 없고, 미접수는 보내지 못했다고 알린다', () => {
		const item = request();
		expect(getErrorText(item)).toBeUndefined();
		item.phase = 'rejected';
		item.error = error('CONNECTION_FAILED', '코칭 서버와 연결하지 못했습니다.');
		expect(getErrorText(item)).toBe('질문을 보내지 못했어요. 코칭 서버와 연결하지 못했습니다.');
	});

	it('앱 미연동은 연동할 곳을 함께 안내한다', () => {
		const item = request();
		item.phase = 'failed';
		item.error = error('CANDIY_USER_NOT_LINKED', '앱 연동을 확인해 주세요.');
		expect(getErrorText(item)).toBe(
			'앱 연동을 확인해 주세요. 휴대폰의 LiNK 앱에서 로그인한 뒤 건강 기록 연동을 마쳐 주세요.'
		);
	});
});
