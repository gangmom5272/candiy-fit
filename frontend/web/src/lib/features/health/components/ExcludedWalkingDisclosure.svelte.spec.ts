import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import ExcludedWalkingDisclosure from './ExcludedWalkingDisclosure.svelte';
import { exerciseSessions, getActivityPeriod } from '../activity';

const sessions = getActivityPeriod(0).days.map((day, index) => ({
	...exerciseSessions[0],
	id: `excluded-walk-${index}`,
	date: day.date,
	countsAsExercise: false,
	avgSpeedMps: index === 0 ? null : (3 + index / 10) / 3.6,
	durationMinutes: 20 + index
}));
const explanation = '평균 시속 4km 미만의 걷기는 운동 시간·횟수·활동 점수에 포함되지 않아요.';

it.each([1, 4, 7])(
	'%i건의 미인정 걷기를 접었다 펼쳐 날짜·속도·시간을 모두 표시한다',
	async (count) => {
		const selected = sessions.slice(0, count);
		render(ExcludedWalkingDisclosure, { sessions: selected });
		const button = page.getByRole('button', {
			name: `운동 시간에 포함되지 않은 걷기 ${count}건`,
			exact: true
		});
		await expect.element(button).toHaveAttribute('aria-expanded', 'false');
		const details = document.getElementById(button.element().getAttribute('aria-controls') ?? '');
		expect(details?.hidden).toBe(true);
		await button.click();
		await expect.element(button).toHaveAttribute('aria-expanded', 'true');
		expect(details?.querySelectorAll('li')).toHaveLength(count);
		for (const session of selected) {
			await expect
				.element(page.getByText(session.date.slice(5).replace('-', '/'), { exact: true }))
				.toBeVisible();
			const text =
				session.avgSpeedMps === null
					? '걷기'
					: `걷기 ${(session.avgSpeedMps * 3.6).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}km/h`;
			await expect.element(page.getByText(text, { exact: true })).toBeVisible();
			await expect
				.element(page.getByText(`${session.durationMinutes}분`, { exact: true }))
				.toBeVisible();
		}
		expect(details?.textContent).not.toContain('0km/h');
		await expect.element(page.getByText(explanation, { exact: true })).toBeVisible();
		await button.click();
		await expect.element(button).toHaveAttribute('aria-expanded', 'false');
		expect(details?.hidden).toBe(true);
	}
);

it('미인정 걷기가 없으면 빈 disclosure나 0건 버튼을 표시하지 않는다', () => {
	render(ExcludedWalkingDisclosure, { sessions: [] });
	expect(document.querySelector('button')).toBeNull();
	expect(document.querySelector('ul')).toBeNull();
});
