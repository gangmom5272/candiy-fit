import { expect, test, type Page } from '@playwright/test';
import { e2eUser, mockSignedIn } from '../../../lib/testing/e2e-auth';

const ME_API = '**/api/v1/users/me';

test.beforeEach(({ page }) => mockSignedIn(page));

/** GET은 저장값을 주고, PATCH는 handler가 정한 응답을 준다. */
async function mockMe(
	page: Page,
	onPatch: (body: Record<string, unknown>) => { status: number; json?: unknown; abort?: boolean }
) {
	const calls = { get: 0, patch: [] as Record<string, unknown>[] };
	await page.route(ME_API, (route) => {
		if (route.request().method() === 'GET') {
			calls.get += 1;
			return route.fulfill({ json: { data: e2eUser } });
		}
		const body = route.request().postDataJSON();
		calls.patch.push(body);
		const response = onPatch(body);
		if (response.abort) return route.abort();
		return route.fulfill({
			status: response.status,
			headers: { 'X-Request-Id': 'req-e2e-my' },
			json: response.json
		});
	});
	return calls;
}

test('헤더 메뉴에서 마이페이지로 가서 저장값을 보여 준다', async ({ page }) => {
	const calls = await mockMe(page, () => ({ status: 500 }));
	await page.goto('/home');

	await page.getByRole('button', { name: '사용자 메뉴' }).click();
	const menuItem = page.getByRole('menuitem', { name: '마이페이지' });
	await expect(menuItem).toHaveAttribute('href', '/my');
	await menuItem.click();

	await expect(page).toHaveURL('/my');
	await expect(page.getByRole('heading', { level: 1, name: '기본 정보 수정' })).toBeVisible();
	await expect(page.getByLabel('닉네임')).toHaveValue('테스트');
	await expect(page.getByLabel('태어난 연도')).toHaveValue('2000');
	await expect(page.getByRole('radio', { name: '여성' })).toBeChecked();
	await expect(page.getByLabel('키', { exact: true })).toHaveValue('160');
	await expect(page.getByLabel('몸무게', { exact: true })).toHaveValue('50');
	expect(calls.get).toBe(1);

	// 바뀐 내용이 없다는 안내는 제목 아래에 남는 상자가 아니라 잠깐 뜨는 토스트다(S15P31A403-438)
	const form = page.getByRole('button', { name: '저장하기' });
	const before = await form.boundingBox();
	await form.click();
	await expect(page.getByRole('status')).toHaveText('바뀐 내용이 없어요.');
	expect((await form.boundingBox())?.y).toBe(before?.y);
	await expect(page.getByRole('status')).toHaveCount(0, { timeout: 5000 });
	expect(calls.patch).toHaveLength(0);
});

test('바꾼 필드만 보내고 저장 후 새 값으로 다시 시작한다', async ({ page }) => {
	const calls = await mockMe(page, (body) => ({
		status: 200,
		json: { data: { ...e2eUser, ...body } }
	}));
	await page.goto('/my');
	const weight = page.getByLabel('몸무게', { exact: true });
	const save = page.getByRole('button', { name: '저장하기' });

	await weight.fill('56.5');
	await page.getByLabel('닉네임').fill(' 테스트 ');
	await save.click();

	await expect(page.getByRole('status')).toHaveText('기본 정보를 저장했어요.');
	expect(calls.patch).toEqual([{ weightKg: 56.5 }]);
	await expect(weight).toHaveValue('56.5');
	await expect(page.getByLabel('닉네임')).toHaveValue('테스트');
});

test('서버 422는 입력칸에, 503은 요청 ID와 함께 보여 준다', async ({ page }) => {
	let attempt = 0;
	await mockMe(page, () => {
		attempt += 1;
		return attempt === 1
			? {
					status: 422,
					json: {
						error: {
							code: 'INVALID_REQUEST',
							message: '요청 내용을 확인해 주세요.',
							details: [{ field: 'heightCm', reason: 'decimal_places' }]
						}
					}
				}
			: {
					status: 503,
					json: { error: { code: 'USER_UNAVAILABLE', message: '잠시 후 다시 시도해 주세요.' } }
				};
	});
	await page.goto('/my');
	const height = page.getByLabel('키', { exact: true });

	await height.fill('170');
	await page.getByRole('button', { name: '저장하기' }).click();
	await expect(height).toHaveAccessibleDescription('cm 키는 소수 둘째 자리까지 입력할 수 있어요.');

	await page.getByRole('button', { name: '저장하기' }).click();
	const alert = page.getByRole('alert');
	await expect(alert).toContainText('잠시 후 다시 시도해 주세요.');
	await expect(alert).toContainText('요청 ID: req-e2e-my');
	await expect(height).toHaveValue('170');
});

test('저장 응답을 잃으면 다시 보내지 않고 저장값을 다시 읽는다', async ({ page }) => {
	const calls = await mockMe(page, () => ({ status: 0, abort: true }));
	await page.goto('/my');

	await page.getByLabel('몸무게', { exact: true }).fill('70');
	await page.getByRole('button', { name: '저장하기' }).click();

	await expect(page.getByRole('alert')).toContainText('저장된 정보를 다시 불러왔어요');
	await expect(page.getByLabel('몸무게', { exact: true })).toHaveValue('50');
	expect(calls.patch).toHaveLength(1);
	expect(calls.get).toBe(2);
});

test('조회에 실패하면 다시 시도할 수 있다', async ({ page }) => {
	let gets = 0;
	await page.route(ME_API, (route) => {
		gets += 1;
		return gets === 1
			? route.fulfill({
					status: 503,
					json: { error: { code: 'USER_UNAVAILABLE', message: '잠시 후' } }
				})
			: route.fulfill({ json: { data: e2eUser } });
	});
	await page.goto('/my');

	await expect(page.getByText('기본 정보를 불러오지 못했어요.')).toBeVisible();
	await page.getByRole('button', { name: '다시 시도' }).click();
	await expect(page.getByLabel('닉네임')).toHaveValue('테스트');
});

test('저장하는 동안 입력을 잠가 응답 후 값이 되돌아가지 않게 한다', async ({ page }) => {
	let release: () => void = () => {};
	const held = new Promise<void>((resolve) => (release = resolve));
	await page.route(ME_API, async (route) => {
		if (route.request().method() === 'GET') return route.fulfill({ json: { data: e2eUser } });
		const body = route.request().postDataJSON();
		await held;
		return route.fulfill({ json: { data: { ...e2eUser, ...body } } });
	});
	await page.goto('/my');
	const weight = page.getByLabel('몸무게', { exact: true });

	await weight.fill('56.5');
	await page.getByRole('button', { name: '저장하기' }).click();
	await expect(page.getByLabel('닉네임')).toBeDisabled();
	await expect(page.getByRole('radio', { name: '남성' })).toBeDisabled();

	release();
	await expect(page.getByRole('status')).toHaveText('기본 정보를 저장했어요.');
	await expect(page.getByLabel('닉네임')).toBeEnabled();
	await expect(weight).toHaveValue('56.5');
});

test('프록시 오류(502)는 저장 여부를 알 수 없으므로 저장값을 다시 읽는다', async ({ page }) => {
	let gets = 0;
	await page.route(ME_API, (route) => {
		if (route.request().method() === 'GET') {
			gets += 1;
			// 서버에는 저장된 상태
			return route.fulfill({ json: { data: gets === 1 ? e2eUser : { ...e2eUser, weightKg: 70 } } });
		}
		return route.fulfill({
			status: 502,
			contentType: 'text/html',
			body: '<h1>502 Bad Gateway</h1>'
		});
	});
	await page.goto('/my');

	await page.getByLabel('몸무게', { exact: true }).fill('70');
	await page.getByRole('button', { name: '저장하기' }).click();

	await expect(page.getByRole('alert')).toContainText('저장된 정보를 다시 불러왔어요');
	await expect(page.getByLabel('몸무게', { exact: true })).toHaveValue('70');
	expect(gets).toBe(2);
});

// 278: 탈퇴했거나 세션이 무효(401 AUTH_REQUIRED)면 "불러오지 못함"에 멈추지 않고 로그인 화면으로 간다(홈·건강과 같음)
test('조회가 401 AUTH_REQUIRED면 로그인 상태를 지우고 로그인 화면으로 간다', async ({ page }) => {
	await page.route(ME_API, (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'AUTH_REQUIRED', message: '로그인이 필요합니다.' } }
		})
	);
	await page.goto('/my');

	await expect(page).toHaveURL(/\/login$/);
	await expect(page.getByText('기본 정보를 불러오지 못했어요.')).toHaveCount(0);
});
