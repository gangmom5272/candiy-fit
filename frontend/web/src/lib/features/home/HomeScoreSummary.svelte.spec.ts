import { expect, it } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import '../../../routes/layout.css';
import HomeScoreSummary from './HomeScoreSummary.svelte';
import { homeScoreStatuses, type LifestyleScore } from './home-api';

/** 브라우저 Component 테스트 전용 합성 값. E2E 실행기 의존성을 가져오지 않는다. */
function lifestyleScoreFixture(): LifestyleScore {
	return {
		date: '2026-10-26',
		isInProgress: true,
		reason: null,
		total: { score: 71, missingAreas: [] },
		areas: {
			activity: {
				score: 85,
				grade: 1,
				steps: { score: 60, value: 7200 },
				exercise: { score: 25, minutes: 19 }
			},
			sleep: { score: 58, grade: 3, sleepMinutes: 366 },
			heart: {
				score: 70,
				grade: 2,
				sleepHrAvg: 64.5,
				baselineHrAvg: 58,
				baselineNights: 20,
				checkWear: false
			}
		},
		computedAt: null
	};
}

const panel = () => page.getByRole('region', { name: '왜 이런 상태로 보일까요?' });

it('점수와 등급이 같아도 새 props의 활동·수면·심박 기록으로 열린 설명을 즉시 갱신한다', async () => {
	const data = lifestyleScoreFixture();
	const { rerender } = render(HomeScoreSummary, {
		open: true,
		score: data.total.score,
		statuses: homeScoreStatuses(data, false, null)
	});
	const rows = panel().getByRole('listitem');
	await expect.element(rows.nth(0)).toHaveTextContent('7,200보');
	await expect.element(rows.nth(1)).toHaveTextContent('6시간 6분');
	await expect.element(rows.nth(2)).toHaveTextContent('64.5 bpm');

	data.areas.activity.steps.value = 8400;
	data.areas.activity.exercise.minutes = 22;
	data.areas.sleep.sleepMinutes = 390;
	data.areas.heart.sleepHrAvg = 64.8;
	data.areas.heart.baselineHrAvg = 58.2;
	await rerender({ statuses: homeScoreStatuses(data, false, null) });

	await expect.element(rows.nth(0)).toHaveTextContent('8,400보');
	await expect.element(rows.nth(0)).toHaveTextContent('22분');
	await expect.element(rows.nth(0)).not.toHaveTextContent('7,200보');
	await expect.element(rows.nth(1)).toHaveTextContent('6시간 30분');
	await expect.element(rows.nth(1)).not.toHaveTextContent('6시간 6분');
	await expect.element(rows.nth(2)).toHaveTextContent('64.8 bpm');
	await expect.element(rows.nth(2)).toHaveTextContent('58.2 bpm');
	await expect.element(rows.nth(2)).toHaveTextContent('6.6 bpm 높');
	await expect.element(rows.nth(2)).not.toHaveTextContent('64.5 bpm');
	await expect
		.element(page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }))
		.toHaveAttribute('aria-expanded', 'true');
	for (const [index, score] of [85, 58, 70].entries())
		await expect.element(rows.nth(index).getByText(`${score}점`, { exact: true })).toBeVisible();
});

it('실제 0분·0점과 수면 결측·심박 비교 기준 부족을 서로 구분해 렌더링한다', async () => {
	const data = lifestyleScoreFixture();
	data.total = { score: 0, missingAreas: ['sleep', 'heart'] };
	data.areas.activity = {
		score: 0,
		grade: 4,
		steps: { score: 0, value: 800 },
		exercise: { score: 0, minutes: 0 }
	};
	data.areas.sleep = { score: null, grade: null, sleepMinutes: null };
	data.areas.heart = {
		...data.areas.heart,
		score: null,
		grade: null,
		baselineHrAvg: null,
		baselineNights: 0,
		checkWear: false
	};
	render(HomeScoreSummary, {
		open: true,
		score: data.total.score,
		statuses: homeScoreStatuses(data, false, null)
	});
	const rows = panel().getByRole('listitem');
	await expect.element(rows.nth(0).getByText('0점', { exact: true })).toBeVisible();
	await expect.element(rows.nth(0)).toHaveTextContent('800보');
	await expect.element(rows.nth(0)).toHaveTextContent('0분');
	await expect.element(rows.nth(0)).not.toHaveTextContent('기록 없음');
	await expect.element(rows.nth(1)).toHaveTextContent(/기록.*확인할 수 없/);
	await expect.element(rows.nth(1)).not.toHaveTextContent('0시간 0분');
	await expect.element(rows.nth(2)).toHaveTextContent('64.5 bpm');
	await expect.element(rows.nth(2)).toHaveTextContent(/평소.*쌓/);
	await expect.element(rows.nth(2)).not.toHaveTextContent('0 bpm');
	await expect.element(rows.nth(2)).not.toHaveTextContent('착용');
});

it('세 영역의 측정값이 없어도 저장 점수·등급을 표시하고 설명은 점수 계산 실패로 단정하지 않는다', async () => {
	const data = lifestyleScoreFixture();
	data.areas.activity.steps = { score: null, value: null };
	data.areas.activity.exercise = { score: null, minutes: null };
	data.areas.sleep.sleepMinutes = null;
	data.areas.heart = {
		...data.areas.heart,
		sleepHrAvg: null,
		baselineHrAvg: null,
		baselineNights: null,
		checkWear: null
	};
	render(HomeScoreSummary, {
		open: true,
		score: data.total.score,
		statuses: homeScoreStatuses(data, false, null)
	});
	await expect
		.element(page.getByRole('button', { name: '활동 훌륭 수면 관심 심박 안정' }))
		.toBeVisible();
	const rows = panel().getByRole('listitem');
	for (const [index, score] of [85, 58, 70].entries()) {
		await expect.element(rows.nth(index).getByText(`${score}점`, { exact: true })).toBeVisible();
		const description = rows.nth(index).element().querySelector('p');
		expect(description?.textContent).toMatch(/\S/);
		expect(description?.textContent).not.toMatch(
			/계산.*못|점수.*확인할 수 없|계산할 기록.*부족|\d+\s*(?:보|분|bpm|점)/
		);
	}
});
