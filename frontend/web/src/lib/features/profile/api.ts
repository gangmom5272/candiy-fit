import { api, type ApiClient } from '#lib/api/client.js';
import { parseUser } from '#lib/features/auth/api.js';
import type { SignupProfile, User } from '#lib/features/auth/types.js';

/** 명세 4.2 수정 요청. 바꾼 필드만 보내며 빈 객체는 422다. */
export type ProfileUpdate = Partial<SignupProfile>;

/** 명세 4.1 내 정보 조회 */
export async function getMe(client: ApiClient = api): Promise<User> {
	return parseUser(await client.request('/users/me'));
}

/** 명세 4.2 내 정보 수정. 수정 후 전체 User를 돌려준다. */
export async function updateMe(update: ProfileUpdate, client: ApiClient = api): Promise<User> {
	return parseUser(await client.request('/users/me', { method: 'PATCH', body: update }));
}
