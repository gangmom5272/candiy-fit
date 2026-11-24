import { expect, test, type Page } from '@playwright/test';
import { e2eSession, enterOnboarding } from '../../../lib/testing/e2e-auth';

const SIGNUP_API = '**/api/v1/auth/signup';

async function fillProfile(page: Page, heightCm = '165.25') {
	await page.getByLabel('닉네임').fill('  새회원  ');
	await page.getByLabel('태어난 연도').fill('1995');
	await page.getByText('여성', { exact: true }).click();
	await page.getByLabel('키', { exact: true }).fill(heightCm);
	await page.getByLabel('몸무게', { exact: true }).fill('55');
}

test('가입 자격 없이 열면 로그인 화면으로 보낸다', async ({ page }) => {
	await page.goto('/onboarding');

	await expect(page).toHaveURL('/login');
});

test('가입 화면을 새로고침하면 메모리 자격이 사라져 로그인으로 돌아간다', async ({ page }) => {
	await enterOnboarding(page);
	await expect(page).toHaveURL('/onboarding');
	await page.reload();
	await expect(page).toHaveURL('/login');
});

test('가입 자격이 10분을 지나면 API를 호출하지 않고 재로그인을 안내한다', async ({ page }) => {
	await page.clock.install();
	let calls = 0;
	await page.route(SIGNUP_API, (route) => {
		calls += 1;
		return route.abort();
	});
	await enterOnboarding(page);
	await fillProfile(page);
	await page.clock.fastForward(11 * 60 * 1000);
	await page.getByRole('button', { name: '가입하기' }).click();
	await expect(page.getByRole('heading', { name: '다시 로그인해 주세요' })).toBeVisible();
	expect(calls).toBe(0);
});

test('입력 오류는 서버로 보내지 않고 첫 오류 칸으로 이동한다', async ({ page }) => {
	let calls = 0;
	await page.route(SIGNUP_API, (route) => {
		calls += 1;
		return route.abort();
	});
	await enterOnboarding(page);

	await page.getByRole('button', { name: '가입하기' }).click();

	await expect(page.getByLabel('닉네임')).toBeFocused();
	await expect(page.getByLabel('닉네임')).toHaveAccessibleDescription(
		'최대 12자 닉네임을 입력해 주세요.'
	);
	await expect(page.getByText('성별을 선택해 주세요.')).toBeVisible();
	await fillProfile(page, '0');
	await page.getByRole('button', { name: '가입하기' }).click();
	await expect(page.getByLabel('키', { exact: true })).toHaveAccessibleDescription(
		'cm 키는 0보다 크고 300 이하로 입력해 주세요.'
	);
	expect(calls).toBe(0);
});

test('칸마다 들어갈 수 없는 글자는 입력하는 즉시 지운다', async ({ page }) => {
	await enterOnboarding(page);
	const nickname = page.getByLabel('닉네임');
	const birthYear = page.getByLabel('태어난 연도');
	const height = page.getByLabel('키', { exact: true });
	const weight = page.getByLabel('몸무게', { exact: true });

	await nickname.pressSequentially('직장인,,,,,,,,,,,,,,,');
	await birthYear.pressSequentially('1992,,,,,,');
	await height.pressSequentially('175,,,.555');
	await weight.fill('74kg');

	await expect(nickname).toHaveValue('직장인,,,,,,,,,');
	await expect(birthYear).toHaveValue('1992');
	await expect(height).toHaveValue('175.55');
	await expect(weight).toHaveValue('74');

	// 12자가 찬 뒤 앞에 넣은 글자는 들어가지 않고, 끝 글자가 밀려나지 않는다
	await nickname.press('Home');
	await nickname.pressSequentially('가');
	await expect(nickname).toHaveValue('직장인,,,,,,,,,');

	// 가운데에서 고쳐도 커서가 입력하던 자리에 남는다
	await birthYear.fill('');
	await birthYear.pressSequentially('192');
	await birthYear.press('ArrowLeft');
	await birthYear.pressSequentially('a9');
	await expect(birthYear).toHaveValue('1992');
});

test('서버 422는 입력칸에 표시하고 고친 뒤 가입하면 홈으로 간다', async ({ page }) => {
	const bodies: unknown[] = [];
	await page.route(SIGNUP_API, (route) => {
		bodies.push(route.request().postDataJSON());
		return bodies.length === 1
			? route.fulfill({
					status: 422,
					json: {
						error: {
							code: 'INVALID_REQUEST',
							message: '요청 내용을 확인해 주세요.',
							details: [{ field: 'birthYear', reason: 'out_of_range' }]
						}
					}
				})
			: route.fulfill({ status: 201, json: { data: e2eSession } });
	});
	await enterOnboarding(page);
	await fillProfile(page);

	await page.getByRole('button', { name: '가입하기' }).click();
	await expect(page.getByLabel('태어난 연도')).toHaveAttribute('aria-invalid', 'true');
	await expect(page.getByText(/1900년부터 \d{4}년 사이로/)).toBeVisible();

	await page.getByRole('button', { name: '가입하기' }).click();
	await expect(page).toHaveURL('/home');
	expect(bodies[1]).toEqual({
		signupToken: 'synthetic-signup',
		displayName: '새회원',
		birthYear: 1995,
		gender: 'FEMALE',
		heightCm: 165.25,
		weightKg: 55
	});
});

test('가입 자격이 무효면 다시 로그인하도록 안내한다', async ({ page }) => {
	await page.route(SIGNUP_API, (route) =>
		route.fulfill({
			status: 401,
			json: { error: { code: 'SIGNUP_TOKEN_INVALID', message: '다시 로그인해 주세요.' } }
		})
	);
	await enterOnboarding(page);
	await fillProfile(page);

	await page.getByRole('button', { name: '가입하기' }).click();

	await expect(page.getByRole('heading', { name: '다시 로그인해 주세요' })).toBeVisible();
	await expect(page.getByRole('link', { name: '로그인 화면으로' })).toHaveAttribute(
		'href',
		'/login'
	);
});

test('일시 장애는 입력값을 유지하고 요청 ID를 보여 준다', async ({ page }) => {
	await page.route(SIGNUP_API, (route) =>
		route.fulfill({
			status: 503,
			headers: { 'X-Request-Id': 'req-e2e-2' },
			json: { error: { code: 'AUTH_UNAVAILABLE', message: '잠시 후 다시 시도해 주세요.' } }
		})
	);
	await enterOnboarding(page);
	await fillProfile(page);

	await page.getByRole('button', { name: '가입하기' }).click();

	const alert = page.getByRole('alert');
	await expect(alert).toContainText('잠시 후 다시 시도해 주세요.');
	await expect(alert).toContainText('요청 ID: req-e2e-2');
	await expect(page.getByLabel('닉네임')).toHaveValue('  새회원  ');
});
