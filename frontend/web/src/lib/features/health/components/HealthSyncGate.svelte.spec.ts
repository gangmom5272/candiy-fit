import { expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import HealthSyncLifecycleExample from '#lib/testing/HealthSyncLifecycleExample.svelte';
import { sleepMetricFixture } from '#lib/testing/sleep-api-data.js';
import { e2eUser } from '#lib/testing/e2e-auth.js';
import { authSession } from '#lib/features/auth/session.svelte.js';
import type { User } from '#lib/features/auth/types.js';

// 컴포넌트 하네스는 SvelteKit 라우터를 시작하지 않으므로 실제 페이지 URL을 제공한다.
vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/health/sleep') }
}));

const first: User = { ...e2eUser, socialType: 'GOOGLE', gender: 'FEMALE' };
const second: User = {
	...first,
	id: '00000000-0000-4000-8000-000000000002',
	endUserId: '00000000-0000-4000-8000-000000000002',
	displayName: '합성 두 번째 계정'
};

for (const blocked of ['POST', 'GET'] as const) {
	it(`동일 SPA의 ${blocked} 중 계정 전환은 이전 요청을 취소하고 늦은 결과를 무시한다`, async () => {
		vi.setSystemTime(new Date('2026-10-26T03:00:00Z'));
		let release = () => {};
		let delivered = false;
		const held = new Promise<void>((resolve) => (release = resolve));
		const oldSignals: AbortSignal[] = [];
		vi.stubGlobal(
			'fetch',
			vi.fn<typeof fetch>(async (input, options) => {
				const owner = authSession.user?.id;
				const url = new URL(String(input), location.origin);
				const isPost = url.pathname.endsWith('/health/sync');
				const isPrimary =
					url.pathname.endsWith('/metrics/sleeps') &&
					url.searchParams.get('start') === '2026-10-20';
				if (
					owner === first.id &&
					((blocked === 'POST' && isPost) || (blocked === 'GET' && isPrimary))
				) {
					if (options?.signal) oldSignals.push(options.signal);
					await held; // fetch가 취소를 무시하고 늦게 응답하는 경우도 검증한다.
					delivered = true;
				}
				if (isPost) return Response.json({ data: { status: 'SYNCED' } });
				const metric = url.pathname.endsWith('/oxygen_saturations')
					? 'oxygen_saturations'
					: 'sleeps';
				const start = url.searchParams.get('start') ?? '';
				const end = url.searchParams.get('end') ?? '';
				const data = sleepMetricFixture(metric, start, end);
				if (metric === 'sleeps') data.summary.avg = owner === first.id ? 480 : 123;
				return Response.json({ data });
			})
		);
		const { unmount } = render(HealthSyncLifecycleExample, { first, second });
		try {
			await vi.waitFor(() => expect(oldSignals).toHaveLength(1));
			await page.getByRole('button', { name: '합성 계정 전환' }).click();
			await vi.waitFor(() => expect(oldSignals[0].aborted).toBe(true));
			await expect
				.element(page.getByRole('region', { name: '수면 기록 요약' }))
				.toHaveTextContent('2시간 3분');
			const sleepTab = page
				.getByRole('navigation', { name: '건강 데이터', exact: true })
				.getByRole('link', { name: '수면', exact: true });
			await expect.element(sleepTab).toHaveAttribute('aria-current', 'page');
			await expect.element(sleepTab).toHaveAttribute('href', '/health/sleep');
			release();
			await vi.waitFor(() => expect(delivered).toBe(true));
			await expect
				.element(page.getByRole('region', { name: '수면 기록 요약' }))
				.toHaveTextContent('2시간 3분');
			await expect
				.element(page.getByRole('region', { name: '수면 기록 요약' }))
				.not.toHaveTextContent('8시간 0분');
			await page.getByRole('button', { name: '테스트 화면 이탈' }).click();
			await expect
				.element(page.getByRole('region', { name: '수면 기록 요약' }))
				.not.toBeInTheDocument();
		} finally {
			release();
			await unmount();
			vi.unstubAllGlobals();
			vi.useRealTimers();
		}
	});
}
