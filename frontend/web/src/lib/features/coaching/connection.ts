import { authSession } from '#lib/features/auth/session.svelte.js';
import { getRequestStatus } from './api';
import { CoachingSession } from './session.svelte';
import { CoachingSocket, defaultSocketUrl } from './socket';

/**
 * 화면에서 쓰는 코칭 연결. WebSocket 인증은 브라우저가 핸드셰이크에 싣는 세션 쿠키로 하고,
 * 소켓 이벤트·끊김을 CoachingSession으로 넘긴다. 브라우저에서만 호출하고 화면을 떠날 때 dispose()한다.
 */
export function createCoachingSession(): CoachingSession {
	const socket = new CoachingSocket({
		url: defaultSocketUrl(window.location),
		onEvent: (event) => session.handleEvent(event),
		onClose: () => session.handleDisconnect(),
		onAuthRequired: () => authSession.clearLocalSession(),
		onQuestionRejected: (code) => session.handleQuestionRejected(code)
	});
	const session = new CoachingSession({
		socket,
		getRequestStatus: (id, options) => getRequestStatus(id, options)
	});
	return session;
}
