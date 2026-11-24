import type { SleepTimelineDay } from '../features/health/sleep-detail';
import { sleepResponsiveFixture } from './sleep-responsive-fixture';

/** 단일 KST 기상일의 요약과 타임라인을 같은 세션에서 만든다. */
export function sleepDetailFixture(day: SleepTimelineDay, sleepMinutes?: number | null) {
	const { date } = day;
	const mainSleep = day.mainSleep
		? {
				startAt: new Date(day.mainSleep.startAt).toISOString(),
				endAt: new Date(day.mainSleep.endAt).toISOString()
			}
		: null;
	const segments = day.sleepSegments
		.map((segment) => ({
			...segment,
			startAt: new Date(segment.startAt).toISOString(),
			endAt: new Date(segment.endAt).toISOString()
		}))
		.sort((a, b) => Date.parse(a.startAt) - Date.parse(b.startAt));
	const sleep = sleepResponsiveFixture('sleeps', date, date, 'normal', '2026-11-10');
	const oxygen = sleepResponsiveFixture('oxygen_saturations', date, date, 'normal', '2026-11-10');
	const duration = (stage: string) =>
		Math.round(
			segments
				.filter((segment) => segment.stage === stage)
				.reduce(
					(sum, segment) => sum + (Date.parse(segment.endAt) - Date.parse(segment.startAt)) / 60000,
					0
				)
		);
	const value = mainSleep
		? sleepMinutes === undefined
			? duration('deep') + duration('light') + duration('rem')
			: sleepMinutes
		: null;
	sleep.days[0] = {
		...sleep.days[0],
		date,
		value,
		missing: value === null,
		reason: value === null ? (mainSleep ? 'NULL_VALUE' : 'NO_RECORD') : null,
		isInProgress: false
	};
	sleep.summary = {
		...sleep.summary,
		avg: value,
		dailyMin: value === null ? null : { date, value },
		dailyMax: value === null ? null : { date, value },
		daysWithData: value === null ? 0 : 1,
		daysUsedForSummary: value === null ? 0 : 1,
		missingDays: value === null ? [date] : [],
		unsyncedDays: []
	};
	sleep.detail.nights = mainSleep
		? sleep.detail.nights.map((night) => ({
				...night,
				...mainSleep,
				sleepMinutes: value,
				sessionMinutes: Math.round(
					(Date.parse(mainSleep.endAt) - Date.parse(mainSleep.startAt)) / 60000
				),
				deepMinutes: segments.length ? duration('deep') : null,
				lightMinutes: segments.length ? duration('light') : null,
				remMinutes: segments.length ? duration('rem') : null,
				awakeMinutes: segments.length ? duration('awake') : null,
				unknownMinutes: segments.length ? duration('unknown') : null,
				unspecifiedMinutes: segments.length ? duration('unspecified') : null
			}))
		: [];
	if (!mainSleep) {
		oxygen.days[0] = {
			...oxygen.days[0],
			date,
			value: null,
			missing: true,
			reason: 'NO_RECORD',
			isInProgress: false
		};
		oxygen.summary = {
			...oxygen.summary,
			avg: null,
			dailyMin: null,
			dailyMax: null,
			daysWithData: 0,
			daysUsedForSummary: 0,
			missingDays: [date],
			unsyncedDays: []
		};
		oxygen.detail.dailyRanges = oxygen.detail.dailyRanges.map((range) => ({
			...range,
			min: null,
			max: null
		}));
		oxygen.detail.measuredMin = oxygen.detail.measuredMax = null;
	}
	return {
		sleep,
		oxygen,
		timeline: {
			period: { start: date, end: date },
			days: [
				{
					...day,
					mainSleep,
					sleepSegments: segments,
					isInProgress: false,
					hourlySteps: null,
					hourlyHeartRates: null,
					exerciseSegments: []
				}
			]
		}
	};
}
