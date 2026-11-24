import type { SleepMetricResult } from '../features/health/sleep';
const dateShift = (date: string, n: number) =>
	new Date(Date.parse(date) + n * 86400000).toISOString().slice(0, 10);
type Scenario =
	| 'normal'
	| 'empty'
	| 'missing'
	| 'one-first'
	| 'one-mid'
	| 'one-last'
	| 'equal'
	| 'extreme'
	| 'short'
	| 'sleep-only'
	| 'oxygen-only'
	| 'increase'
	| 'decrease'
	| 'neutral'
	| 'error'
	| 'error-long'
	| 'loading'
	| 'oxygen-error'
	| 'previous-error';
// Sleep uses completed wake dates in KST, including today.
export function sleepResponsiveFixture(
	metric: SleepMetricResult['metric'],
	start: string,
	end: string,
	kind: Scenario = 'normal',
	today = '2026-11-09'
) {
	const state = { kind, today };
	const dates = Array.from(
		{ length: (Date.parse(end) - Date.parse(start)) / 86400000 + 1 },
		(_, i) => dateShift(start, i)
	);
	let sleeps: (number | null)[] = [450, 420, 480, 390, 510, 460, 440],
		oxygen: (number | null)[] = [97, 96, 98, 97, 95, 96, 97];
	if (state.kind === 'empty') sleeps = oxygen = Array(7).fill(null);
	if (state.kind === 'missing') {
		sleeps = [null, 420, null, null, 480, 0, null];
		oxygen = [null, 97, null, null, 96, 95, null];
	}
	if (state.kind.startsWith('one-')) {
		const i = state.kind === 'one-first' ? 0 : state.kind === 'one-mid' ? 3 : 6;
		sleeps = Array(7).fill(null);
		oxygen = Array(7).fill(null);
		sleeps[i] = 480;
		oxygen[i] = 97;
	}
	if (state.kind === 'equal' || state.kind === 'neutral') {
		sleeps = Array(7).fill(480);
		oxygen = Array(7).fill(97);
	}
	if (state.kind === 'extreme') {
		sleeps = [0, 1, 1200, 480, 60, 720, 0];
		oxygen = [1, 100, 90.1, 100, 0.1, 98, 1];
	}
	if (state.kind === 'short') {
		sleeps = Array(7).fill(90);
		oxygen = Array(7).fill(97);
	}
	if (state.kind === 'sleep-only') oxygen = Array(7).fill(null);
	if (state.kind === 'oxygen-only') sleeps = Array(7).fill(null);
	const current = end === state.today;
	if (!current && state.kind === 'increase')
		sleeps = sleeps.map((v) => (v === null ? null : Math.max(0, v - 30)));
	if (!current && ['normal', 'decrease'].includes(state.kind))
		sleeps = sleeps.map((v) => (v === null ? null : v + 30));
	const minO = (v: number) =>
		v === 100 || v === 0.1 ? v : Math.max(0.1, Number((v - 1).toFixed(1)));
	const maxO = (v: number) =>
		v === 100 || v === 0.1 ? v : Math.min(100, Number((v + 1).toFixed(1)));
	const days = dates.map<SleepMetricResult['days'][number] & { wearHours: null; isWellWorn: null }>(
		(date, i) => {
			const value = (metric === 'sleeps' ? sleeps : oxygen)[i % 7];
			const hasNight = sleeps[i % 7] !== null || oxygen[i % 7] !== null;
			return {
				date,
				value,
				missing: value === null,
				reason:
					value !== null
						? null
						: date >= state.today
							? 'UNSYNCED'
							: hasNight
								? 'NULL_VALUE'
								: 'NO_RECORD',
				isInProgress: false,
				wearHours: null,
				isWellWorn: null
			};
		}
	);
	const valid = days.filter((d) => !d.missing),
		vals = valid.map((d) => d.value!),
		sum = vals.reduce((a, b) => a + b, 0);
	const extreme = (v: number) => {
		const d = valid.find((d) => d.value === v);
		return d ? { date: d.date, value: v } : null;
	};
	const ranges =
		metric === 'oxygen_saturations'
			? days.map((d) => ({
					date: d.date,
					min: d.missing ? null : minO(d.value!),
					max: d.missing ? null : maxO(d.value!),
					sampleCount: null
				}))
			: [];
	const measured = (key: 'min' | 'max') => {
		const rs = ranges
			.filter((r) => r[key] !== null)
			.sort((a, b) => (key === 'min' ? a.min! - b.min! : b.max! - a.max!));
		return rs[0] ? { date: rs[0].date, value: rs[0][key]! } : null;
	};
	const nights =
		metric === 'sleeps'
			? dates.flatMap((date, i) => {
					const minutes = sleeps[i % 7],
						o = oxygen[i % 7];
					if (minutes === null && o === null) return [];
					const endAt = new Date(date + 'T07:00:00+09:00'),
						session = (minutes ?? 480) + 20,
						startAt = new Date(endAt.getTime() - session * 60000);
					const deep = minutes === null ? null : Math.floor(minutes * 0.2),
						rem = minutes === null ? null : Math.floor(minutes * 0.2),
						light = minutes === null ? null : minutes - deep! - rem!;
					const low = [o === null ? null : minO(o), o, o === null ? null : maxO(o)].flatMap(
						(v, j) =>
							v !== null && v < 90
								? [
										new Date(endAt.getTime() - (15 - j * 5) * 60000)
											.toISOString()
											.replace('.000Z', 'Z')
									]
								: []
					);
					return [
						{
							date,
							startAt: startAt.toISOString(),
							endAt: endAt.toISOString(),
							sleepMinutes: minutes,
							deepMinutes: deep,
							remMinutes: rem,
							lightMinutes: light,
							awakeMinutes: 20,
							sessionMinutes: session,
							unspecifiedMinutes: 0,
							unknownMinutes: 0,
							avgHeartRate: 65,
							minHeartRate: 55,
							maxHeartRate: 80,
							spo2Avg: o,
							spo2Min: o === null ? null : minO(o),
							spo2Max: o === null ? null : maxO(o),
							spo2Count: o === null ? null : 3,
							spo2LowCount: o === null ? null : low.length,
							spo2LowTimes: o === null ? null : low
						}
					];
				})
			: [];
	const data: SleepMetricResult = {
		metric,
		unit: metric === 'sleeps' ? 'min' : '%',
		period: { start, end },
		summary: {
			avg: valid.length ? Math.round((sum / valid.length) * 100) / 100 : null,
			dailyMin: valid.length ? extreme(Math.min(...vals)) : null,
			dailyMax: valid.length ? extreme(Math.max(...vals)) : null,
			total: null,
			daysWithData: valid.length,
			daysUsedForSummary: valid.length,
			completedDays: days.length,
			missingDays: days
				.filter((d) => d.reason === 'NO_RECORD' || d.reason === 'NULL_VALUE')
				.map((d) => d.date),
			unsyncedDays: days.filter((d) => d.reason === 'UNSYNCED').map((d) => d.date),
			linkedFrom: '2026-01-01'
		},
		days,
		inProgress: null,
		dataStatus: {
			lastDataAt: state.today + 'T02:50:00Z',
			syncedUntil: state.today + 'T02:55:00Z',
			lastSyncStatus: 'success',
			lastSuccessAt: state.today + 'T02:58:00Z',
			checkedAt: state.today + 'T03:00:00Z'
		},
		detail: {
			nights,
			dailyRanges: ranges,
			measuredMin: metric === 'oxygen_saturations' ? measured('min') : null,
			measuredMax: metric === 'oxygen_saturations' ? measured('max') : null,
			sessions: []
		}
	};

	return data;
}
