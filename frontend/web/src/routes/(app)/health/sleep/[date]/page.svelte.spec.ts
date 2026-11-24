import { page } from 'vitest/browser';
import { expect, it, vi } from 'vitest';
import { render } from 'vitest-browser-svelte';
import type { SleepMetricResult, SleepNight } from '../../../../../lib/features/health/sleep';
import { completedSleepResult } from '../../../../../lib/features/health/sleep-mock';

const fixtures = vi.hoisted(() => {
	const days: SleepMetricResult['days'] = [];
	const oxygen: SleepMetricResult['days'] = [];
	const nights: SleepNight[] = [];
	return { days, oxygen, nights };
});

vi.mock('$app/state', () => ({
	page: { params: { date: '2026-10-20' }, url: new URL('http://localhost/health/sleep/2026-10-20') }
}));
vi.mock('#lib/features/health/sleep-api.js', () => {
	return {
		sleepDateChoices: () => ['2026-10-20'],
		sleepRequestError: () => '조회 실패',
		loadSleepDetail: async () => ({
			sleep: {
				status: 'fulfilled',
				value: {
					summary: { linkedFrom: '2026-10-20' },
					days: fixtures.days,
					detail: { nights: fixtures.nights }
				}
			},
			oxygen: { status: 'fulfilled', value: { days: fixtures.oxygen } },
			timeline: { status: 'fulfilled', value: [] }
		})
	};
});

import SleepDetail from './+page.svelte';

const cases = [
	['NO_RECORD', '해당 날짜의 수면 기록이 없어요.', '기록 없음'],
	['UNSYNCED', '아직 건강 데이터가 동기화되지 않았어요.', '동기화 대기'],
	['BEFORE_LINK', '연동 전의 기록은 확인할 수 없어요.', '연동 전'],
	['NULL_VALUE', '해당 정보가 기록되지 않았어요.', '값 없음']
] as const;

it.each(cases)(
	'수면 전체 %s는 같은 날짜에서도 서버 reason에 맞춰 안내한다',
	async (reason, message) => {
		fixtures.days.splice(0, fixtures.days.length, {
			date: '2026-10-20',
			value: null,
			missing: true,
			reason,
			isInProgress: false
		});
		fixtures.nights.length = 0;
		fixtures.oxygen.length = 0;
		render(SleepDetail);
		await expect.element(page.getByText(message, { exact: true })).toBeVisible();
	}
);

it.each(cases)('산소포화도 %s는 수면 요약과 분리해서 표시한다', async (reason, _message, label) => {
	const night = completedSleepResult.detail.nights.find((item) => item.date === '2026-10-20');
	if (!night) throw new Error('기존 수면 fixture 없음');
	fixtures.nights.splice(0, fixtures.nights.length, night);
	fixtures.days.splice(0, fixtures.days.length, {
		date: night.date,
		value: night.sleepMinutes,
		missing: false,
		reason: null,
		isInProgress: false
	});
	fixtures.oxygen.splice(0, fixtures.oxygen.length, {
		date: night.date,
		value: null,
		missing: true,
		reason,
		isInProgress: false
	});
	render(SleepDetail);
	const summary = page.getByRole('region', { name: '수면 요약', exact: true });
	await expect.element(summary.getByText('6시간 39분', { exact: true })).toBeVisible();
	await expect.element(summary.getByText(label, { exact: true })).toBeVisible();
});
