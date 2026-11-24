import {
	isKindEvidence,
	type Evidence,
	type EvidenceSnapshot,
	type ExerciseActivity,
	type HealthMetric,
	type MetricEvidence
} from './types';

export type EvidenceGroup = {
	metric: HealthMetric;
	/**
	 * 화면에 보여 줄 기간. 나눠 조회한 기록을 합친 묶음이면 처음 날부터 마지막 날까지를 이은 근거다
	 * (평균·합계는 다시 계산하지 않아 summary.avg·total 이 null 이다. 기간별 평균은 parts 에 있다).
	 */
	current: MetricEvidence;
	/** 같은 지표의 이전 기간 근거. 명세상 비교는 별도 근거로 오고 관계 필드는 없다. 합친 묶음에는 없다. */
	comparison: MetricEvidence | null;
	/**
	 * AI 가 실제로 조회한 기간들(시작 순). 하나면 그대로이고, 둘 이상이면 current 가 이들을 합친 것이다.
	 * "최근 6주를 주별로"처럼 한 주씩 여러 번 부르거나, 한 번에 31일까지만 부를 수 있어 긴 기간을 둘로 나눠 부른 경우다(S15P31A403-368)
	 */
	parts: MetricEvidence[];
	/**
	 * 다른 기간과 날짜가 겹쳐 위의 기간 구분에서 뺀 근거(시작 순). AI 가 받은 평균은 그대로라 화면에 따로 적는다.
	 * "한 달 전이랑 비교"를 물으면 이전 30일·최근 30일과 함께 두 달 전체를 한 번 더 조회해 오는데,
	 * 셋을 한 목록에 두면 기간이 서로 겹쳐 무엇을 견주는지 읽히지 않았다
	 */
	overlaps: MetricEvidence[];
};

type DatedValue = { date: string; value: number };

/** `2026-10-11` 의 다음 날. 근거 날짜는 KST 달력 날짜라 UTC 자정으로 읽어 하루를 더한다 */
function nextDay(date: string): string {
	const day = new Date(`${date}T00:00:00Z`);
	day.setUTCDate(day.getUTCDate() + 1);
	return day.toISOString().slice(0, 10);
}

/** 앞 기간이 끝난 바로 다음 날 뒤 기간이 시작하는지 */
function isContinuous(earlier: MetricEvidence, later: MetricEvidence): boolean {
	return nextDay(earlier.periodEnd) === later.periodStart;
}

/** 한 번에 조회할 수 있는 최대 일수(AI 도구 제한, ai tools/handlers.py MAX_LIST_DAYS) */
const MAX_QUERY_DAYS = 31;
/** AI 가 나눠 조회하는 가장 긴 기간(ai tools/handlers.py LIST_SPLIT_MAX_DAYS). 이보다 길면 나눠 조회하지 않는다 */
const MAX_SPLIT_DAYS = 49;

function dayCount(item: MetricEvidence): number {
	const start = Date.parse(`${item.periodStart}T00:00:00Z`);
	const end = Date.parse(`${item.periodEnd}T00:00:00Z`);
	return Math.round((end - start) / (24 * 60 * 60 * 1000)) + 1;
}

/**
 * 날짜가 이어진 두 기간이 "나눠 부른 한 기간"인지. 근거에는 두 기간의 관계가 실려 오지 않아 모양으로 가린다.
 *
 * AI 도구는 31일을 넘고 49일 이하인 기간만 둘로 나누고, 끝 날짜부터 31일을 자른다(ai tools/periods.py split_range).
 * 그래서 나눠 부른 모양은 하나뿐이다: 뒤 기간이 정확히 31일, 앞 기간이 남는 1~18일(42일이면 11일 + 31일).
 * 이 모양이 아닌 두 기간은 사용자가 비교를 물어본 것이다. 길이가 달라도 비교다 —
 * 수요일에 묻는 "이번 주랑 지난주"(7일·3일), 월 중에 묻는 "이번 달이랑 지난달"(31일·12일)이 그렇다(S15P31A403-374, AI 담당 확인).
 * "최근 31일이랑 그 전 일주일 비교"처럼 같은 모양의 비교는 가릴 수 없다. 근거에 관계를 알려 주는 값이 생기면 그 값을 쓴다.
 */
function isSplitPair(earlier: MetricEvidence, later: MetricEvidence): boolean {
	if (!isContinuous(earlier, later)) return false;
	return dayCount(later) === MAX_QUERY_DAYS && dayCount(earlier) <= MAX_SPLIT_DAYS - MAX_QUERY_DAYS;
}

/**
 * 서로 겹치지 않고 날짜가 이어지는 다른 기간들만으로 이 기간의 모든 날이 덮이는지.
 * 첫 기간은 이 기간의 첫날을 품고, 그다음부터는 앞 기간이 끝난 바로 다음 날 시작해야 한다
 */
function isCoveredBy(item: MetricEvidence, others: MetricEvidence[]): boolean {
	const reach = (from: string, first: boolean): boolean =>
		others.some((other) => {
			const starts = first ? other.periodStart <= from : other.periodStart === from;
			if (!starts || other.periodEnd < from) return false;
			return other.periodEnd >= item.periodEnd || reach(nextDay(other.periodEnd), false);
		});
	return reach(item.periodStart, true);
}

/**
 * 조회한 기간들을 겹치지 않는 구분(parts)과 거기에 겹치는 기간(overlaps)으로 가른다.
 * 긴 기간부터 보아, 나머지 기간들로 모든 날이 덮이는 기간을 뺀다. 그래서
 * - 이전 30일·최근 30일·두 달 전체 → 두 달 전체가 빠지고 30일 둘이 남는다(비교)
 * - 6주 전체·지난주·이번 주 → 두 주가 빠지고 6주가 남는다
 * 일부만 걸친 기간은 어느 쪽도 덮이지 않아 그대로 남는다(합칠 때 겹친 날은 한 번만 센다)
 */
function splitOverlaps(items: MetricEvidence[]): Pick<EvidenceGroup, 'parts' | 'overlaps'> {
	let parts = items;
	const overlaps: MetricEvidence[] = [];
	for (const item of [...items].sort((a, b) => dayCount(b) - dayCount(a))) {
		const others = parts.filter((part) => part !== item);
		if (others.length > 0 && isCoveredBy(item, others)) {
			parts = others;
			overlaps.push(item);
		}
	}
	return { parts, overlaps };
}

const byStart = (a: MetricEvidence, b: MetricEvidence) =>
	a.periodStart.localeCompare(b.periodStart) || a.periodEnd.localeCompare(b.periodEnd);

function pick(
	values: (DatedValue | null)[],
	better: (a: number, b: number) => boolean
): DatedValue | null {
	return values.reduce<DatedValue | null>(
		(best, value) => (value && (!best || better(value.value, best.value)) ? value : best),
		null
	);
}

/**
 * 나눠 조회한 기간의 운동 횟수·칼로리·거리. 날짜별 값은 이어 붙이고 합계·평균은 두지 않는다(다시 계산하지 않는다).
 * 수집 실패 표시는 한 기간이라도 실패했으면 남긴다
 */
function mergeActivities(activities: ExerciseActivity[]): ExerciseActivity {
	const byDate = new Map<string, ExerciseActivity['days'][number]>();
	const sources: ExerciseActivity['source_statuses'] = {};
	for (const activity of activities) {
		for (const day of activity.days) byDate.set(day.date, day);
		for (const [name, status] of Object.entries(activity.source_statuses)) {
			const key = name as keyof ExerciseActivity['source_statuses'];
			if (sources[key]?.last_sync_status !== 'fail') sources[key] = status;
		}
	}
	return {
		days: [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date)),
		summary: null,
		source_statuses: sources
	};
}

/** 겹치는 기록은 날짜별 값과 같이 뒤 기간을 우선한다. 기간별 평균·합계는 parts에 보존한다. */
function mergeParts(parts: MetricEvidence[]): MetricEvidence {
	const last = parts[parts.length - 1];
	const byDate = new Map<string, EvidenceSnapshot['days'][number]>();
	const nightsByDate = new Map<string, EvidenceSnapshot['detail']['nights'][number]>();
	const sessionsByStart = new Map<number, EvidenceSnapshot['detail']['sessions'][number]>();
	for (const part of parts) {
		for (const day of part.snapshot.days) byDate.set(day.date, day);
		// nights는 깬 날짜별 주 수면 하나다. 겹치는 밤을 남기면 목록 키와 단계 평균이 중복된다.
		for (const night of part.snapshot.detail.nights) nightsByDate.set(night.date, night);
		for (const session of part.snapshot.detail.sessions)
			sessionsByStart.set(Date.parse(session.start_at), session);
	}
	const days = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
	const completed = days.filter((day) => !day.is_in_progress && day.reason !== 'BEFORE_LINK');
	const valid = completed.filter((day) => !day.missing && day.value !== null);
	const summaries = parts.map((part) => part.snapshot.summary);
	const details = parts.map((part) => part.snapshot.detail);
	const activities = details.flatMap((detail) =>
		detail.exercise_activity ? [detail.exercise_activity] : []
	);
	const periodStart = parts[0].periodStart;
	const periodEnd = parts.reduce(
		(end, part) => (part.periodEnd > end ? part.periodEnd : end),
		parts[0].periodEnd
	);

	return {
		id: parts.map((part) => part.id).join('+'),
		position: Math.min(...parts.map((part) => part.position)),
		metric: last.metric,
		periodStart,
		periodEnd,
		snapshot: {
			metric: last.snapshot.metric,
			period: { start: periodStart, end: periodEnd },
			unit: last.snapshot.unit,
			summary: {
				avg: null,
				daily_min: pick(
					summaries.map((summary) => summary.daily_min),
					(a, b) => a < b
				),
				daily_max: pick(
					summaries.map((summary) => summary.daily_max),
					(a, b) => a > b
				),
				total: null,
				days_with_data: valid.length,
				days_used_for_summary: valid.length,
				completed_days: completed.length,
				missing_days: days
					.filter(
						(day) =>
							day.reason === 'NO_RECORD' ||
							day.reason === 'NULL_VALUE' ||
							day.reason === 'ZERO_STEPS'
					)
					.map((day) => day.date),
				unsynced_days: days.filter((day) => day.reason === 'UNSYNCED').map((day) => day.date),
				linked_from: summaries.find((summary) => summary.linked_from)?.linked_from ?? null
			},
			days,
			in_progress: last.snapshot.in_progress,
			data_status: last.snapshot.data_status,
			detail: {
				measured_min: pick(
					details.map((detail) => detail.measured_min),
					(a, b) => a < b
				),
				measured_max: pick(
					details.map((detail) => detail.measured_max),
					(a, b) => a > b
				),
				nights: [...nightsByDate.values()].sort((a, b) => a.date.localeCompare(b.date)),
				daily_ranges: details.flatMap((detail) => detail.daily_ranges),
				sessions: [...sessionsByStart.values()].sort(
					(a, b) => Date.parse(a.start_at) - Date.parse(b.start_at)
				),
				...(activities.length > 0 ? { exercise_activity: mergeActivities(activities) } : {})
			}
		}
	};
}

/**
 * 날짜별 지표 근거를 지표별로 묶는다. 순서는 각 지표가 답변에서 처음 나온 위치(position)를 따른다.
 * 같은 지표를 여러 기간으로 조회했을 때:
 * - 두 기간이면 늦게 끝나는 쪽이 현재, 앞쪽이 비교다(이번 주와 지난주).
 * - 두 기간이 일부만 겹치면 비교가 아니다. 긴 쪽을 보여 주고 짧은 쪽은 overlaps 에 둔다. 길이가 같으면 늦게 끝나는 쪽이다(S15P31A403-439).
 * - 다만 두 기간이 한 기간을 나눠 부른 모양이면 한 범위로 합친다(31일 제한으로 둘로 나눈 긴 기간, isSplitPair).
 * - 세 기간 이상이면 모두 합쳐 처음 날부터 마지막 날까지 보여 준다(주별로 여러 번 조회).
 * 같은 기간을 두 번 조회한 근거는 하나로 센다.
 * 다른 기간들과 날짜가 통째로 겹치는 기간은 먼저 빼서 overlaps 에 두고, 남은 기간으로 위 규칙을 따른다(S15P31A403-418).
 */
export function groupEvidenceByMetric(evidence: Evidence[]): EvidenceGroup[] {
	const byMetric = new Map<HealthMetric, MetricEvidence[]>();
	// 모양이 다른 근거(kind)는 지표별 묶음·비교에 넣지 않고 따로 그린다
	const metricEvidence = evidence.filter((item): item is MetricEvidence => !isKindEvidence(item));
	for (const item of metricEvidence.sort((a, b) => a.position - b.position)) {
		const items = byMetric.get(item.metric) ?? [];
		const repeated = items.some(
			(known) => known.periodStart === item.periodStart && known.periodEnd === item.periodEnd
		);
		if (!repeated) byMetric.set(item.metric, [...items, item]);
	}

	return [...byMetric.entries()].map(([metric, items]): EvidenceGroup => {
		const split = splitOverlaps([...items].sort(byStart));
		const { parts } = split;
		const overlaps = [...split.overlaps].sort(byStart);
		if (parts.length === 1) return { metric, current: parts[0], comparison: null, parts, overlaps };
		if (parts.length >= 3 || isSplitPair(parts[0], parts[1]))
			return { metric, current: mergeParts(parts), comparison: null, parts, overlaps };

		const [later, earlier] = [...parts].sort((a, b) => b.periodEnd.localeCompare(a.periodEnd));
		if (earlier.periodEnd < later.periodStart)
			return { metric, current: later, comparison: earlier, parts: [later], overlaps };
		// 일부만 걸친 두 기간은 견주는 사이가 아니다. 전에는 늦게 끝나는 쪽을 보여 줘서, 28일과 그 끝에 하루 더 걸친
		// 최근 7일을 함께 조회하면 이 지표만 7일로 나오고 다른 지표 탭은 28일이었다. 긴 쪽을 보여 주고,
		// 다른 쪽도 버리지 않고 겹치는 기간으로 남긴다
		const current = dayCount(earlier) > dayCount(later) ? earlier : later;
		const left = current === later ? earlier : later;
		return {
			metric,
			current,
			comparison: null,
			parts: [current],
			overlaps: [...overlaps, left].sort(byStart)
		};
	});
}

/** 나눠 조회한 기록을 합친 묶음인지 */
export function isMergedGroup(group: EvidenceGroup): boolean {
	return group.parts.length > 1;
}

/** 현재와 비교 기간의 평균 차이. 어느 한쪽이라도 평균이 없으면 null이다. */
export function averageDelta(group: EvidenceGroup): number | null {
	const currentAvg = group.current.snapshot.summary.avg;
	const comparisonAvg = group.comparison?.snapshot.summary.avg ?? null;
	if (currentAvg === null || comparisonAvg === null) return null;
	return currentAvg - comparisonAvg;
}
