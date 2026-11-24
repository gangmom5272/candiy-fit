import { redirect } from '@sveltejs/kit';
import { resolve } from '$app/paths';

/**
 * 첫 주소는 홈으로 보낸다 (S15P31A403-189). 로그인 확인은 홈을 감싼 AuthGate가 그대로 맡는다.
 * 로그인돼 있으면 홈, 로그인 기록이 없으면(가입 전 사용자 포함) /login, 확인이 일시 실패하면 다시 시도 화면이다.
 */
export function load() {
	redirect(307, resolve('/(app)/home'));
}
