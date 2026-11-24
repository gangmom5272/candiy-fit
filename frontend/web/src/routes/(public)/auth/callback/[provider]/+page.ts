import { error } from '@sveltejs/kit';
import { isOAuthProvider } from '#lib/features/auth/oauth.js';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
	if (!isOAuthProvider(params.provider)) error(404, '지원하지 않는 로그인 방식이에요.');
	return { provider: params.provider };
};
