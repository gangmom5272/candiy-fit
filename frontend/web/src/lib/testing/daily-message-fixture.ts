import type { DailyMessage } from '../features/home/daily-message-api';

/** 공개 v23 형식의 합성 응답. 서버 문구의 선택 규칙은 FE에서 재구현하지 않는다. */
export function dailyMessageFixture(date = '2026-10-26'): DailyMessage {
	return {
		date,
		status: 'READY',
		code: 'BALANCED_RECORDS',
		area: null,
		title: '오늘은 나에게 칭찬 한마디 건네볼까요?',
		description: '수면과 활동 기록이 함께 쌓였어요. 오늘 남긴 작은 성취들을 돌아보세요.',
		dataAsOf: {
			steps: `${date}T06:00:00Z`,
			exercises: `${date}T05:30:00Z`,
			sleeps: `${date}T00:00:00Z`
		},
		scoreComputedAt: `${date}T06:10:00Z`,
		notices: []
	};
}
