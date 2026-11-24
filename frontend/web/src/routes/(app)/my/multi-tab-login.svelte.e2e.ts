import { expect, test, type BrowserContext } from '@playwright/test';
import { SESSION_API, e2eUser, mockHealthSync } from '../../../lib/testing/e2e-auth';

const ME_API = '**/api/v1/users/me';
const LOGIN_KEY = 'candiy-fit:login-version';

/** e2e 전용 합성 회원. 실제 계정이 아니다. */
const otherUser = {
	...e2eUser,
	id: '0b0f7a4e-5c1d-4f0e-9a52-3f6f1c2d7e91',
	endUserId: '0b0f7a4e-5c1d-4f0e-9a52-3f6f1c2d7e91',
	displayName: '다른계정',
	weightKg: 70
};

/** 세션 쿠키처럼 브라우저의 모든 탭이 같은 회원으로 응답받게 한다. current 를 바꾸면 쿠키가 바뀐 것과 같다 */
async function mockSharedSession(context: BrowserContext) {
	const state = { current: e2eUser, patches: [] as { owner: string; body: unknown }[] };
	await context.route(SESSION_API, (route) =>
		route.fulfill({ json: { data: { user: state.current, expiresAt: '2099-01-01T00:00:00Z' } } })
	);
	await context.route(ME_API, (route) => {
		if (route.request().method() === 'GET') return route.fulfill({ json: { data: state.current } });
		const body = route.request().postDataJSON();
		state.patches.push({ owner: state.current.id, body });
		return route.fulfill({ json: { data: { ...state.current, ...body } } });
	});
	return state;
}

// S15P31A403-426: 한 탭이 로그아웃 없이 다른 계정으로 로그인하면 나머지 탭은 옛 회원을 보여 주면서 새 회원의 쿠키로 요청했다.
// 그 상태로 프로필을 저장하면 옛 회원의 값이 새 회원에게 저장됐다
test('다른 탭이 다른 계정으로 로그인하면 열려 있던 마이페이지가 새 회원의 정보로 바뀐다', async ({
	context
}) => {
	const state = await mockSharedSession(context);
	const first = await context.newPage();
	await mockHealthSync(first);
	await first.goto('/my');
	await expect(first.getByLabel('닉네임')).toHaveValue('테스트');
	await expect(first.getByLabel('몸무게', { exact: true })).toHaveValue('50');

	// 다른 탭에서 로그인이 끝난 것처럼: 쿠키의 회원이 바뀌고, 로그인 표시가 공유 저장소에 남는다
	const second = await context.newPage();
	await mockHealthSync(second);
	await second.goto('/my');
	state.current = otherUser;
	await second.evaluate((key) => localStorage.setItem(key, crypto.randomUUID()), LOGIN_KEY);

	// 처음 탭은 손대지 않아도 새 회원의 값으로 다시 그려진다
	await expect(first.getByLabel('닉네임')).toHaveValue('다른계정');
	await expect(first.getByLabel('몸무게', { exact: true })).toHaveValue('70');

	// 이제 저장하면 화면에 보이는 회원의 값이 그 회원에게 간다
	await first.getByLabel('몸무게', { exact: true }).fill('71');
	await first.getByRole('button', { name: '저장하기' }).click();
	await expect(first.getByRole('status')).toHaveText('기본 정보를 저장했어요.');
	expect(state.patches).toHaveLength(1);
	expect(state.patches[0].owner).toBe(otherUser.id);
	expect(state.patches[0].body).toMatchObject({ weightKg: 71 });
});

test('다른 탭이 같은 계정으로 다시 로그인하면 입력하던 내용을 지우지 않는다', async ({
	context
}) => {
	await mockSharedSession(context);
	const first = await context.newPage();
	await mockHealthSync(first);
	await first.goto('/my');
	await first.getByLabel('몸무게', { exact: true }).fill('56.5');

	const second = await context.newPage();
	await mockHealthSync(second);
	await second.goto('/my');
	const session = first.waitForResponse(SESSION_API);
	await second.evaluate((key) => localStorage.setItem(key, crypto.randomUUID()), LOGIN_KEY);
	await session;

	await expect(first.getByLabel('몸무게', { exact: true })).toHaveValue('56.5');
});
