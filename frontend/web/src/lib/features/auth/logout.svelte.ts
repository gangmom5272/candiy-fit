import { authSession, toLogoutFailure } from './session.svelte';
import type { LogoutFailure } from './types';

const LOGOUT_ERROR_MESSAGES: Record<LogoutFailure, string> = {
	failed: '로그아웃하지 못했어요. 잠시 후 다시 시도해 주세요.',
	uncertain: '로그아웃 완료 여부를 확인하지 못했어요. 잠시 후 다시 확인해 주세요.'
};

/** 화면이 이동 정책을 맡고, 요청 중복 방지와 기존 3초 실패 안내는 공유한다. */
export class LogoutController {
	message = $state<string | null>(null);
	#isLoggingOut = false;
	#toastTimer: ReturnType<typeof setTimeout> | undefined;

	constructor() {
		$effect(() => () => clearTimeout(this.#toastTimer));
	}

	/** 성공한 경우에만 true. 세션 정리·탭 간 전파는 AuthSession의 기존 정책을 따른다. */
	run = async (): Promise<boolean> => {
		if (this.#isLoggingOut) return false;
		this.#isLoggingOut = true;
		try {
			await authSession.logout();
			return true;
		} catch (error) {
			this.message = LOGOUT_ERROR_MESSAGES[toLogoutFailure(error)];
			clearTimeout(this.#toastTimer);
			this.#toastTimer = setTimeout(() => (this.message = null), 3000);
			return false;
		} finally {
			this.#isLoggingOut = false;
		}
	};
}
