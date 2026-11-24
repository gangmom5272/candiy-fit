import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import '../../../../routes/layout.css';
import SleepStageTimeline from './SleepStageTimeline.svelte';
import type { SleepTimelineDay } from '../sleep-detail';

it('실제 라벨 폭으로 눈금을 선별하고 리사이즈·긴 세션 변경 후에도 양끝과 원본 좌표를 유지한다', async () => {
	const previous = { width: window.innerWidth, height: window.innerHeight };
	const normal: SleepTimelineDay = {
		date: '2026-10-20',
		mainSleep: { startAt: '2026-10-19T23:00:00+09:00', endAt: '2026-10-20T07:00:00+09:00' },
		sleepSegments: [
			{ stage: 'deep', startAt: '2026-10-19T23:00:00+09:00', endAt: '2026-10-20T07:00:00+09:00' }
		]
	};
	try {
		await page.viewport(640, 900);
		const { rerender } = render(SleepStageTimeline, { timeline: normal });
		await document.fonts.ready;
		const axis = () => document.querySelector('[data-sleep-time-axis]');
		const nonOverlapping = () => {
			const labels = Array.from(axis()?.children ?? [], (label) =>
				label.getBoundingClientRect()
			).sort((a, b) => a.left - b.left);
			return (
				labels.length >= 2 &&
				labels.every((label, index) => index === 0 || labels[index - 1].right <= label.left)
			);
		};
		await expect.poll(nonOverlapping).toBe(true);
		expect(axis()?.firstElementChild?.textContent).toBe('23:00');
		expect(axis()?.lastElementChild?.textContent).toBe('07:00');
		await page.viewport(320, 900);
		await expect.poll(nonOverlapping).toBe(true);
		await rerender({
			timeline: {
				...normal,
				mainSleep: { startAt: '2026-10-19T10:40:00+09:00', endAt: '2026-10-20T07:00:00+09:00' }
			}
		});
		await expect.poll(() => axis()?.firstElementChild?.textContent).toBe('10:40');
		await expect.poll(nonOverlapping).toBe(true);
		const label = axis()?.firstElementChild;
		if (!label) throw new Error('시작 시각 없음');
		expect(getComputedStyle(label).fontSize).toBe('12px');
	} finally {
		await page.viewport(previous.width, previous.height);
	}
});

it('인접한 짧은 구간은 선택 영역을 나누고 키보드로 각 구간을 확인한다', async () => {
	const previous = { width: window.innerWidth, height: window.innerHeight };
	await page.viewport(360, 800);
	try {
		render(SleepStageTimeline, {
			timeline: {
				date: '2026-10-20',
				mainSleep: { startAt: '2026-10-19T23:59:30+09:00', endAt: '2026-10-20T00:10:00+09:00' },
				sleepSegments: [
					['00:00:00', '00:00:10'],
					['00:00:20', '00:00:25'],
					['00:00:35', '00:00:40']
				].map(([start, end]) => ({
					stage: 'deep' as const,
					startAt: `2026-10-20T${start}+09:00`,
					endAt: `2026-10-20T${end}+09:00`
				}))
			}
		});
		const points = page.getByRole('button');
		const midnight = document.querySelector('[aria-label$=" 자정 경계"] span');
		expect(midnight).not.toBeNull();
		if (!midnight) throw new Error('자정 날짜 라벨 없음');
		expect(getComputedStyle(midnight).color).toBe('rgb(204, 56, 62)');
		expect(getComputedStyle(midnight).fontSize).toBe('12px');
		const elements = [...document.querySelectorAll<HTMLButtonElement>('[data-chart-point]')];
		expect(elements).toHaveLength(3);
		for (let index = 1; index < elements.length; index++)
			expect(elements[index - 1].getBoundingClientRect().right).toBeLessThanOrEqual(
				elements[index].getBoundingClientRect().left + 0.02
			);
		for (const [index, note] of [
			'00:00:00~00:00:10',
			'00:00:20~00:00:25',
			'00:00:35~00:00:40'
		].entries()) {
			await points.nth(index).hover();
			await expect.element(page.getByRole('tooltip')).toHaveTextContent(note);
			await points.nth(index).click();
			await expect.element(page.getByRole('tooltip')).toHaveTextContent(note);
			elements[index].focus();
			await expect.element(page.getByRole('tooltip')).toHaveTextContent(note);
		}
	} finally {
		await page.viewport(previous.width, previous.height);
	}
});

it.each([1366, 360])(
	'%ipx에서 자정 통과·짧은 단계의 실제 시간과 지속 시간을 확인한다',
	async (width) => {
		const original = { width: window.innerWidth, height: window.innerHeight };
		await page.viewport(width, 900);
		try {
			render(SleepStageTimeline, {
				timeline: {
					date: '2026-10-23',
					mainSleep: { startAt: '2026-10-22T23:29:00+09:00', endAt: '2026-10-23T01:00:00+09:00' },
					sleepSegments: [
						{
							stage: 'light',
							startAt: '2026-10-22T23:29:00+09:00',
							endAt: '2026-10-23T00:14:00+09:00'
						},
						{
							stage: 'deep',
							startAt: '2026-10-23T00:14:00+09:00',
							endAt: '2026-10-23T00:14:10+09:00'
						},
						{
							stage: 'rem',
							startAt: '2026-10-23T00:15:00+09:00',
							endAt: '2026-10-23T00:45:00+09:00'
						},
						{
							stage: 'awake',
							startAt: '2026-10-23T00:45:00+09:00',
							endAt: '2026-10-23T00:50:00+09:00'
						},
						{
							stage: 'unknown',
							startAt: '2026-10-23T00:50:00+09:00',
							endAt: '2026-10-23T01:00:00+09:00'
						}
					]
				}
			});
			for (const name of [
				'얕은 수면 23:29~00:14 · 45분',
				'깊은 수면 00:14:00~00:14:10 · 1분 미만',
				'렘 수면 00:15~00:45 · 30분',
				'깨어 있음 00:45~00:50 · 5분',
				'분류되지 않음 00:50~01:00 · 10분'
			]) {
				const point = page.getByRole('button', { name, exact: true });
				expect(point.element().getBoundingClientRect().width).toBeGreaterThanOrEqual(24);
				point.element().focus();
				await expect.element(page.getByRole('tooltip')).toHaveTextContent(name.split(' · ')[1]);
				expect(page.getByRole('tooltip').element().textContent?.replace(/\s+/g, ' ')).toContain(
					name
				);
				expect(document.querySelectorAll('[role="tooltip"]')).toHaveLength(1);
			}
			await page.getByRole('button', { name: '얕은 수면 23:29~00:14 · 45분', exact: true }).hover();
			await expect.element(page.getByRole('tooltip')).toHaveTextContent('23:29~00:14 · 45분');
			expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
				document.documentElement.clientWidth
			);
		} finally {
			await page.viewport(original.width, original.height);
		}
	}
);

it('실제 시간에 비례한 구간과 단계별 row, 분류되지 않은 구간을 표시한다', async () => {
	const timeline: SleepTimelineDay = {
		date: '2026-10-23',
		mainSleep: { startAt: '2026-10-22T14:00:00Z', endAt: '2026-10-22T20:00:00Z' },
		sleepSegments: [
			{ startAt: '2026-10-22T14:00:00Z', endAt: '2026-10-22T15:00:00Z', stage: 'deep' },
			{ startAt: '2026-10-22T15:00:00Z', endAt: '2026-10-22T15:30:00Z', stage: 'awake' },
			{ startAt: '2026-10-22T15:30:00Z', endAt: '2026-10-22T16:00:00Z', stage: 'rem' },
			{ startAt: '2026-10-22T16:00:00Z', endAt: '2026-10-22T18:00:00Z', stage: 'light' },
			{ startAt: '2026-10-22T18:00:00Z', endAt: '2026-10-22T19:00:00Z', stage: 'unknown' },
			{ startAt: '2026-10-22T19:00:00Z', endAt: '2026-10-22T20:00:00Z', stage: 'unspecified' }
		]
	};
	render(SleepStageTimeline, { timeline });
	const figure = page.getByRole('figure', { name: '시간대별 수면 단계' }).element();
	const box = (stage: string) => {
		const segment = figure.querySelector(`[data-stage="${stage}"]`);
		if (!segment) throw new Error(`${stage} 구간 없음`);
		return segment.getBoundingClientRect();
	};
	expect(box('light').width / box('deep').width).toBeCloseTo(2);
	expect(box('deep').y).toBeGreaterThan(box('light').y);
	expect(box('light').y).toBeGreaterThan(box('rem').y);
	expect(box('rem').y).toBeGreaterThan(box('awake').y);
	expect(box('unknown').y).toBeGreaterThan(box('deep').y);
	expect(box('unknown').y).toBe(box('unspecified').y);
	await expect.element(page.getByText('분류되지 않음', { exact: true })).toBeVisible();
	await expect.element(page.getByLabelText('2026-10-23 자정 경계')).toBeVisible();
});

it('단계가 없으면 차트 대신 별도의 빈 상태를 제공한다', async () => {
	render(SleepStageTimeline, {
		timeline: {
			date: '2026-10-21',
			mainSleep: { startAt: '2026-10-20T16:10:00Z', endAt: '2026-10-20T22:56:00Z' },
			sleepSegments: []
		}
	});
	await expect.element(page.getByText('해당 날짜의 수면 단계 기록이 없어요.')).toBeVisible();
	await expect.element(page.getByRole('figure')).not.toBeInTheDocument();
});

it.each([1920, 1366, 768, 360])(
	'%ipx에서 범위 밖 segment는 표시만 제한하고 자정 종료 경계도 카드 안에 표시한다',
	async (width) => {
		const previous = { width: window.innerWidth, height: window.innerHeight };
		await page.viewport(width, 900);
		try {
			const timeline: SleepTimelineDay = {
				date: '2026-10-23',
				mainSleep: { startAt: '2026-10-22T15:00:00Z', endAt: '2026-10-22T22:00:00Z' },
				sleepSegments: [
					{ startAt: '2026-10-22T14:00:00Z', endAt: '2026-10-22T16:00:00Z', stage: 'deep' },
					{ startAt: '2026-10-22T21:00:00Z', endAt: '2026-10-22T23:00:00Z', stage: 'rem' },
					{ startAt: '2026-10-22T23:00:00Z', endAt: '2026-10-23T00:00:00Z', stage: 'light' }
				]
			};
			const { rerender } = render(SleepStageTimeline, { timeline });
			const figure = page.getByRole('figure', { name: '시간대별 수면 단계' }).element();
			expect(figure.querySelectorAll('[data-stage]')).toHaveLength(2);
			expect(figure.querySelector('[data-stage="light"]')).toBeNull();
			for (const segment of figure.querySelectorAll('[data-stage]')) {
				const rect = segment.getBoundingClientRect();
				const parent = segment.parentElement?.getBoundingClientRect();
				if (!parent) throw new Error('단계 row 없음');
				expect(rect.left).toBeGreaterThanOrEqual(parent.left);
				expect(rect.right).toBeLessThanOrEqual(parent.right + 1);
			}
			await rerender({
				timeline: {
					date: '2026-10-23',
					mainSleep: { startAt: '2026-10-22T23:00:00+09:00', endAt: '2026-10-23T00:00:00+09:00' },
					sleepSegments: [
						{
							startAt: '2026-10-22T23:00:00+09:00',
							endAt: '2026-10-23T00:00:00+09:00',
							stage: 'light'
						}
					]
				}
			});
			await expect.element(page.getByLabelText('2026-10-23 자정 경계')).toBeVisible();
			const label = page.getByText('10/23', { exact: true }).element().getBoundingClientRect();
			const outer = page
				.getByRole('region', { name: '수면 단계', exact: true })
				.element()
				.getBoundingClientRect();
			expect(label.right).toBeLessThanOrEqual(outer.right);
			expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
				document.documentElement.clientWidth
			);
		} finally {
			await page.viewport(previous.width, previous.height);
		}
	}
);
