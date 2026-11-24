import {
	formatDayWithWeekday,
	formatExerciseActivity,
	formatMetricValue,
	formatPeriod,
	formatShortDate
} from './format';
import { BODY_METRICS, EXERCISE_ACTIVITY_FIELDS } from './types';
import type {
	EvidenceSnapshot,
	ExerciseActivityDay,
	ExerciseActivityField,
	ExerciseActivitySource,
	ExerciseSession,
	HourlyStepsSnapshot,
	MissingReason,
	SleepNight,
	SleepSessionsSnapshot
} from './types';

type Day = EvidenceSnapshot['days'][number];

/** 기록이 이만큼은 있어야 평균선·최저 색이 뜻을 가진다. 하루뿐이면 평균·최저가 모두 그 값이다 */
export const MIN_DAYS_FOR_TREND = 2;
/** 이보다 길면 막대를 주 단위로 묶는다. AI 근거 기간은 최대 31일(이번 달·지난달)이다 */
export const MAX_DAILY_BARS = 31;
/** 날짜를 모두 나열하는 최대 일수. 넘으면 주요 날짜만 먼저 보여 주고 전체는 펼쳐서 본다 */
export const MAX_LISTED_DAYS = 14;
/**
 * 기간 비교를 믿을 만하다고 보는 기준. AI의 reliable 판단(data/docs/calculation-rules.md 4장,
 * ai/src/coach_ai/tools/handlers.py RELIABLE_MIN_DAYS·RELIABLE_MIN_SHARE)과 같게 둬서
 * AI가 "기록이 적다"고 말한 비교를 화면이 강조하지 않게 한다
 */
export const RELIABLE_MIN_DAYS = 3;
export const RELIABLE_MIN_SHARE = 1 / 2;

export const MISSING_REASON_LABELS: Record<MissingReason, string> = {
	NO_RECORD: '기록 없음',
	NULL_VALUE: '값 없음',
	ZERO_STEPS: '0걸음',
	UNSYNCED: '동기화 전',
	BEFORE_LINK: '연동 전'
};

export type Coverage = {
	/** 통계 대상 날짜 수(명세 completed_days). 연동 전·진행 중인 오늘은 빠진다 */
	countedDays: number;
	/** 그중 값이 있는 날 수(명세 days_with_data) */
	recordedDays: number;
	/** 기간 전체 날짜 수 */
	periodDays: number;
	kind: 'none' | 'single' | 'partial' | 'full';
	/** 평균이 있고 기록이 3일 이상이면서 대상 날짜의 절반 이상. 아니면 비교는 참고용이다 */
	isReliable: boolean;
	/** 수집 중인 오늘 값. 통계(recordedDays)에는 빠지지만 "오늘 몇 걸음?" 답변은 이 값을 쓴다 */
	today: TodayValue | null;
};

export type TodayValue = { date: string; value: number; asOf: string | null };

/** 명세 in_progress(걸음·심박·운동)에 값이 있으면 그 값, 없으면 days의 진행 중인 날 값 */
export function todayValue(snapshot: EvidenceSnapshot): TodayValue | null {
	const progress = snapshot.in_progress;
	if (progress && !progress.missing && progress.value !== null)
		return { date: progress.date, value: progress.value, asOf: progress.as_of };
	const day = snapshot.days.find((entry) => entry.is_in_progress && !entry.missing);
	return day?.value != null ? { date: day.date, value: day.value, asOf: null } : null;
}

export function coverage(snapshot: EvidenceSnapshot): Coverage {
	const { completed_days: countedDays, days_with_data: recordedDays } = snapshot.summary;
	const kind =
		recordedDays === 0
			? 'none'
			: recordedDays === 1
				? 'single'
				: recordedDays < countedDays
					? 'partial'
					: 'full';
	return {
		countedDays,
		recordedDays,
		periodDays: snapshot.days.length,
		kind,
		isReliable:
			snapshot.summary.avg !== null &&
			recordedDays >= RELIABLE_MIN_DAYS &&
			recordedDays >= countedDays * RELIABLE_MIN_SHARE,
		today: todayValue(snapshot)
	};
}

/**
 * 지표 묶음의 기록 상태. 나눠 조회한 기록을 합친 묶음은 평균을 다시 내지 않아 평균이 비어 있으므로,
 * 믿을 만한지는 기록된 날 수로만 본다(3일 이상이면서 대상 날짜의 절반 이상)
 */
export function groupCoverage(group: {
	current: { snapshot: EvidenceSnapshot };
	parts: unknown[];
}): Coverage {
	const cover = coverage(group.current.snapshot);
	if (group.parts.length <= 1) return cover;
	return {
		...cover,
		isReliable:
			cover.recordedDays >= RELIABLE_MIN_DAYS &&
			cover.recordedDays >= cover.countedDays * RELIABLE_MIN_SHARE
	};
}

/** 근거 기간 글자. 하루면 `2026.10.27`, 아니면 `2026.10.22 ~ 10.28` (같은 날짜를 두 번 쓰지 않는다) */
export function formatEvidencePeriod(start: string, end: string): string {
	return start === end ? start.replaceAll('-', '.') : formatPeriod(start, end);
}

/**
 * 근거 카드에 지표 이름 옆에 붙일 기록 상태. 기록이 부족할 때(AI 기준 미달)만 붙이고,
 * 7일 중 5일처럼 충분하면 null이라 평소 카드는 183 이전과 같다. 하루 기간은 일수가 뜻이 없어 쓰지 않는다
 */
export function coverageNote(cover: Coverage): string | null {
	if (cover.recordedDays === 0) {
		if (!cover.today) return '기록 없음';
		return cover.periodDays === 1 ? '수집 중' : '오늘만 수집 중';
	}
	if (cover.isReliable || cover.periodDays === 1) return null;
	return `${cover.countedDays}일 중 ${cover.recordedDays}일`;
}

/** 지표에 보여 줄 값이 하나라도 있는지. 지난날 기록이 없어도 수집 중인 오늘 값이 있으면 있다 */
export function hasAnyValue(cover: Coverage): boolean {
	return cover.recordedDays > 0 || cover.today !== null;
}

/**
 * 막대를 그릴 만한지. 기간이 하루이거나 기록이 하루 이하면 막대 하나·빈 칸뿐이라 요약과 같은 내용이 된다.
 * 기록이 모두 0(운동 0분 등)이어도 빈 차트와 "평균 0" 선뿐이라 요약·날짜 목록으로 보여 준다
 */
export function showsChart(snapshot: EvidenceSnapshot): boolean {
	return (
		snapshot.days.length > 1 &&
		snapshot.summary.days_with_data >= MIN_DAYS_FOR_TREND &&
		snapshot.summary.daily_max?.value !== 0
	);
}

/**
 * 차트를 그리지 않은 이유. 값은 있는데 차트가 없으면 "차트가 안 나온다"고 읽혀(247 테스트 피드백)
 * 차트 자리에 한 줄로 알린다. 보여 줄 값이 없으면 "기록이 없어요" 안내가 따로 있어 null이다
 */
export function chartSkipReason(snapshot: EvidenceSnapshot): string | null {
	if (showsChart(snapshot)) return null;
	const cover = coverage(snapshot);
	if (!hasAnyValue(cover)) return null;
	if (cover.periodDays === 1) return '하루 기록이라 차트 대신 값으로 보여 줘요.';
	if (cover.recordedDays === 0) return '아직 오늘 기록만 있어 차트를 그리지 않았어요.';
	if (cover.recordedDays === 1) return '기록된 날이 하루뿐이라 차트를 그리지 않았어요.';
	return '모든 날 기록이 0이라 차트 대신 날짜별 기록으로 보여 줘요.';
}

export type ChartBar = {
	key: string;
	/** 축 아래 날짜 글자 */
	axisLabel: string;
	/** 넓은 화면(sm 이상)에서 축 글자를 보일지. 7칸 이하는 모두, 그보다 많으면 6개 안팎만 */
	labelWide: boolean;
	/** 휴대폰 폭에서 축 글자를 보일지. 처음·가운데·끝 3개만 */
	labelCompact: boolean;
	/** 보조기기·툴팁용 날짜(범위) */
	title: string;
	value: number | null;
	inProgress: boolean;
};

export type ChartBars = { unit: 'day' | 'week'; bars: ChartBar[] };

/** 축 글자를 둘 칸. "10.01" 다섯 글자가 휴대폰 폭에 3개, 넓은 화면에 6~7개까지 겹치지 않는다 */
function labelSlots(index: number, count: number): Pick<ChartBar, 'labelWide' | 'labelCompact'> {
	const every = count <= 7 ? 1 : Math.ceil(count / 6);
	return {
		labelWide: index % every === 0,
		labelCompact: count <= 3 || [0, Math.floor((count - 1) / 2), count - 1].includes(index)
	};
}

/** 연동 전 날짜는 비교할 기록이 생길 수 없으므로 차트에서 뺀다 */
function chartDays(days: Day[]): Day[] {
	return days.filter((day) => day.reason !== 'BEFORE_LINK');
}

function average(values: number[]): number | null {
	return values.length === 0 ? null : values.reduce((sum, value) => sum + value, 0) / values.length;
}

/** 주 막대 아래에 "10.13 ~ 10.19"처럼 기간을 적는 최대 주 수. 넘으면 글자가 서로 닿는다 */
const MAX_WEEK_RANGE_LABELS = 6;

/**
 * 하루 한 칸짜리 막대. 기간이 길어 차트가 주 단위로 묶여도, 가로로 넘겨 보는 날짜별 수면 단계는 이 칸을 쓴다(S15P31A403-418)
 */
export function dayBars(snapshot: EvidenceSnapshot): ChartBar[] {
	const days = chartDays(snapshot.days);
	return days.map((day, index) => ({
		key: day.date,
		axisLabel: formatShortDate(day.date),
		...labelSlots(index, days.length),
		// 축 글자는 칸이 좁아 날짜만, 툴팁·보조기기용 이름에는 요일까지 둔다
		title: formatDayWithWeekday(day.date),
		value: day.missing ? null : day.value,
		inProgress: day.is_in_progress
	}));
}

/**
 * 차트 막대. 31일 이하는 하루 한 칸, 더 길면 7일씩 묶은 하루 평균으로 그린다.
 * 신체 지표(체중 등)는 평균을 다시 내지 않고 그 주의 마지막 측정값을 쓴다(백엔드 합의, S15P31A403-311).
 * 진행 중인 오늘은 명세상 통계에서 빠지므로 주 값에도 넣지 않는다.
 */
export function chartBars(snapshot: EvidenceSnapshot): ChartBars {
	const days = chartDays(snapshot.days);
	if (days.length <= MAX_DAILY_BARS) return { unit: 'day', bars: dayBars(snapshot) };

	const weeks: Day[][] = [];
	for (let index = 0; index < days.length; index += 7) weeks.push(days.slice(index, index + 7));
	return {
		unit: 'week',
		bars: weeks.map((week, index) => {
			const first = week[0].date;
			const last = week[week.length - 1].date;
			const values = week.flatMap((day) =>
				day.missing || day.is_in_progress || day.value === null ? [] : [day.value]
			);
			return {
				key: first,
				// 막대 하나가 한 주라는 것이 보이게 기간으로 적는다. 주가 많으면 자리가 없어 첫날만 둔다(S15P31A403-418)
				axisLabel:
					weeks.length <= MAX_WEEK_RANGE_LABELS
						? `${formatShortDate(first)} ~ ${formatShortDate(last)}`
						: formatShortDate(first),
				...labelSlots(index, weeks.length),
				title: `${formatShortDate(first)} ~ ${formatShortDate(last)}`,
				value: BODY_METRICS.includes(snapshot.metric) ? (values.at(-1) ?? null) : average(values),
				inProgress: false
			};
		})
	};
}

export type DayListItem =
	| { kind: 'day'; date: string; value: number; inProgress: boolean }
	| { kind: 'gap'; start: string; end: string; count: number; reason: MissingReason | null };

/** 날짜별 목록. 이어지는 기록 없는 날은 이유가 같으면 한 줄(`10.21 ~ 10.26 · 기록 없음`)로 묶는다 */
export function dayListItems(days: Day[]): DayListItem[] {
	const items: DayListItem[] = [];
	for (const day of days) {
		if (!day.missing && day.value !== null) {
			items.push({ kind: 'day', date: day.date, value: day.value, inProgress: day.is_in_progress });
			continue;
		}
		const previous = items[items.length - 1];
		if (previous?.kind === 'gap' && previous.reason === day.reason) {
			previous.end = day.date;
			previous.count += 1;
		} else {
			items.push({ kind: 'gap', start: day.date, end: day.date, count: 1, reason: day.reason });
		}
	}
	return items;
}

export type DayHighlight = { label: string; date: string; value: number };

/**
 * 가장 높은 날·낮은 날을 부르는 말(S15P31A403-427). 잠과 걸음을 "높다·낮다"로 부르면 어색해 지표에 맞는 말을 쓴다.
 * 심박·산소포화도·신체 지표는 수치의 높낮이가 그대로 뜻이라 "가장 높은 날·낮은 날"로 둔다
 */
const HIGHLIGHT_LABELS: Partial<Record<EvidenceSnapshot['metric'], [string, string]>> = {
	sleeps: ['가장 길게 잔 날', '가장 짧게 잔 날'],
	steps: ['가장 많이 걸은 날', '가장 적게 걸은 날'],
	exercises: ['가장 오래 운동한 날', '가장 짧게 운동한 날']
};

/** 지표에 맞는 가장 높은 날·낮은 날 이름. 수면 달력의 표시 이름에도 쓴다(S15P31A403-430) */
export function highlightLabels(metric: EvidenceSnapshot['metric']): [string, string] {
	return HIGHLIGHT_LABELS[metric] ?? ['가장 높은 날', '가장 낮은 날'];
}

/** 긴 기간에서 먼저 보여 줄 날: 가장 높은 날·낮은 날·오늘(진행 중) */
export function dayHighlights(snapshot: EvidenceSnapshot): DayHighlight[] {
	const { daily_max: max, daily_min: min } = snapshot.summary;
	const [highest, lowest] = HIGHLIGHT_LABELS[snapshot.metric] ?? ['가장 높은 날', '가장 낮은 날'];
	const highlights: DayHighlight[] = [];
	if (max) highlights.push({ label: highest, ...max });
	if (min && min.date !== max?.date) highlights.push({ label: lowest, ...min });
	const today = snapshot.days.find(
		(day) => day.is_in_progress && !day.missing && day.value !== null
	);
	if (today?.value != null)
		highlights.push({ label: '오늘(수집 중)', date: today.date, value: today.value });
	return highlights;
}

/** 날짜별 목록을 모두 펼쳐 둘지 */
export function listsAllDays(snapshot: EvidenceSnapshot): boolean {
	return snapshot.days.length <= MAX_LISTED_DAYS;
}

/** 기간에서 연동 전이라 뺀 날 수 */
export function beforeLinkDays(snapshot: EvidenceSnapshot): number {
	return snapshot.days.filter((day) => day.reason === 'BEFORE_LINK').length;
}

type DatedValue = { date: string; value: number };

/**
 * 심박·산소포화도의 실제 측정 최저·최고(명세 detail.measured_min/max). 하루 평균의 최저·최고와 다르다.
 * "지난주 화요일 최고 심박"처럼 답변이 이 값을 말하므로 근거 창에도 보여 준다. 진행 중인 오늘은 명세상 빠진다
 */
export function measuredRange(
	snapshot: EvidenceSnapshot
): { min: DatedValue | null; max: DatedValue | null } | null {
	if (snapshot.metric !== 'heart_rates' && snapshot.metric !== 'oxygen_saturations') return null;
	const { measured_min: min, measured_max: max } = snapshot.detail;
	return min || max ? { min, max } : null;
}

const SLEEP_STAGES = [
	['deep_minutes', '깊은 잠'],
	['rem_minutes', '렘수면'],
	['light_minutes', '얕은 잠'],
	['awake_minutes', '깬 시간']
] as const satisfies readonly (readonly [keyof SleepNight, string])[];

export type StageAverage = { label: string; minutes: number; nights: number };

/**
 * 수면 단계별 밤 평균(분). 단계 값이 있는 밤만 평균하고, 어느 밤에도 없는 단계는 뺀다.
 * 워치에 따라 단계가 없는 밤이 있어 단계마다 센 밤 수가 다를 수 있다
 */
export function sleepStageAverages(snapshot: EvidenceSnapshot): StageAverage[] {
	return SLEEP_STAGES.flatMap(([key, label]) => {
		const values = snapshot.detail.nights.flatMap((night) =>
			night[key] === null ? [] : [night[key] as number]
		);
		const avg = average(values);
		return avg === null ? [] : [{ label, minutes: Math.round(avg), nights: values.length }];
	});
}

/** 처음부터 펼쳐 둘 운동 기록 수. 넘으면 나머지는 펼쳐서 본다 */
export const MAX_LISTED_SESSIONS = 8;

export type SessionItem = ExerciseSession & { isLongest: boolean };

/**
 * 운동 기록. 최근 것부터, 인정된 운동 중 가장 긴 것 하나에 표시를 붙인다("가장 오래 운동한 날" 답변 근거).
 * 인정되지 않은 기록(느린 걷기 등)도 명세대로 목록에는 남긴다
 */
export function sessionItems(snapshot: EvidenceSnapshot): SessionItem[] {
	const sessions = [...snapshot.detail.sessions].sort((a, b) =>
		b.start_at.localeCompare(a.start_at)
	);
	// 견줄 기록이 있어야 "가장 길게"가 뜻이 있다. 인정된 운동이 한 건뿐이면 표시하지 않는다(S15P31A403-439)
	const counted = sessions.filter((session) => session.counts_as_exercise);
	const longest =
		counted.length > 1
			? counted.reduce((best, session) =>
					session.duration_minutes > best.duration_minutes ? session : best
				)
			: null;
	return sessions.map((session) => ({ ...session, isLongest: session === longest }));
}

/**
 * 수면 중 산소포화도 90% 미만 상태(S15P31A403-312, 백엔드 합의 네 가지).
 * - 횟수 null · 시각 null → 그 밤에 측정 없음
 * - 횟수 숫자 · 시각 null → 재집계 전이라 횟수만 있음
 * - 0 · [] → 측정은 있고 90% 미만 없음
 * - N · 시각 N개 → 시각 목록
 * null 을 빈 배열로 바꾸지 않는다. 키가 없는 예전 근거(undefined)는 표시하지 않는다(null 반환)
 */
export type Spo2LowState =
	| { kind: 'unmeasured' }
	| { kind: 'count-only'; count: number }
	| { kind: 'none' }
	| { kind: 'times'; times: string[] };

export function spo2LowState(
	count: number | null | undefined,
	times: string[] | null | undefined
): Spo2LowState | null {
	if (count === undefined && times === undefined) return null;
	// Z 와 +09:00 이 섞여 와도 실제 시각 순으로 둔다(글자 순 정렬은 틀린다)
	if (times && times.length > 0)
		return { kind: 'times', times: [...times].sort((a, b) => Date.parse(a) - Date.parse(b)) };
	if (Array.isArray(times) || count === 0) return { kind: 'none' };
	if (typeof count === 'number') return { kind: 'count-only', count };
	return { kind: 'unmeasured' };
}

/** 펼치기 전에 보여 줄 시각 수. 나머지는 "외 N회"로 접는다 */
export const SPO2_TIMES_PREVIEW = 3;

/** 공통 수면 근거의 밤 목록에 산소포화도 칸이 하나라도 있으면(새 근거) 날짜별로 보여 준다 */
export function nightSpo2Items(
	snapshot: EvidenceSnapshot
): { key: string; date: string; state: Spo2LowState; average: number | null }[] {
	return [...snapshot.detail.nights]
		.sort((a, b) => a.start_at.localeCompare(b.start_at))
		.flatMap((night) => {
			const state = spo2LowState(night.spo2_low_count, night.spo2_low_times);
			// 그 밤의 평균을 함께 싣는다. 90% 미만 여부만으로는 평소 수준을 알 수 없다(S15P31A403-388)
			return state
				? [
						{
							key: night.start_at,
							date: night.date,
							state,
							average: night.spo2_avg ?? null
						}
					]
				: [];
		});
}

/** 날짜별 산소포화도를 접지 않고 다 보여 주는 최대 밤 수. 한 주는 그대로, 더 길면 요약을 먼저 보여 준다 */
export const MAX_LISTED_SPO2_NIGHTS = 7;

/** date 는 기록 날짜(깬 날). 창의 다른 날짜와 같은 기준이다 */
export type Spo2Point = { value: number; date: string };

/**
 * 긴 기간의 수면 중 산소포화도 요약(S15P31A403-388, 앱 384와 같다). 한 달처럼 밤이 많으면 목록이 화면을 다 차지해
 * 평균·최저·최고를 먼저 보여 주고 날짜별 목록은 접어 둔다.
 * - average: 날짜별 평균을 다시 평균한 값. 근거에 기간 평균이 따로 실려 오지 않아 화면에서 낸다(AI 가 받은 값이 아니다)
 * - lowest·highest: 밤마다 실려 온 최저·최고 중에서 고른 값(새로 계산하지 않는다)
 * - nights: 산소포화도 칸이 있는 밤 수, lowNights: 90% 미만이 한 번이라도 있었던 밤 수
 * 밤이 MAX_LISTED_SPO2_NIGHTS 이하면 null(목록을 그대로 보여 준다)
 */
export function spo2Summary(snapshot: EvidenceSnapshot): {
	average: number | null;
	lowest: Spo2Point | null;
	highest: Spo2Point | null;
	nights: number;
	lowNights: number;
} | null {
	const nights = snapshot.detail.nights.filter(
		(night) => spo2LowState(night.spo2_low_count, night.spo2_low_times) !== null
	);
	if (nights.length <= MAX_LISTED_SPO2_NIGHTS) return null;
	const averages = nights.flatMap((night) =>
		night.spo2_avg === undefined ? [] : [night.spo2_avg]
	);
	const pick = (key: 'spo2_min' | 'spo2_max', better: (a: number, b: number) => boolean) =>
		nights.reduce<Spo2Point | null>((best, night) => {
			const value = night[key];
			return value !== undefined && (best === null || better(value, best.value))
				? { value, date: night.date }
				: best;
		}, null);
	return {
		average:
			averages.length === 0
				? null
				: averages.reduce((sum, value) => sum + value, 0) / averages.length,
		lowest: pick('spo2_min', (a, b) => a < b),
		highest: pick('spo2_max', (a, b) => a > b),
		nights: nights.length,
		lowNights: nights.filter((night) => (night.spo2_low_count ?? 0) > 0).length
	};
}

/** 요약 아래 안내. 목록을 접으면 90% 미만이 있었는지가 가려지므로 그 밤 수만 알린다 */
export function spo2SummaryNote(lowNights: number): string {
	return lowNights > 0
		? `90% 미만으로 측정된 날은 ${lowNights}일이에요.`
		: '90% 미만으로 측정된 날은 없어요.';
}

/**
 * 수면 단계 평균 아래 안내. 며칠 중 며칠의 평균인지를 날 수로 말한다.
 * periodDays 조회 기간, counted 단계 기록이 있는 밤, skipped 수면 기록은 있는데 단계 기록이 없는 밤
 */
export function stageNote(periodDays: number, counted: number, skipped: number): string {
	const base =
		periodDays <= 1
			? '이 날 하루의 수면 단계예요.'
			: counted >= periodDays
				? `${periodDays}일 평균이에요.`
				: `${periodDays}일 중 ${counted}일의 평균이에요.`;
	return base + (skipped > 0 ? ` 수면 단계 기록이 없는 ${skipped}일은 뺐어요.` : '');
}

/** 측정 최저·최고 아래 안내. 하루 평균의 최저·최고가 아니라 기간 전체에서 실제로 잰 값이라는 뜻을 쉬운 말로 쓴다 */
export function measuredNote(periodDays: number): string {
	return periodDays <= 1
		? '이 날 측정된 값 중 가장 낮은 값과 가장 높은 값이에요.'
		: `${periodDays}일 동안 측정된 값 중 가장 낮은 값과 가장 높은 값이에요.`;
}

/** 수면 세션 목록(kind sleep_sessions). 시작 순서대로, 주 수면 요약은 is_main 만으로 낸다 */
export function sleepSessionsView(snapshot: SleepSessionsSnapshot) {
	const sessions = [...snapshot.sessions].sort((a, b) => a.start_at.localeCompare(b.start_at));
	const mains = sessions.filter((session) => session.is_main && session.sleep_minutes !== null);
	return {
		sessions,
		mainCount: sessions.filter((session) => session.is_main).length,
		extraCount: sessions.filter((session) => !session.is_main).length,
		mainAverage: average(mains.map((session) => session.sleep_minutes as number))
	};
}

/** 시간대 걸음 표의 열 이름(6시, 7시, …). end_hour 는 끝 시각이라 열에 넣지 않는다 */
export function hourlyColumns(snapshot: HourlyStepsSnapshot): number[] {
	return Array.from(
		{ length: Math.max(0, snapshot.end_hour - snapshot.start_hour) },
		(_, index) => snapshot.start_hour + index
	);
}

const ACTIVITY_LABELS: Record<ExerciseActivityField, string> = {
	exercise_count: '운동 횟수',
	exercise_calories_kcal: '운동 칼로리',
	exercise_distance_m: '운동 거리'
};
/** 항목별로 값이 없는 이유가 실려 오는 칸 */
const ACTIVITY_REASON_KEYS = {
	exercise_count: 'reason',
	exercise_calories_kcal: 'calories_reason',
	exercise_distance_m: 'distance_reason'
} as const satisfies Record<ExerciseActivityField, keyof ExerciseActivityDay>;
const ACTIVITY_SOURCE_LABELS: Record<ExerciseActivitySource, string> = {
	exercise_sessions: '운동 횟수',
	calories_burned: '칼로리',
	distances: '거리'
};
/** 날짜별 표를 접지 않고 보여 주는 최대 일수 */
export const MAX_LISTED_ACTIVITY_DAYS = 7;

export type ExerciseActivityView = {
	/** 항목별 기간 합계. 나눠 조회한 기간을 합친 범위는 합계를 다시 내지 않아 비어 있다 */
	/** captions 는 한 줄씩 따로 놓는다(좁은 칸에서 한 문장이 어중간하게 갈리지 않게) */
	cards: { label: string; value: string; captions: string[] }[];
	/** 날짜별 값. 값이 없으면 이유(연동 전·동기화 전·기록 없음)를 글로 둔다 */
	rows: { date: string; inProgress: boolean; cells: string[] }[];
	notes: string[];
};

/** 값이 없는 칸의 글. AI 가 받는 표현과 같다(값 없음과 기록 없음을 나누지 않는다) */
function activityCell(day: ExerciseActivityDay, field: ExerciseActivityField): string {
	const value = day[field];
	if (value !== null) return formatExerciseActivity(field, value);
	const reason = day[ACTIVITY_REASON_KEYS[field]];
	if (reason === 'BEFORE_LINK') return '연동 전';
	if (reason === 'UNSYNCED') return '동기화 전';
	return reason === null ? '-' : '기록 없음';
}

/**
 * 운동 근거의 횟수·칼로리·거리(S15P31A403-375). AI 가 운동 요약·추이 답변에 쓰는 값이라 근거에서 찾을 수 있게 보여 준다.
 * 합계·평균은 서버가 낸 값을 그대로 쓴다. 오늘(수집 중)은 합계에서 빠져 있어, 합계가 없고 오늘 값만 있으면 그 값을 보여 준다.
 * 값이 실려 오지 않은 근거(예전 근거·다른 지표)는 null 이다
 */
export function exerciseActivityView(snapshot: EvidenceSnapshot): ExerciseActivityView | null {
	const activity = snapshot.detail.exercise_activity;
	if (!activity || activity.days.length === 0) return null;
	const days = [...activity.days].sort((a, b) => a.date.localeCompare(b.date));
	const today = days.find((day) => day.is_in_progress) ?? null;
	const summary = activity.summary;

	const cards = summary
		? EXERCISE_ACTIVITY_FIELDS.map((field) => {
				const stat = summary[field];
				const label = ACTIVITY_LABELS[field];
				if (stat.total !== null) {
					return {
						label,
						value: formatExerciseActivity(field, stat.total),
						captions: [
							...(stat.days_with_data > 1 && stat.avg !== null
								? [`하루 평균 ${formatExerciseActivity(field, stat.avg, true)}`]
								: []),
							`기록 ${stat.days_with_data}일`
						]
					};
				}
				const todayValue = today?.[field] ?? null;
				return todayValue !== null
					? {
							label: `오늘 ${label}`,
							value: formatExerciseActivity(field, todayValue),
							captions: ['수집 중']
						}
					: { label, value: '-', captions: ['기록 없음'] };
			})
		: [];

	const failed = Object.entries(activity.source_statuses)
		.filter(([, status]) => status.last_sync_status === 'fail')
		.map(([name]) => ACTIVITY_SOURCE_LABELS[name as ExerciseActivitySource]);
	// 횟수·칼로리·거리가 모두 비어 있으면 "-"만 세 칸인 표가 되어, "기록이 없어요" 안내 아래에 같은 말을 한 번 더 하게 된다.
	// 수집 실패를 알릴 것도 없으면 묶음을 두지 않는다(S15P31A403-439)
	if (cards.length > 0 && cards.every((card) => card.value === '-') && failed.length === 0)
		return null;
	const notes = [
		'횟수는 운동으로 인정된 기록만 세요.',
		'칼로리·거리는 운동으로 인정되지 않은 기록까지 포함한 값이에요. 하루 전체 소모 칼로리나 이동 거리가 아니에요.'
	];
	if (summary === null)
		notes.push('합계와 평균은 조회한 기간별로 받아, 여기서 다시 더하지 않았어요.');
	if (today && summary !== null && days.length > 1)
		notes.push('오늘 값은 수집 중이며 합계·평균에서 제외해요.');
	if (failed.length > 0)
		notes.push(`${failed.join('·')} 수집에 실패해 이전에 저장된 기록 기준이에요.`);

	return {
		cards,
		rows: days.map((day) => ({
			date: day.date,
			inProgress: day.is_in_progress,
			cells: EXERCISE_ACTIVITY_FIELDS.map((field) => activityCell(day, field))
		})),
		notes
	};
}

/**
 * 날짜별 기록 달력(S15P31A403-430). 긴 기간의 "날짜별 전체 보기"를 대신한다. 7차 멘토링에서 기록을 달력으로 보여 주자는 의견을 받았다.
 * 칸마다 숫자를 쓰면 휴대폰 폭에서 들어가지 않아, 하루 값을 기간 평균 대비 도넛으로 그리고 숫자는 날짜를 눌렀을 때 보여 준다.
 * 평균 대비가 뜻을 갖는 지표만 쓴다. 체중 같은 신체 지표는 날마다 거의 같아 도넛이 모두 꽉 차고, 산소포화도 탭은 위 요약·차트로 충분하다
 */
export const CALENDAR_METRICS: readonly EvidenceSnapshot['metric'][] = [
	'steps',
	'exercises',
	'sleeps',
	'heart_rates'
];

/** 달력 원 기준이 어디서 왔는지. summary: AI 가 받은 기간 평균, merged: 나눠 조회한 기간을 합쳐 화면에서 냄, active-days: 운동한 날만 화면에서 냄 */
export type CalendarBasis = 'summary' | 'merged' | 'active-days';

/** 달력이 세는 날: 오늘(수집 중)·연동 전·값 없는 날을 뺀 날의 값. 백엔드 평균과 같은 제외 규칙이다 */
function countedValues(snapshot: EvidenceSnapshot): { date: string; value: number }[] {
	return snapshot.days.flatMap((day) =>
		day.is_in_progress || day.reason === 'BEFORE_LINK' || day.missing || day.value === null
			? []
			: [{ date: day.date, value: day.value }]
	);
}

/**
 * 도넛 한 바퀴의 기준.
 * - 한 번에 조회한 기간은 AI 가 받은 기간 평균(summary.avg)을 그대로 쓴다.
 * - 31일 제한으로 나눠 조회해 합친 긴 기간(6개월 등)은 전체 평균이 실려 오지 않는다(evidence.ts mergeParts).
 *   백엔드 평균과 같은 제외 규칙으로 하루 값을 평균해 화면에서 낸다. 심박은 백엔드가 시간별 가중이라 조금 다를 수 있다.
 * - 운동은 운동한 날(0분보다 긴 날)만 평균한다(S15P31A403-435). 운동하지 않은 날은 쉬는 날이라
 *   평균에 넣으면 운동한 날이 모두 평균을 크게 넘어 차이가 보이지 않는다. AI 의 기간 평균(쉰 날 포함)과 다른 값이라 안내 팝업에 알린다.
 * 기준이 없거나 0이면 달력을 두지 않는다
 */
export function calendarAverage(
	snapshot: EvidenceSnapshot
): { value: number; basis: CalendarBasis } | null {
	if (!CALENDAR_METRICS.includes(snapshot.metric)) return null;
	if (snapshot.metric === 'exercises') {
		const active = average(
			countedValues(snapshot)
				.filter((day) => day.value > 0)
				.map((day) => day.value)
		);
		return active !== null ? { value: active, basis: 'active-days' } : null;
	}
	const avg = snapshot.summary.avg;
	if (avg !== null) return avg > 0 ? { value: avg, basis: 'summary' } : null;
	const computed = average(countedValues(snapshot).map((day) => day.value));
	return computed !== null && computed > 0 ? { value: computed, basis: 'merged' } : null;
}

/**
 * 달력에 체크로 표시할 기간 최고·최저 날(S15P31A403-430·435).
 * - 기본은 AI 가 받은 daily_max·daily_min. 최저가 0이면 여러 날 가운데 하나만 가리키게 되어 두지 않는다.
 * - 운동은 운동한 날 가운데 가장 긴 날·짧은 날이다(쉰 날 0분은 최저로 치지 않는다). 같으면 이른 날(백엔드와 같은 규칙).
 * 최고와 최저가 같은 날이면 최고 하나만 둔다
 */
export function calendarExtremes(snapshot: EvidenceSnapshot): {
	max: { date: string; value: number } | null;
	min: { date: string; value: number } | null;
} {
	if (snapshot.metric === 'exercises') {
		const active = countedValues(snapshot).filter((day) => day.value > 0);
		const max = active.reduce<(typeof active)[number] | null>(
			(best, day) => (best === null || day.value > best.value ? day : best),
			null
		);
		const min = active.reduce<(typeof active)[number] | null>(
			(best, day) => (best === null || day.value < best.value ? day : best),
			null
		);
		return { max, min: min && min.date !== max?.date ? min : null };
	}
	const { daily_max: max, daily_min: min } = snapshot.summary;
	return {
		max: max ?? null,
		min: min && min.value !== 0 && min.date !== max?.date ? min : null
	};
}

/**
 * 일별 막대 차트의 최고·최저 꼬리표에 쓸 날(S15P31A403-439). 달력의 체크와 같은 날을 가리킨다(calendarExtremes).
 * 수면·걸음·심박·운동 네 탭 모두 단다. 처음에는 심박을 뺐는데(같은 창의 "측정 최저·최고"와 헷갈릴까 봐),
 * 탭마다 차트가 달라 보인다는 의견으로 넷을 맞췄다. 심박의 꼬리표는 하루 평균이 가장 높은 날·낮은 날이다.
 * - 운동은 0분인 날을 빼고 운동한 날 가운데서 고른다. 예전에는 AI 가 받은 최저(0분)를 그대로 써서 최저 꼬리표가 없었다
 * - 견줄 날이 없으면(기록이 하루뿐이거나 모든 날이 같은 값) 달지 않는다. 최저가 0이면 최고만 단다
 */
export function chartExtremes(snapshot: EvidenceSnapshot): {
	max: { key: string; value: number };
	min: { key: string; value: number } | null;
} | null {
	const { max, min } = calendarExtremes(snapshot);
	if (!max) return null;
	if (snapshot.metric !== 'exercises') {
		const lowest = snapshot.summary.daily_min;
		if (!lowest || lowest.value === max.value) return null;
	}
	return {
		max: { key: max.date, value: max.value },
		min: min && min.value !== max.value ? { key: min.date, value: min.value } : null
	};
}

/**
 * 도넛이 얼마나 찰지. first 는 첫 바퀴(0에서 평균까지), second 는 두 바퀴째(평균에서 기간 최고까지)다.
 * 평균을 넘은 날이 모두 꽉 찬 같은 도넛으로 보이지 않게 두 바퀴째를 진한 색으로 그린다. 처음에는 "넘은 만큼"(평균의 두 배에서 꽉 참)으로
 * 했는데, 운동처럼 안 한 날이 많아 평균이 낮으면 운동한 날이 모두 두 배를 넘어 다시 같은 모양이 되었다.
 * 그래서 두 바퀴째의 끝을 기간에서 가장 높은 날(summary.daily_max)로 둔다. 가장 높은 날만 두 바퀴를 다 채운다
 */
export function donutFill(
	value: number,
	average: number,
	max: number | null
): { first: number; second: number } {
	const first = Math.min(Math.max(value / average, 0), 1);
	if (value <= average || max === null || max <= average) return { first, second: 0 };
	return { first, second: Math.min((value - average) / (max - average), 1) };
}

/**
 * 원 한 바퀴(0~1)를 SVG 점선으로 그리는 값(pathLength 100 기준). 둥근 끝은 선 양쪽으로 굵기의 절반씩 튀어나와
 * 그대로 그리면 실제보다 길어 보인다. 반지름 11·굵기 4 원에서 끝 하나가 둘레의 약 2.9%라, 95% 인 날이 꽉 찬 원과
 * 같아 보였다(S15P31A403-438 확인). 그래서 그 길이만큼 줄이고 시작점을 밀어 둥근 끝까지 합친 길이가 값과 같게 한다.
 * 다 찬 바퀴는 끝이 맞닿아 평평한 끝(butt)으로 그린다. 아주 작은 값은 점 하나(둥근 끝 두 개)로 남는다
 */
export function donutDash(
	amount: number,
	radius = 11,
	strokeWidth = 4
): { dasharray: string; dashoffset: number; linecap: 'butt' | 'round' } {
	if (amount >= 1) return { dasharray: '100 100', dashoffset: 0, linecap: 'butt' };
	const cap = (strokeWidth / 2 / (2 * Math.PI * radius)) * 100;
	const length = Math.max(amount * 100 - cap * 2, 0.001);
	return { dasharray: `${length} 100`, dashoffset: -cap, linecap: 'round' };
}
/** 기간이 걸친 달(`2026-10`)을 앞에서부터. 날짜 순서가 섞여 와도 달이 겹치거나 뒤바뀌지 않게 정렬한다 */
export function calendarMonths(days: Day[]): string[] {
	return [...new Set(days.map((day) => day.date.slice(0, 7)))].sort();
}

/**
 * 달력을 처음 열 때 고를 날과 보여 줄 달. 마지막으로 값이 있는 날(오늘 수집 중 포함), 값이 하나도 없으면 기간의 마지막 날이다.
 * 달은 그 날이 있는 달로 둔다. 기간 끝 며칠이 비어 다음 달로 넘어가도 고른 날과 보이는 달이 어긋나지 않는다
 */
export function calendarStart(days: Day[]): { date: string; month: string } | null {
	const sorted = [...days].sort((a, b) => a.date.localeCompare(b.date));
	const day = sorted.findLast((entry) => !entry.missing && entry.value !== null) ?? sorted.at(-1);
	return day ? { date: day.date, month: day.date.slice(0, 7) } : null;
}

export type CalendarCell = {
	date: string;
	/** 날짜 숫자 */
	day: number;
	/** 0 일요일 ~ 6 토요일 */
	weekday: number;
	/** 보고 있는 달의 날인지. 앞뒤 달의 날은 흐리게 그린다 */
	inMonth: boolean;
	/** 기간 안의 날이면 그날 기록. 같은 달이라도 기간 밖이면 null 이다 */
	entry: Day | null;
};

function shiftDate(date: string, offset: number): string {
	const next = new Date(`${date}T00:00:00Z`);
	next.setUTCDate(next.getUTCDate() + offset);
	return next.toISOString().slice(0, 10);
}

/**
 * 한 달을 일요일부터 시작하는 주로 나눈다. 첫 주 앞과 마지막 주 뒤는 앞뒤 달의 날로 채운다.
 * 달마다 4~6주라 달을 넘기면 달력 높이가 바뀌고 아래 닫기 버튼이 움직였다. 처음에는 늘 6주로 맞췄는데,
 * 빈 줄이 남는 달이 많아 어색했다(S15P31A403-438 피드백). 기본은 5주로 채우고, 6주가 꼭 필요한 달만 한 줄 늘린다.
 * 4주로 끝나는 달(1일이 일요일인 평년 2월)도 5주로 채운다
 */
export const CALENDAR_MIN_WEEKS = 5;

/** 그 달에 필요한 주 수(4~6). 1일 앞 빈 칸과 날 수로 정해진다 */
export function weeksInMonth(month: string): number {
	const [year, monthNumber] = month.split('-').map(Number);
	const firstWeekday = new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay();
	const length = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
	return Math.ceil((firstWeekday + length) / 7);
}

/** 한 달 달력의 칸들. CALENDAR_MIN_WEEKS 주보다 적으면 다음 달 날로 채운다 */
export function calendarWeeks(month: string, days: Day[]): CalendarCell[][] {
	const byDate = new Map(days.map((day) => [day.date, day]));
	const first = `${month}-01`;
	const firstWeekday = new Date(`${first}T00:00:00Z`).getUTCDay();
	const weeks: CalendarCell[][] = [];
	for (
		let date = shiftDate(first, -firstWeekday), count = 0;
		count < Math.max(CALENDAR_MIN_WEEKS, weeksInMonth(month)) * 7;
		date = shiftDate(date, 1), count += 1
	) {
		const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
		if (weekday === 0) weeks.push([]);
		const inMonth = date.startsWith(month);
		weeks.at(-1)!.push({
			date,
			day: Number(date.slice(8)),
			weekday,
			inMonth,
			entry: inMonth ? (byDate.get(date) ?? null) : null
		});
	}
	return weeks;
}

/** 그 밤에 산소포화도 90% 미만이 있었는지. 달력 날짜에 점을 찍는다 */
export function hasLowSpo2(state: Spo2LowState): boolean {
	return state.kind === 'times' || (state.kind === 'count-only' && state.count > 0);
}

/** 평균보다 많을 때·적을 때 쓰는 말. 잠과 운동은 "길다·짧다", 심박은 "높다·낮다"가 자연스럽다 */
const COMPARE_WORDS: Partial<Record<EvidenceSnapshot['metric'], [string, string]>> = {
	steps: ['많아요', '적어요'],
	exercises: ['길어요', '짧아요'],
	sleeps: ['길어요', '짧아요'],
	heart_rates: ['높아요', '낮아요']
};

/** 화면에 쓰는 자리(format.ts). 걸음·분은 정수, 심박은 소수 첫째 자리 */
const DISPLAY_STEP: Partial<Record<EvidenceSnapshot['metric'], number>> = { heart_rates: 0.1 };

/**
 * 달력에서 고른 날의 "평균보다 ○ 많아요". 화면에 보이는 자리로 반올림한 두 값의 차이로 낸다.
 * 반올림 전 값으로 빼면 그날 값과 평균이 같은 "7시간 2분"으로 보이는데 "1분 길어요"라고 나올 수 있다
 */
export function compareToAverage(
	metric: EvidenceSnapshot['metric'],
	value: number,
	average: number
): string {
	const step = DISPLAY_STEP[metric] ?? 1;
	const round = (number: number) => Math.round(number / step);
	const gap = round(value) - round(average);
	if (gap === 0) return '평균과 같아요';
	const [more, less] = COMPARE_WORDS[metric] ?? ['높아요', '낮아요'];
	return `평균보다 ${formatMetricValue(metric, Math.abs(gap) * step)} ${gap > 0 ? more : less}`;
}

/**
 * 그날 실제 측정 최저·최고 심박(detail.daily_ranges, backend/docs/api-spec.md 499). 근거에는 실려 오지만 화면이 읽지 않던 값이라
 * 달력 상세에 둔다(S15P31A403-430). "화요일 최고 심박" 같은 답변을 그날 칸에서 맞춰 볼 수 있다.
 * 산소포화도 칸도 같은 키로 오지만 수면 탭 상세가 밤 기록에서 따로 보여 줘 심박만 읽는다.
 * 모양이 다른 칸은 건너뛰고, 그날 값이 둘 다 없으면 null 이다.
 * 나눠 조회해 합친 기간은 겹치는 날 칸이 두 번 올 수 있어, 날짜별 값과 같이 뒤 기간(마지막 칸)을 쓴다(evidence.ts mergeParts)
 */
export function dailyRangeOf(
	snapshot: EvidenceSnapshot,
	date: string
): { min: number | null; max: number | null } | null {
	if (snapshot.metric !== 'heart_rates') return null;
	const number = (value: unknown) =>
		typeof value === 'number' && Number.isFinite(value) ? value : null;
	const range = snapshot.detail.daily_ranges.findLast(
		(item): item is Record<string, unknown> =>
			typeof item === 'object' && item !== null && (item as Record<string, unknown>).date === date
	);
	if (!range) return null;
	const min = number(range.min);
	const max = number(range.max);
	return min === null && max === null ? null : { min, max };
}
