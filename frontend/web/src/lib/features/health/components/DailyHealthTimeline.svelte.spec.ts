import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import DailyHealthTimeline from './DailyHealthTimeline.svelte';
import { getDayDetailMock } from '../day-detail-mock';
import { dayTimeX, dayTimelineStart } from '../day-detail';
import '../../../../routes/layout.css';

it('시간대 걸음의 진하기는 값에 따르고 선택·기간 변경에도 좌표와 색상 계열을 보존한다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	timeline.hourlySteps = [100, 400, 100, ...Array<number>(21).fill(0)];
	const { container, rerender } = render(DailyHealthTimeline, { timeline });
	await expect.element(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
	const bars = () => [...container.querySelectorAll<SVGRectElement>('[data-step-hour]')];
	const opacity = () => bars().map((bar) => Number(getComputedStyle(bar).opacity));
	const before = opacity();
	expect(before[0]).toBeLessThan(before[1]);
	expect(before[0]).toBe(before[2]);
	expect(before[3]).toBeLessThan(before[0]);
	expect(before[3]).toBeGreaterThan(0);
	const geometry = bars().map((bar) => [bar.getAttribute('y'), bar.getAttribute('height')]);
	const colors = bars().map((bar) => getComputedStyle(bar).fill);
	container.querySelector<HTMLButtonElement>('[data-step-trigger="0"]')?.focus();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('100보');
	expect(opacity()).toEqual(before);
	expect(bars().map((bar) => [bar.getAttribute('y'), bar.getAttribute('height')])).toEqual(
		geometry
	);
	expect(bars().map((bar) => getComputedStyle(bar).fill)).toEqual(colors);
	await rerender({ timeline: { ...timeline, hourlySteps: Array<number>(24).fill(0) } });
	expect(opacity()).toEqual(Array<number>(24).fill(1));
	expect(bars().every((bar) => Number(bar.getAttribute('height')) === 0)).toBe(true);
	await rerender({ timeline: { ...timeline, hourlySteps: null } });
	expect(bars()).toHaveLength(0);
	await expect.element(page.getByText('시간대별 걸음 기록 없음')).toBeVisible();
});

it('실제 12px 시간축·범례를 쓰고 눈금 선별은 원본 격자와 데이터 좌표를 바꾸지 않는다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	const original = JSON.stringify(timeline);
	render(DailyHealthTimeline, { timeline });
	await expect
		.element(page.getByRole('list', { name: '타임라인 범례' }))
		.toHaveStyle({ fontSize: '12px' });
	const labels = [...document.querySelectorAll<HTMLElement>('[data-time-minute]')];
	expect(labels.map((label) => Number(label.dataset.timeMinute))).toContain(1440);
	expect(labels.map((label) => Number(label.dataset.timeMinute))).toContain(0);
	for (const label of labels) expect(getComputedStyle(label).fontSize).toBe('12px');
	expect(document.querySelectorAll('[data-grid-minute]').length).toBeGreaterThanOrEqual(9);
	expect(document.querySelectorAll('[data-step-hour]')).toHaveLength(24);
	expect(JSON.stringify(timeline)).toBe(original);
});

it('운동 표시는 통일하고 실제 유형과 자정 통과 원본 시간을 툴팁에 보존한다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	timeline.exerciseSegments = [
		{
			startAt: '2026-10-19T23:50:00+09:00',
			endAt: '2026-10-20T00:20:00+09:00',
			exerciseType: 79,
			typeName: '걷기',
			title: null
		},
		{
			startAt: '2026-10-19T14:20:00+09:00',
			endAt: '2026-10-19T15:10:00+09:00',
			exerciseType: 999,
			typeName: '',
			title: null
		}
	];
	const original = JSON.stringify(timeline);
	render(DailyHealthTimeline, { timeline });
	await page.getByRole('button', { name: '걷기 23:50 ~ 00:20 상세', exact: true }).click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('걷기');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('23:50~00:20');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('0시간 30분');
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('2026.10.20');
	await page
		.getByRole('region', { name: '하루 기록 시간축' })
		.getByRole('button', { name: '운동 14:20 ~ 15:10 상세', exact: true })
		.click();
	await expect.element(page.getByRole('tooltip')).toHaveTextContent('운동');
	await expect.element(page.getByRole('tooltip')).not.toHaveTextContent('걷기');
	expect(JSON.stringify(timeline)).toBe(original);
});

it('시간대 걸음 24칸을 제공하고 결측 심박 구간을 연결하지 않는다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	render(DailyHealthTimeline, { timeline });
	await expect.element(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
	expect(document.querySelectorAll('[data-step-hour]')).toHaveLength(24);
	expect(document.querySelectorAll('[data-heart-segment]')).toHaveLength(1);
	expect(document.querySelector('[data-step-hour="18"]')?.getAttribute('data-step-value')).toBe(
		String(timeline.hourlySteps?.[18])
	);
	await expect
		.element(page.getByRole('list', { name: '타임라인 범례' }))
		.toHaveTextContent('운동 구간');
});

it('심박 측정점의 높이는 시간대 평균값에 비례하고 결측에는 점을 만들지 않는다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	timeline.hourlyHeartRates = Array.from({ length: 24 }, (_, hour) =>
		hour === 0 ? { avg: 55, min: 50, max: 60 } : hour === 1 ? { avg: 110, min: 90, max: 120 } : null
	);
	render(DailyHealthTimeline, { timeline });
	await expect.element(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
	const points = [...document.querySelectorAll<HTMLElement>('[data-heart-point]')];
	expect(points).toHaveLength(2);
	expect(Number.parseFloat(points[0].style.top)).toBeGreaterThan(
		Number.parseFloat(points[1].style.top)
	);
	expect(points.map((point) => point.getAttribute('data-heart-point'))).toEqual(['55', '110']);
});

it('수면·운동·심박·걸음과 시간 label은 같은 24시간 좌표를 사용한다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	timeline.mainSleep = { startAt: '2026-10-18T18:30:00Z', endAt: '2026-10-18T19:30:00Z' };
	timeline.sleepSegments = [];
	timeline.exerciseSegments = [
		{
			startAt: timeline.mainSleep.startAt,
			endAt: timeline.mainSleep.endAt,
			exerciseType: 79,
			typeName: '걷기',
			title: null
		}
	];
	render(DailyHealthTimeline, { timeline });
	await expect.element(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
	expect(
		Number.parseFloat(document.querySelector<HTMLElement>('[data-sleep-start]')?.style.left ?? '')
	).toBeCloseTo(dayTimeX(210));
	expect(
		Number.parseFloat(
			document.querySelector<HTMLElement>('[data-exercise-start]')?.style.left ?? ''
		)
	).toBeCloseTo(dayTimeX(210));
	const bar = document.querySelector('[data-step-hour="3"]');
	expect(Number(bar?.getAttribute('x')) + Number(bar?.getAttribute('width')) / 2).toBeCloseTo(
		dayTimeX(210)
	);
	const heartX = document
		.querySelector('[data-heart-segment]')
		?.getAttribute('points')
		?.split(' ')
		.map((point) => Number(point.split(',')[0]));
	const barCenter = Number(bar?.getAttribute('x')) + Number(bar?.getAttribute('width')) / 2;
	expect(heartX?.some((x) => Math.abs(x - barCenter) < 0.000001)).toBe(true);
	expect(document.querySelector<HTMLElement>('[data-time-minute="180"]')?.style.left).toBe(
		document.querySelector<HTMLElement>('[data-grid-minute="180"]')?.style.left
	);
});

it('미수집은 막대를 만들지 않고, 실제 시간대 0보는 그대로 유지한다', async () => {
	const timeline = getDayDetailMock('2026-10-21').timeline;
	timeline.hourlySteps = Array.from({ length: 24 }, () => 0);
	render(DailyHealthTimeline, { timeline });
	await expect.element(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
	expect(document.querySelectorAll('[data-step-value="0"]')).toHaveLength(24);
	expect(document.querySelectorAll('[data-step-trigger]')).toHaveLength(24);
	await expect
		.element(page.getByRole('button', { name: '00:00 ~ 01:00 걸음 수 0보 상세', exact: true }))
		.toBeVisible();
	expect(
		[...document.querySelectorAll('[data-step-hour]')].every(
			(bar) => bar.getAttribute('height') === '0'
		)
	).toBe(true);
});

it('전체 timeline 결측에서도 각 row의 빈 상태와 날짜 축을 유지한다', async () => {
	render(DailyHealthTimeline, { timeline: getDayDetailMock('2026-10-07').timeline });
	await expect.element(page.getByText('구간 기록 없음', { exact: true })).toBeVisible();
	await expect.element(page.getByText('시간대별 심박 기록 없음', { exact: true })).toBeVisible();
	await expect.element(page.getByText('시간대별 걸음 기록 없음', { exact: true })).toBeVisible();
	expect(
		document.querySelectorAll(
			'[data-step-hour], [data-heart-segment], [data-sleep-start], [data-exercise-start]'
		)
	).toHaveLength(0);
	expect(document.querySelectorAll('[data-time-minute]')).toHaveLength(9);
	expect(document.querySelectorAll('[data-step-trigger]')).toHaveLength(0);
	for (const minute of [0, 1440]) {
		const label = document.querySelector<HTMLElement>(`[data-time-minute="${minute}"]`);
		if (!label) throw new Error('자정 눈금 없음');
		expect(getComputedStyle(label).visibility).toBe('visible');
	}
});

it.each([
	['2026-10-20', '2026-10-19T20:59:00+09:00', 0, true, -181],
	['2026-10-20', '2026-10-19T21:00:00+09:00', 0, true, -180],
	['2026-10-20', '2026-10-19T21:01:00+09:00', 0, false, -179],
	['2026-10-20', '2026-10-19T21:00:01+09:00', 0, false, -179.98333333333332],
	['2026-10-20', '2026-10-19T12:00:00Z', 0, true, -180],
	['2026-10-20', '2026-10-19T12:01:00Z', 0, false, -179],
	['2026-10-20', '2026-10-19T08:01:00-04:00', 0, false, -179],
	['2026-11-01', '2026-10-31T21:01:00+09:00', 0, false, -179],
	['2027-01-01', '2026-12-31T21:01:00+09:00', 0, false, -179],
	['2026-10-19', '2026-10-19T20:59:00+09:00', 1440, true, 0],
	['2026-10-19', '2026-10-19T21:00:00+09:00', 1440, true, 0],
	['2026-10-19', '2026-10-19T21:01:00+09:00', 1440, false, 0],
	['2026-10-19', '2026-10-19T21:00:01+09:00', 1440, false, 0],
	['2026-10-19', '2026-10-19T00:05:00+09:00', 1440, true, 0]
] as const)(
	'%s 상세의 %s 취침은 다음 자정 라벨만 정책에 따라 표시한다',
	async (date, startAt, midnightMinute, visible, rangeStart) => {
		const timeline = getDayDetailMock('2026-10-19').timeline;
		timeline.date = date;
		timeline.mainSleep = {
			startAt,
			endAt: new Date(Date.parse(startAt) + 8 * 3600000).toISOString()
		};
		timeline.sleepSegments = [{ ...timeline.mainSleep, stage: 'light' }];
		const original = JSON.stringify(timeline);
		render(DailyHealthTimeline, { timeline });
		await expect.element(page.getByRole('region', { name: '나의 하루' })).toBeVisible();
		const midnight = document.querySelector<HTMLElement>(`[data-time-minute="${midnightMinute}"]`);
		if (!midnight) throw new Error('자정 눈금 없음');
		expect(getComputedStyle(midnight).visibility).toBe(visible ? 'visible' : 'hidden');
		expect(document.querySelector(`[data-grid-minute="${midnightMinute}"]`)).not.toBeNull();
		expect(document.querySelector('[data-range-start]')?.getAttribute('data-range-start')).toBe(
			String(rangeStart)
		);
		const otherMidnight = document.querySelector<HTMLElement>(
			`[data-time-minute="${midnightMinute === 0 ? 1440 : 0}"]`
		);
		if (!otherMidnight) throw new Error('다른 자정 눈금 없음');
		expect(getComputedStyle(otherMidnight).visibility).toBe('visible');
		const bar = document.querySelector('[data-step-hour="3"]');
		expect(Number(bar?.getAttribute('x')) + Number(bar?.getAttribute('width')) / 2).toBeCloseTo(
			dayTimeX(210, rangeStart)
		);
		expect(document.querySelectorAll('[data-step-hour]')).toHaveLength(24);
		expect(JSON.stringify(timeline)).toBe(original);
	}
);

it('전날 취침을 보존하고 확장된 축에서 모든 row와 날짜 label을 함께 이동한다', async () => {
	const timeline = getDayDetailMock('2026-10-19').timeline;
	timeline.mainSleep = { startAt: '2026-10-18T23:20:00+09:00', endAt: '2026-10-19T07:00:00+09:00' };
	timeline.sleepSegments = [{ ...timeline.mainSleep, stage: 'light' }];
	const original = JSON.stringify(timeline);
	render(DailyHealthTimeline, { timeline });
	await expect.element(page.getByRole('region', { name: '하루 기록 시간축' })).toBeVisible();
	expect(document.querySelector('[data-sleep-start]')?.getAttribute('data-sleep-start')).toBe(
		'-40'
	);
	expect(document.querySelector<HTMLElement>('[data-sleep-start]')?.style.left).toBe('0%');
	const start = dayTimelineStart(timeline);
	const bar = document.querySelector('[data-step-hour="3"]');
	expect(Number(bar?.getAttribute('x')) + Number(bar?.getAttribute('width')) / 2).toBeCloseTo(
		dayTimeX(210, start)
	);
	expect(
		Number.parseFloat(
			document.querySelector<HTMLElement>('[data-time-minute="0"]')?.style.left ?? ''
		)
	).toBeCloseTo(dayTimeX(0, start));
	expect(document.querySelectorAll('[data-timeline-scroll]')).toHaveLength(1);
	expect(JSON.stringify(timeline)).toBe(original);
});
