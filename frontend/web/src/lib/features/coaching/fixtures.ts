/**
 * Storybook·테스트용 더미 데이터. 모든 값은 합성 값이며 실제 사용자 건강데이터가 아니다.
 * 화면은 실제 연결(코칭 layout)의 응답을 쓴다.
 */
import { BODY_METRICS } from './types';
import type {
	CoachingMessage,
	ConversationItem,
	EvidenceSnapshot,
	ExerciseActivity,
	ExerciseActivityDay,
	MetricEvidence,
	HealthMetric,
	ExerciseSession,
	MissingReason,
	PendingAnswerExtras,
	SleepNight
} from './types';

/** 더미 대화 시각의 기준 시각 (KST 2026-10-14 14:00) */
export const FIXTURE_NOW = new Date('2026-10-14T05:00:00Z');

const UNIT: Record<HealthMetric, EvidenceSnapshot['unit']> = {
	steps: 'steps',
	heart_rates: 'bpm',
	oxygen_saturations: '%',
	sleeps: 'min',
	exercises: 'min',
	weights: 'kg',
	body_fats: '%',
	basal_metabolic_rates: 'kcal/day',
	heights: 'm'
};

function datesFrom(start: string, count: number): string[] {
	const first = new Date(`${start}T00:00:00Z`);
	return Array.from({ length: count }, (_, index) => {
		const date = new Date(first.getTime() + index * 24 * 60 * 60 * 1000);
		return date.toISOString().slice(0, 10);
	});
}

type SnapshotOptions = {
	/** 값이 없는 날의 이유. 날짜 index별로 정하고, 없으면 NO_RECORD다 */
	reasons?: Record<number, MissingReason>;
	/** 마지막 날을 오늘(진행 중)로 둔다. 걸음·심박·운동은 명세상 통계에서 뺀다 */
	inProgressLast?: boolean;
	/** 측정 최저·최고, 날짜별 수면, 운동 기록 (명세 2.2 detail) */
	detail?: Partial<EvidenceSnapshot['detail']>;
};

function makeSnapshot(
	metric: HealthMetric,
	start: string,
	values: (number | null)[],
	{ reasons = {}, inProgressLast = false, detail = {} }: SnapshotOptions = {}
): EvidenceSnapshot {
	const dates = datesFrom(start, values.length);
	const days = dates.map((date, index) => {
		const value = values[index];
		return {
			date,
			value,
			missing: value === null,
			reason: value === null ? (reasons[index] ?? 'NO_RECORD') : null,
			is_in_progress: inProgressLast && index === values.length - 1
		};
	});
	const counted = days.filter(
		(day, index) => !day.is_in_progress && reasons[index] !== 'BEFORE_LINK'
	);
	const valid = counted.flatMap((day) =>
		day.value === null ? [] : [{ date: day.date, value: day.value }]
	);
	const sum = valid.reduce((total, day) => total + day.value, 0);
	const pick = (compare: (a: number, b: number) => boolean) =>
		valid.reduce<(typeof valid)[number] | null>(
			(best, day) => (best === null || compare(day.value, best.value) ? day : best),
			null
		);

	return {
		metric,
		period: { start: dates[0], end: dates[dates.length - 1] },
		unit: UNIT[metric],
		summary: {
			avg: valid.length === 0 ? null : Math.round((sum / valid.length) * 100) / 100,
			daily_min: pick((a, b) => a < b),
			daily_max: pick((a, b) => a > b),
			total: metric === 'steps' || metric === 'exercises' ? sum : null,
			days_with_data: valid.length,
			days_used_for_summary: valid.length,
			completed_days: counted.length,
			missing_days: counted
				.filter((day) => day.missing && day.reason !== 'UNSYNCED')
				.map((day) => day.date),
			unsynced_days: counted.filter((day) => day.reason === 'UNSYNCED').map((day) => day.date),
			linked_from: Object.values(reasons).includes('BEFORE_LINK')
				? (days.find((day) => day.reason !== 'BEFORE_LINK')?.date ?? null)
				: '2026-09-01'
		},
		days,
		in_progress: inProgressLast
			? {
					date: days[days.length - 1].date,
					value: days[days.length - 1].value,
					missing: days[days.length - 1].missing,
					as_of: '2026-10-28T05:00:00Z'
				}
			: null,
		data_status: {
			last_data_at: '2026-10-07T22:10:00Z',
			// 신체 지표는 동기화 기준 시각이 없어 null 로 온다(S15P31A403-311 백엔드 합의)
			synced_until: BODY_METRICS.includes(metric) ? null : '2026-10-07T22:10:00Z',
			last_sync_status: 'success',
			last_success_at: '2026-10-07T22:15:00Z',
			checked_at: '2026-10-14T05:00:00Z'
		},
		detail: {
			measured_min: null,
			measured_max: null,
			nights: [],
			daily_ranges: [],
			sessions: [],
			...detail
		}
	};
}

function makeEvidence(
	id: string,
	position: number,
	metric: HealthMetric,
	start: string,
	values: (number | null)[],
	options?: SnapshotOptions
): MetricEvidence {
	const snapshot = makeSnapshot(metric, start, values, options);
	return {
		id,
		position,
		metric,
		periodStart: snapshot.period.start,
		periodEnd: snapshot.period.end,
		snapshot
	};
}

export const sleepAndStepsEvidence: MetricEvidence[] = [
	makeEvidence('e0000000-0000-4000-8000-000000000001', 1, 'sleeps', '2026-10-01', [
		312,
		468,
		384,
		366,
		null,
		null,
		435
	]),
	makeEvidence('e0000000-0000-4000-8000-000000000002', 2, 'sleeps', '2026-09-24', [
		350,
		372,
		401,
		340,
		null,
		388,
		399
	]),
	makeEvidence('e0000000-0000-4000-8000-000000000003', 3, 'steps', '2026-10-01', [
		6820,
		9140,
		7410,
		8020,
		7110,
		null,
		8420
	]),
	makeEvidence('e0000000-0000-4000-8000-000000000004', 4, 'steps', '2026-09-24', [
		6210,
		7020,
		6650,
		7480,
		6890,
		7230,
		null
	])
];

/** 규칙적으로 흔들리는 합성 값. index마다 같은 값이 나와 화면 확인이 매번 같다 */
function wave(count: number, base: number, spread: number): number[] {
	return Array.from({ length: count }, (_, index) => base + (((index * 37) % 11) - 5) * spread);
}

/** 밤 하나. 깬 날 기준이고 시각은 UTC다 */
function night(
	date: string,
	sleep: number,
	stages: [deep: number, rem: number, light: number, awake: number] | null
): SleepNight {
	const [deep, rem, light, awake] = stages ?? [null, null, null, null];
	const previous = new Date(Date.parse(`${date}T00:00:00Z`) - 24 * 60 * 60 * 1000)
		.toISOString()
		.slice(0, 10);
	return {
		date,
		// 전날 KST 23:30에 잠들어 그날 07:00에 깬다(UTC로는 둘 다 전날)
		start_at: `${previous}T14:30:00Z`,
		end_at: `${previous}T22:00:00Z`,
		sleep_minutes: sleep,
		deep_minutes: deep,
		rem_minutes: rem,
		light_minutes: light,
		awake_minutes: awake
	};
}

/** 운동 기록 하나. 시작 시각은 KST 오후 7시(UTC 10시) */
function session(
	date: string,
	typeName: string,
	minutes: number,
	options: { title?: string; counted?: boolean } = {}
): ExerciseSession {
	return {
		date,
		start_at: `${date}T10:00:00Z`,
		duration_minutes: minutes,
		type_name: typeName,
		title: options.title ?? null,
		counts_as_exercise: options.counted ?? true
	};
}

/** 운동 활동값 하루. 값 셋은 횟수·칼로리(kcal)·거리(m) 순서이고, null 이면 이유를 붙인다 */
function activityDay(
	date: string,
	[count, calories, distance]: [number | null, number | null, number | null],
	options: { inProgress?: boolean; reason?: MissingReason; distanceReason?: MissingReason } = {}
): ExerciseActivityDay {
	const reason = options.reason ?? null;
	return {
		date,
		exercise_count: count,
		exercise_calories_kcal: calories,
		exercise_distance_m: distance,
		is_in_progress: options.inProgress ?? false,
		reason,
		calories_reason: reason,
		distance_reason: reason ?? options.distanceReason ?? null
	};
}

/**
 * 운동 7일(10.22~10.28)의 횟수·칼로리·거리(S15P31A403-375). 0은 실제 값, 10.24 거리는 값만 없음,
 * 10.25 는 기록 없는 날, 10.28 은 오늘(수집 중)이라 합계에서 빠진다. 칼로리 원천은 최근 수집에 실패했다
 */
const weekActivity: ExerciseActivity = {
	days: [
		activityDay('2026-10-22', [1, 240, 4200]),
		activityDay('2026-10-23', [0, 0, 0]),
		activityDay('2026-10-24', [2, 410, null], { distanceReason: 'NULL_VALUE' }),
		activityDay('2026-10-25', [null, null, null], { reason: 'NO_RECORD' }),
		activityDay('2026-10-26', [1, 520, 8300]),
		activityDay('2026-10-27', [0, 0, 0]),
		activityDay('2026-10-28', [1, 100, 2000], { inProgress: true })
	],
	summary: {
		exercise_count: { total: 4, avg: 0.8, days_with_data: 5 },
		exercise_calories_kcal: { total: 1170, avg: 234, days_with_data: 5 },
		exercise_distance_m: { total: 12500, avg: 3125, days_with_data: 4 }
	},
	source_statuses: {
		exercise_sessions: { last_sync_status: 'success' },
		calories_burned: { last_sync_status: 'fail' },
		distances: { last_sync_status: null }
	}
};

/** 운동 활동값 여러 날(S15P31A403-404). 사흘에 한 번 쉬는 규칙적인 합성 값이고 합계·평균도 함께 낸다 */
function activityRange(start: string, count: number): ExerciseActivity {
	const days = datesFrom(start, count).map((date, index) =>
		index % 3 === 2
			? activityDay(date, [0, 0, 0])
			: activityDay(date, [1, 220 + (index % 5) * 30, 3000 + (index % 4) * 800])
	);
	const stat = (field: 'exercise_count' | 'exercise_calories_kcal' | 'exercise_distance_m') => {
		const total = days.reduce((sum, day) => sum + (day[field] ?? 0), 0);
		return { total, avg: Math.round((total / days.length) * 10) / 10, days_with_data: days.length };
	};
	return {
		days,
		summary: {
			exercise_count: stat('exercise_count'),
			exercise_calories_kcal: stat('exercise_calories_kcal'),
			exercise_distance_m: stat('exercise_distance_m')
		},
		source_statuses: {
			exercise_sessions: { last_sync_status: 'success' },
			calories_burned: { last_sync_status: 'success' },
			distances: { last_sync_status: 'success' }
		}
	};
}

const monthSessions: ExerciseSession[] = [
	session('2026-09-29', '웨이트 트레이닝', 40, { title: '헬스' }),
	session('2026-10-02', '웨이트 트레이닝', 70, { title: '헬스' }),
	session('2026-10-05', '달리기', 60),
	session('2026-10-08', '웨이트 트레이닝', 50, { title: '헬스' }),
	session('2026-10-11', '웨이트 트레이닝', 80, { title: '헬스' }),
	session('2026-10-14', '걷기', 45),
	session('2026-10-15', '걷기', 32, { counted: false }),
	session('2026-10-17', '웨이트 트레이닝', 60, { title: '헬스' }),
	session('2026-10-20', '사이클', 40),
	session('2026-10-23', '웨이트 트레이닝', 90, { title: '헬스' })
];

/** 근거 상세 창의 특수 경우(기록 부족·과다). 이름이 곧 확인할 상황이다 */
export const evidenceCases = {
	/** "최근 6주를 주별로": 한 주씩 여섯 번 나눠 조회(S15P31A403-368). 조회 순서는 최근 주부터 */
	weeklySplit: Array.from({ length: 6 }, (_, index) =>
		makeEvidence(
			`e3000000-0000-4000-8000-00000000000${index + 1}`,
			index + 1,
			'sleeps',
			datesFrom('2026-09-03', 42)[(5 - index) * 7],
			wave(7, 380 + (5 - index) * 6, 11)
		)
	),
	/** 한 번에 31일까지만 조회할 수 있어 42일을 둘로 나눠 조회(10.01~10.11, 10.12~11.11) */
	longSplit: [
		makeEvidence(
			'e3000000-0000-4000-8000-000000000011',
			1,
			'steps',
			'2026-10-01',
			wave(11, 7200, 13)
		),
		makeEvidence(
			'e3000000-0000-4000-8000-000000000012',
			2,
			'steps',
			'2026-10-12',
			wave(31, 8100, 17)
		)
	],
	/** 7일 중 하루만 기록. 평균·최저·막대가 모두 같은 값이 된다 */
	oneDayOfSeven: [
		makeEvidence('e1000000-0000-4000-8000-000000000001', 1, 'sleeps', '2026-10-22', [
			null,
			null,
			null,
			null,
			null,
			412,
			null
		]),
		makeEvidence('e1000000-0000-4000-8000-000000000002', 2, 'sleeps', '2026-10-15', wave(7, 400, 9))
	],
	/** 기록이 하나도 없음. 마지막 이틀은 아직 동기화 전 */
	noRecords: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000003',
			1,
			'steps',
			'2026-10-22',
			Array(7).fill(null),
			{ reasons: { 5: 'UNSYNCED', 6: 'UNSYNCED' } }
		)
	],
	/** 어제 하루와 그 전날 하루 비교 (기간 1일) */
	singleDay: [
		makeEvidence('e1000000-0000-4000-8000-000000000004', 1, 'steps', '2026-10-27', [8240]),
		makeEvidence('e1000000-0000-4000-8000-000000000005', 2, 'steps', '2026-10-26', [6930])
	],
	/** 현재 기간은 충분하지만 비교 기간은 하루만 기록 */
	sparseComparison: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000006',
			1,
			'sleeps',
			'2026-10-22',
			wave(7, 390, 11)
		),
		makeEvidence('e1000000-0000-4000-8000-000000000007', 2, 'sleeps', '2026-10-15', [
			null,
			null,
			455,
			null,
			null,
			null,
			null
		])
	],
	/** 이번 달 31일. 기록 없는 날이 이어지고 오늘은 진행 중 */
	month: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000008',
			1,
			'steps',
			'2026-10-01',
			wave(31, 7600, 420).map((value, index) => ([9, 10, 11, 20].includes(index) ? null : value)),
			{ inProgressLast: true }
		),
		makeEvidence(
			'e1000000-0000-4000-8000-000000000009',
			2,
			'steps',
			'2026-09-01',
			wave(30, 7100, 380)
		)
	],
	/** 연동 시작 전 날짜가 기간 앞쪽에 있음 */
	beforeLink: [
		makeEvidence(
			'e1000000-0000-4000-8000-00000000000a',
			1,
			'heart_rates',
			'2026-10-22',
			[null, null, null, null, 72, 75, 70],
			{ reasons: { 0: 'BEFORE_LINK', 1: 'BEFORE_LINK', 2: 'BEFORE_LINK', 3: 'BEFORE_LINK' } }
		)
	],
	/** 심박 7일. 눈금이 딱 떨어지는지 본다 */
	heartWeek: [
		makeEvidence(
			'e1000000-0000-4000-8000-00000000000b',
			1,
			'heart_rates',
			'2026-10-22',
			wave(7, 74, 2)
		)
	],
	/** "오늘 몇 걸음?" 오늘 하루 기간. 통계 대상 날짜는 0일이지만 수집 중인 값이 있다 */
	todayOnly: [
		makeEvidence('e1000000-0000-4000-8000-00000000000d', 1, 'steps', '2026-10-28', [4210], {
			inProgressLast: true
		})
	],
	/** 7일 중 지난 6일은 기록이 없고 오늘 수집 중 값만 있음 */
	todayOnlyWeek: [
		makeEvidence(
			'e1000000-0000-4000-8000-00000000000e',
			1,
			'steps',
			'2026-10-22',
			[null, null, null, null, null, null, 4210],
			{ inProgressLast: true }
		)
	],
	/** 걸음 7일이 모두 있고 오늘은 수집 중. 날짜별 기록 칸 높이가 오늘 칸에서 어긋나지 않는지 본다 (278) */
	weekWithToday: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000013',
			1,
			'steps',
			'2026-10-22',
			[10074, 9247, 11379, 10558, 11048, 9896, 2075],
			{ inProgressLast: true }
		)
	],
	/** 운동 7일이 모두 0분. 빈 차트 대신 요약·날짜 목록으로 보여야 한다 */
	allZeroWeek: [
		makeEvidence(
			'e1000000-0000-4000-8000-00000000000f',
			1,
			'exercises',
			'2026-10-22',
			Array(7).fill(0)
		)
	],
	/** 명세상 가능한 긴 기간(90일). 막대를 주 단위로 묶어 보여야 한다 */
	quarter: [
		makeEvidence(
			'e1000000-0000-4000-8000-00000000000c',
			1,
			'steps',
			'2026-07-31',
			wave(90, 7300, 450).map((value, index) => (index % 13 === 4 ? null : value))
		)
	],
	/** "지난주 화요일 최고 심박은?" 하루 기간. 답변은 하루 평균이 아닌 실제 측정 최고를 말한다 (247 테스트) */
	heartOneDay: [
		makeEvidence('e1000000-0000-4000-8000-000000000010', 1, 'heart_rates', '2026-10-27', [76], {
			detail: {
				measured_min: { date: '2026-10-27', value: 54 },
				measured_max: { date: '2026-10-27', value: 138 }
			}
		})
	],
	/** "지난주 깊은 잠 평균은?" 날짜별 수면 단계. 10.25 밤은 단계가 없는 워치 기록 */
	sleepStagesWeek: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000011',
			1,
			'sleeps',
			'2026-10-20',
			[402, 388, 415, 371, 430, 455, 396],
			{
				detail: {
					nights: [
						night('2026-10-20', 402, [78, 92, 210, 22]),
						night('2026-10-21', 388, [70, 85, 211, 22]),
						night('2026-10-22', 415, [84, 96, 215, 20]),
						night('2026-10-23', 371, [62, 80, 205, 24]),
						night('2026-10-24', 430, [88, 101, 221, 20]),
						night('2026-10-25', 455, null),
						night('2026-10-26', 396, [74, 90, 210, 22])
					]
				}
			}
		)
	],
	/** "지난 30일 중 가장 오래 운동한 날은?" 운동 기록 10개. 느린 걷기 하나는 운동으로 인정되지 않음 */
	exerciseSessionsMonth: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000012',
			1,
			'exercises',
			'2026-09-29',
			// 날짜별 값은 인정된 운동 시간의 합이다
			datesFrom('2026-09-29', 30).map((date) =>
				monthSessions
					.filter((item) => item.date === date && item.counts_as_exercise)
					.reduce((sum, item) => sum + item.duration_minutes, 0)
			),
			{ detail: { sessions: monthSessions } }
		)
	],
	/**
	 * "지난 6개월 운동 기록 보여 줘"(S15P31A403-430). 31일 제한으로 여섯 번 나눠 조회한 2026-05-21~11-17(181일).
	 * 사흘에 한 번 쉬고, 운동한 날은 하루 한 번 20~85분. 마지막 날은 오늘(수집 중)
	 */
	exerciseSixMonths: (() => {
		const dates = datesFrom('2026-05-21', 181);
		const minutes = dates.map((_, index) => (index % 3 === 2 ? 0 : 20 + ((index * 13) % 66)));
		const sessions = dates.flatMap((date, index) =>
			minutes[index] > 0 ? [session(date, index % 2 ? '달리기' : '걷기', minutes[index])] : []
		);
		const starts = [0, 31, 62, 93, 124, 155];
		return starts.map((start, part) => {
			const end = starts[part + 1] ?? dates.length;
			return makeEvidence(
				`e4000000-0000-4000-8000-00000000000${part + 1}`,
				part + 1,
				'exercises',
				dates[start],
				minutes.slice(start, end),
				{
					inProgressLast: end === dates.length,
					detail: {
						sessions: sessions.filter(
							(item) => item.date >= dates[start] && item.date <= dates[end - 1]
						)
					}
				}
			);
		});
	})(),
	/** "이번 주 운동 어땠어?" 운동 시간에 횟수·칼로리·거리가 함께 온 근거(S15P31A403-375) */
	exerciseActivityWeek: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000013',
			1,
			'exercises',
			'2026-10-22',
			[30, 0, 45, null, 60, 0, 20],
			{ inProgressLast: true, detail: { exercise_activity: weekActivity } }
		)
	],
	/** 신체 4종 7일. 체중은 이틀 비고 오늘은 진행 중(통계에서 빠짐), 키는 m로 와서 cm로 보인다 */
	bodyWeek: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000030',
			1,
			'weights',
			'2026-10-22',
			[68.4, 68.1, null, 67.9, null, 68.2, 68],
			{ inProgressLast: true }
		),
		makeEvidence('e1000000-0000-4000-8000-000000000031', 2, 'body_fats', '2026-10-22', [
			24.1,
			23.9,
			null,
			23.8,
			null,
			23.6,
			null
		]),
		makeEvidence('e1000000-0000-4000-8000-000000000032', 3, 'basal_metabolic_rates', '2026-10-22', [
			1480,
			1476,
			null,
			1478,
			null,
			1482,
			null
		]),
		makeEvidence('e1000000-0000-4000-8000-000000000033', 4, 'heights', '2026-10-22', [
			1.725,
			null,
			null,
			null,
			null,
			1.724,
			null
		])
	],
	/** 체중이 오늘 측정값만 있음. 평균·최저·최고는 null 이어도 오늘 값은 보인다 */
	bodyTodayOnly: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000034',
			1,
			'weights',
			'2026-10-22',
			[null, null, null, null, null, null, 68.3],
			{ inProgressLast: true }
		)
	],
	/** 체중 90일. 주 단위 막대는 평균이 아니라 그 주 마지막 측정값이다 */
	bodyQuarter: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000035',
			1,
			'weights',
			'2026-07-30',
			wave(91, 68, 0.3)
		)
	],
	/** 비교 기간에 기록이 하나도 없음. 차이를 낼 수 없어 "-"와 "이전 기간 기록 없음"이 보인다(S15P31A403-404) */
	comparisonEmpty: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000070',
			1,
			'steps',
			'2026-10-22',
			wave(7, 7800, 210)
		),
		makeEvidence(
			'e1000000-0000-4000-8000-000000000071',
			2,
			'steps',
			'2026-10-15',
			Array(7).fill(null)
		)
	],
	/** 산소포화도에 비교 기간이 붙음. 평균은 큰 상자에, 측정 최저·최고는 아래 두 칸에 따로 놓인다 */
	spo2Compare: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000072',
			1,
			'oxygen_saturations',
			'2026-10-22',
			[96.1, 95.4, 96.8, 95.9, 96.3, 95.2, 96.6],
			{
				detail: {
					measured_min: { date: '2026-10-27', value: 89 },
					measured_max: { date: '2026-10-24', value: 99 }
				}
			}
		),
		makeEvidence(
			'e1000000-0000-4000-8000-000000000073',
			2,
			'oxygen_saturations',
			'2026-10-15',
			[96.4, 96.9, 96.2, 97.1, 96.5, 96.8, 96.3]
		)
	],
	/** 심박 7일의 측정 최저·최고. 안내가 "7일 동안 측정된 값 중"으로 나온다(하루짜리는 heartOneDay) */
	heartWeekMeasured: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000074',
			1,
			'heart_rates',
			'2026-10-22',
			wave(7, 74, 2),
			{
				detail: {
					measured_min: { date: '2026-10-24', value: 52 },
					measured_max: { date: '2026-10-26', value: 141 },
					// 날짜별 실제 측정 최저·최고(S15P31A403-430). 10.28 은 그날 측정이 없다
					daily_ranges: [
						{ date: '2026-10-22', min: 55, max: 128, sample_count: 1310 },
						{ date: '2026-10-23', min: 58, max: 122, sample_count: 1284 },
						{ date: '2026-10-24', min: 52, max: 118, sample_count: 1296 },
						{ date: '2026-10-25', min: 57, max: 126, sample_count: 1302 },
						{ date: '2026-10-26', min: 60, max: 141, sample_count: 1277 },
						{ date: '2026-10-27', min: 56, max: 119, sample_count: 1290 },
						{ date: '2026-10-28', min: null, max: null, sample_count: null }
					]
				}
			}
		)
	],
	/** 하루 기간의 수면 단계. 평균이 아니라 그날 하루의 단계다 */
	sleepStagesOneDay: [
		makeEvidence('e1000000-0000-4000-8000-000000000075', 1, 'sleeps', '2026-10-27', [402], {
			detail: { nights: [night('2026-10-27', 402, [78, 92, 210, 22])] }
		})
	],
	/**
	 * "잠을 짧게 잔 날 심박수는?" 심박을 28일(9.30~10.27)과 최근 7일(10.22~10.28)로 두 번 조회했다(S15P31A403-439).
	 * 7일이 하루 늦게 끝나 일부만 겹친다. 걸음은 28일 한 번이다
	 */
	partialOverlapHeart: [
		makeEvidence(
			'e5000000-0000-4000-8000-000000000001',
			1,
			'steps',
			'2026-09-30',
			wave(28, 8100, 170)
		),
		makeEvidence(
			'e5000000-0000-4000-8000-000000000002',
			2,
			'heart_rates',
			'2026-09-30',
			wave(28, 72, 1)
		),
		makeEvidence(
			'e5000000-0000-4000-8000-000000000003',
			3,
			'heart_rates',
			'2026-10-22',
			[...wave(6, 71, 1), 58],
			{ inProgressLast: true }
		)
	],
	/** "어제 운동 얼마나 했어?" 하루에 운동 기록이 한 건뿐이다(S15P31A403-439) */
	exerciseOneSession: [
		makeEvidence('e5000000-0000-4000-8000-000000000004', 1, 'exercises', '2026-10-27', [78], {
			detail: {
				sessions: [session('2026-10-27', '근력 운동', 78)],
				exercise_activity: {
					days: [activityDay('2026-10-27', [1, 548, null], { distanceReason: 'NULL_VALUE' })],
					summary: {
						exercise_count: { total: 1, avg: 1, days_with_data: 1 },
						exercise_calories_kcal: { total: 548, avg: 548, days_with_data: 1 },
						exercise_distance_m: { total: null, avg: null, days_with_data: 0 }
					},
					source_statuses: {
						exercise_sessions: { last_sync_status: 'success' },
						calories_burned: { last_sync_status: 'success' },
						distances: { last_sync_status: 'success' }
					}
				}
			}
		})
	],
	/** "오늘 운동 얼마나 했어?" 오늘 기록이 아직 없고 횟수·칼로리·거리도 모두 비어 온다(S15P31A403-439) */
	exerciseTodayEmpty: [
		makeEvidence('e5000000-0000-4000-8000-000000000005', 1, 'exercises', '2026-10-28', [null], {
			reasons: { 0: 'UNSYNCED' },
			inProgressLast: true,
			detail: {
				exercise_activity: {
					days: [
						activityDay('2026-10-28', [null, null, null], { inProgress: true, reason: 'UNSYNCED' })
					],
					summary: {
						exercise_count: { total: null, avg: null, days_with_data: 0 },
						exercise_calories_kcal: { total: null, avg: null, days_with_data: 0 },
						exercise_distance_m: { total: null, avg: null, days_with_data: 0 }
					},
					source_statuses: {
						exercise_sessions: { last_sync_status: 'success' },
						calories_burned: { last_sync_status: 'success' },
						distances: { last_sync_status: 'success' }
					}
				}
			}
		})
	],
	/** "최근 석 달 걸음 수"처럼 앞쪽 40일이 연동 전인 긴 기간(7.31~10.28). 9월 9일에 연동했다(S15P31A403-439) */
	stepsBeforeLinkMonths: [
		makeEvidence(
			'e5000000-0000-4000-8000-000000000006',
			1,
			'steps',
			'2026-07-31',
			wave(90, 7300, 450).map((value, index) => (index < 40 ? null : value)),
			{
				reasons: Object.fromEntries(
					Array.from({ length: 40 }, (_, index) => [index, 'BEFORE_LINK' as const])
				)
			}
		)
	],
	/** "8월 10일 걸음 수 알려줘" 물은 하루가 앱 연동 전이다(S15P31A403-439) */
	stepsBeforeLinkDay: [
		makeEvidence('e5000000-0000-4000-8000-000000000007', 1, 'steps', '2026-08-10', [null], {
			reasons: { 0: 'BEFORE_LINK' }
		})
	],
	/** 한 달 수면에서 산소포화도 90% 미만인 날이 하루도 없음 */
	sleepSpo2MonthClean: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000076',
			1,
			'sleeps',
			'2026-10-01',
			datesFrom('2026-10-01', 30).map((_, index) => 380 + ((index * 13) % 60)),
			{
				detail: {
					nights: datesFrom('2026-10-01', 30).map((date, index) => ({
						...night(date, 380 + ((index * 13) % 60), [70, 85, 205, 20]),
						spo2_avg: 95.5 + ((index * 3) % 10) * 0.1,
						spo2_min: 91 + (index % 4),
						spo2_max: 98,
						spo2_low_count: 0,
						spo2_low_times: []
					}))
				}
			}
		)
	],
	/**
	 * "한 달 전이랑 비교해서 수면이 어떻게 변했어"(S15P31A403-418): 이전 30일·최근 30일과 함께 두 달 전체를 한 번 더 조회한다.
	 * 두 달 전체는 앞의 두 기간과 날짜가 겹친다
	 */
	sleepMonthCompareOverlap: (
		[
			['2026-09-18', 30, 0],
			['2026-09-20', 58, 2],
			['2026-10-18', 30, 30]
		] as const
	).map(([start, count, offset], order) => {
		const dates = datesFrom(start, count);
		// 날짜마다 값이 같도록 9.18 부터 센 날 수로 만든다(겹치는 기간끼리 같은 날은 같은 값)
		const minutes = dates.map((_, index) => {
			const day = index + offset;
			return 395 + ((day * 37) % 95) + (day >= 30 ? 8 : 0);
		});
		return makeEvidence(
			`e1000000-0000-4000-8000-0000000000a${order}`,
			order + 1,
			'sleeps',
			start,
			minutes,
			{
				detail: {
					nights: dates.map((date, index) => {
						const day = index + offset;
						const deep = 48 + ((day * 11) % 32);
						const rem = 95 + ((day * 7) % 40);
						const awake = 12 + ((day * 5) % 14);
						return {
							...night(date, minutes[index], [deep, rem, minutes[index] - deep - rem, awake]),
							spo2_avg: 95 + ((day * 3) % 12) * 0.1,
							spo2_min: 92 + (day % 3),
							spo2_max: 98 + (day % 2) * 0.5,
							spo2_low_count: 0,
							spo2_low_times: []
						};
					})
				}
			}
		);
	}),
	/**
	 * 한 달을 넘는 수면(35일, S15P31A403-418). "자는 동안 다른 이상은 없었어?"처럼 최근 5주를 한 번에 조회한 경우다.
	 * 차트는 주 단위로 묶이고, 날짜별 수면 단계는 최근 7일부터 넘겨 본다
	 */
	sleepFiveWeeks: [
		(() => {
			const dates = datesFrom('2026-10-13', 35);
			const minutes = dates.map((_, day) => 400 + ((day * 37) % 95) + (day >= 28 ? 14 : 0));
			return makeEvidence(
				'e1000000-0000-4000-8000-0000000000b0',
				1,
				'sleeps',
				'2026-10-13',
				minutes,
				{
					detail: {
						nights: dates.map((date, day) => {
							const deep = 48 + ((day * 11) % 32);
							const rem = 95 + ((day * 7) % 40);
							return {
								...night(date, minutes[day], [
									deep,
									rem,
									minutes[day] - deep - rem,
									12 + ((day * 5) % 14)
								]),
								spo2_avg: 95 + ((day * 3) % 12) * 0.1,
								spo2_min: 92 + (day % 3),
								spo2_max: 98 + (day % 2) * 0.5,
								spo2_low_count: 0,
								spo2_low_times: []
							};
						})
					}
				}
			);
		})()
	],
	/** 운동 14일의 횟수·칼로리·거리. 7일을 넘어 날짜별 표가 접힌다 */
	exerciseActivityTwoWeeks: [
		makeEvidence(
			'e1000000-0000-4000-8000-000000000077',
			1,
			'exercises',
			'2026-10-14',
			wave(14, 35, 5),
			{ detail: { exercise_activity: activityRange('2026-10-14', 14) } }
		)
	],
	/** 운동 3주를 한 주씩 나눠 조회. 합친 범위는 합계·평균을 다시 내지 않는다 */
	exerciseActivitySplit: ['2026-10-07', '2026-10-14', '2026-10-21'].map((start, index) =>
		makeEvidence(
			`e1000000-0000-4000-8000-00000000007${index + 8}`,
			index + 1,
			'exercises',
			start,
			wave(7, 35, 5),
			{ detail: { exercise_activity: activityRange(start, 7) } }
		)
	),
	/**
	 * 한 달 걸음인데 최근 8일이 동기화 전(S15P31A403-420). 최근 7일부터 보이면 처음 화면이 빈 칸뿐이라,
	 * 값이 있는 마지막 날(10.22)이 오른쪽 끝에 오게 시작해야 한다
	 */
	stepsRecentGap: [
		makeEvidence(
			'e1000000-0000-4000-8000-0000000000c0',
			1,
			'steps',
			'2026-10-01',
			wave(30, 7600, 420).map((value, index) => (index >= 22 ? null : value)),
			{
				reasons: Object.fromEntries(
					Array.from({ length: 8 }, (_, index) => [22 + index, 'UNSYNCED'])
				)
			}
		)
	],
	/** 빈 날의 이유 세 가지: 값만 없음(10.23), 기록 없음(10.26), 동기화 전(10.28) */
	missingReasons: [
		makeEvidence(
			'e1000000-0000-4000-8000-00000000007b',
			1,
			'steps',
			'2026-10-22',
			[8120, null, 7640, 9010, null, 8330, null],
			{ reasons: { 1: 'NULL_VALUE', 4: 'NO_RECORD', 6: 'UNSYNCED' } }
		)
	]
} satisfies Record<string, MetricEvidence[]>;

/**
 * 모양이 다른 근거(snapshot.kind, S15P31A403-312). 백엔드가 준 반환 예시(합성 값)를 그대로 옮겼다.
 * 웹이 읽지 않는 키(심박·단계·data_status 등)도 그대로 두어 파서가 버리는지 함께 본다
 */
const sleepSessionsSnapshot = {
	kind: 'sleep_sessions',
	period: { start: '2026-11-04', end: '2026-11-05' },
	sessions: [
		{
			session_id: 's-001',
			is_main: true,
			computed_at: '2026-11-04T22:00:00Z',
			date: '2026-11-05',
			start_at: '2026-11-04T14:10:00Z',
			end_at: '2026-11-04T21:40:00Z',
			sleep_minutes: 420,
			deep_minutes: 80,
			rem_minutes: 95,
			light_minutes: 245,
			awake_minutes: 30,
			session_minutes: 450,
			unspecified_minutes: 0,
			unknown_minutes: 0,
			avg_heart_rate: 58,
			min_heart_rate: 51,
			max_heart_rate: 72,
			spo2_avg: 96.1,
			spo2_min: 89,
			spo2_max: 98,
			spo2_count: 40,
			spo2_low_count: 5,
			spo2_low_times: [
				'2026-11-05T02:02:00+09:00',
				'2026-11-05T02:47:00+09:00',
				'2026-11-05T03:15:00+09:00',
				'2026-11-04T19:31:00Z',
				'2026-11-05T05:10:00+09:00'
			]
		},
		{
			session_id: 's-002',
			is_main: false,
			computed_at: '2026-11-05T06:00:00Z',
			date: '2026-11-05',
			start_at: '2026-11-05T05:00:00Z',
			end_at: '2026-11-05T05:40:00Z',
			sleep_minutes: 35,
			deep_minutes: null,
			rem_minutes: null,
			light_minutes: null,
			awake_minutes: 5,
			session_minutes: 40,
			unspecified_minutes: 35,
			unknown_minutes: 0,
			avg_heart_rate: null,
			min_heart_rate: null,
			max_heart_rate: null,
			spo2_avg: null,
			spo2_min: null,
			spo2_max: null,
			spo2_count: null,
			spo2_low_count: null,
			spo2_low_times: null
		},
		{
			// 야간 근무 뒤 아침에 잠든 주 수면. 재집계 전이라 시각 없이 횟수만 있다
			session_id: 's-003',
			is_main: true,
			computed_at: '2026-11-04T06:00:00Z',
			date: '2026-11-04',
			start_at: '2026-11-03T23:30:00Z',
			end_at: '2026-11-04T05:20:00Z',
			sleep_minutes: 330,
			deep_minutes: 60,
			rem_minutes: 70,
			light_minutes: 190,
			awake_minutes: 10,
			session_minutes: 350,
			unspecified_minutes: 0,
			unknown_minutes: 0,
			avg_heart_rate: 60,
			min_heart_rate: 52,
			max_heart_rate: 75,
			spo2_avg: 95.8,
			spo2_min: 88,
			spo2_max: 98,
			spo2_count: 30,
			spo2_low_count: 2,
			spo2_low_times: null
		}
	],
	data_status: {
		last_sync_status: 'success',
		last_data_at: '2026-11-05T06:00:00Z',
		last_success_at: '2026-11-05T06:10:00Z',
		checked_at: '2026-11-05T07:00:00Z'
	}
} as const;

const hourlyStepsSnapshot = {
	kind: 'hourly_steps',
	period: { start: '2026-11-03', end: '2026-11-05' },
	start_hour: 6,
	end_hour: 10,
	days: [
		{ date: '2026-11-03', is_in_progress: false, values: null, total: null, reason: 'NO_RECORD' },
		{
			date: '2026-11-04',
			is_in_progress: false,
			values: [120, 850, 1430, 610],
			total: 3010,
			reason: null
		},
		{ date: '2026-11-05', is_in_progress: true, values: [90, 300, 0, 0], total: 390, reason: null }
	],
	// 백엔드 규칙: 하루라도 values 가 null 이면 기간 합계도 null(날짜별 합계는 있어도)
	total: null,
	data_status: {
		last_data_at: '2026-11-05T00:40:00Z',
		synced_until: '2026-11-05T00:40:00Z',
		last_sync_status: 'success',
		last_success_at: '2026-11-05T00:45:00Z',
		checked_at: '2026-11-05T01:00:00Z'
	}
} as const;

/** 서버가 보내는 그대로의 근거 JSON(파서 입력) */
export const kindEvidenceJson = {
	sleepSessions: {
		id: '88888888-8888-4888-8888-888888888888',
		position: 1,
		metric: 'sleeps',
		periodStart: '2026-11-04',
		periodEnd: '2026-11-05',
		snapshot: sleepSessionsSnapshot
	},
	hourlySteps: {
		id: '99999999-9999-4999-8999-999999999999',
		position: 2,
		metric: 'steps',
		periodStart: '2026-11-03',
		periodEnd: '2026-11-05',
		snapshot: hourlyStepsSnapshot
	},
	/** 모든 날에 기록이 있어 기간 합계가 있다. 오늘(진행 중) 걸음도 합계에 들어간다(3,010 + 390) */
	hourlyStepsComplete: {
		id: '99999999-9999-4999-8999-999999999997',
		position: 2,
		metric: 'steps',
		periodStart: '2026-11-04',
		periodEnd: '2026-11-05',
		snapshot: {
			...hourlyStepsSnapshot,
			period: { start: '2026-11-04', end: '2026-11-05' },
			days: hourlyStepsSnapshot.days.slice(1),
			total: 3400
		}
	},
	/** 하루 24시간 전체 열. 휴대폰 폭에서 표만 가로로 밀린다 */
	hourlyStepsFullDay: {
		id: '99999999-9999-4999-8999-999999999998',
		position: 2,
		metric: 'steps',
		periodStart: '2026-11-04',
		periodEnd: '2026-11-05',
		snapshot: {
			...hourlyStepsSnapshot,
			period: { start: '2026-11-04', end: '2026-11-05' },
			start_hour: 0,
			end_hour: 24,
			days: [
				{
					date: '2026-11-04',
					is_in_progress: false,
					values: Array.from({ length: 24 }, (_, hour) =>
						hour >= 7 && hour <= 21 ? 300 + hour * 10 : 0
					),
					total: 6270,
					reason: null
				},
				{
					date: '2026-11-05',
					is_in_progress: true,
					values: null,
					total: null,
					reason: 'NULL_VALUE'
				}
			],
			total: null
		}
	},
	/** 기간 안에 수면 세션이 없음 */
	sleepSessionsEmpty: {
		id: '88888888-8888-4888-8888-888888888887',
		position: 1,
		metric: 'sleeps',
		periodStart: '2026-11-04',
		periodEnd: '2026-11-05',
		snapshot: { ...sleepSessionsSnapshot, sessions: [] }
	},
	/** 아직 모양이 정해지지 않은 종류. 웹은 이 근거만 숨긴다 */
	exerciseIntervals: {
		id: '77777777-7777-4777-8777-777777777777',
		position: 3,
		metric: 'exercises',
		periodStart: '2026-11-04',
		periodEnd: '2026-11-05',
		snapshot: { kind: 'exercise_intervals', intervals: [] }
	}
};

/** 수면 공통 원형 밤 목록에 산소포화도 네 가지 상태가 모두 있는 7일 */
const spo2Nights: SleepNight[] = [
	{ ...night('2026-10-21', 388, [70, 85, 211, 22]), spo2_low_count: null, spo2_low_times: null },
	{ ...night('2026-10-22', 415, [84, 96, 215, 20]), spo2_low_count: 0, spo2_low_times: [] },
	{ ...night('2026-10-23', 371, [62, 80, 205, 24]), spo2_low_count: 3, spo2_low_times: null },
	{
		...night('2026-10-24', 430, [88, 101, 221, 20]),
		spo2_low_count: 2,
		spo2_low_times: ['2026-10-23T17:12:00Z', '2026-10-23T19:40:00Z']
	}
];
export const sleepNightsSpo2: MetricEvidence[] = [
	makeEvidence(
		'e1000000-0000-4000-8000-000000000040',
		1,
		'sleeps',
		'2026-10-21',
		[388, 415, 371, 430],
		{ detail: { nights: spo2Nights } }
	)
];

/** 317: 장기 분석과 지난주/이번 주 조회가 겹치는 근거. 같은 밤이 53행으로 합쳐지던 사례 */
export const overlappingSleepEvidence: MetricEvidence[] = (
	[
		['2026-10-02', 42],
		['2026-11-02', 7],
		['2026-11-09', 4]
	] as const
).map(([start, count], index) => {
	const dates = datesFrom(start, count);
	const values = dates.map((date) => 300 + Number(date.slice(-2)));
	return makeEvidence(
		`e1000000-0000-4000-8000-00000000005${index}`,
		index,
		'sleeps',
		start,
		values,
		{
			detail: {
				nights: dates.map((date, i) => ({
					...night(date, values[i], [Number(date.slice(-2)), 90, 200, 20]),
					spo2_low_count: 0,
					spo2_low_times: []
				}))
			}
		}
	);
});

export const conversations: ConversationItem[] = [
	{
		id: 'c0000000-0000-4000-8000-000000000001',
		title: '수면이 늘었는데 활동량도 늘었어?',
		summary: '수면과 활동량 변화를 함께 살펴봤어요.',
		createdAt: '2026-10-14T04:59:10Z',
		updatedAt: '2026-10-14T04:59:40Z',
		lastRequestStatus: 'completed'
	},
	{
		id: 'c0000000-0000-4000-8000-000000000002',
		title: '지난주보다 얼마나 더 걸었어?',
		summary: null,
		createdAt: '2026-10-14T00:18:00Z',
		updatedAt: '2026-10-14T00:18:00Z',
		lastRequestStatus: 'generating'
	},
	{
		id: 'c0000000-0000-4000-8000-000000000003',
		title: '이 수치면 병이야? 무슨 약 먹어야 해?',
		summary: '기록 해석과 생활 습관 안내 범위를 알려 드렸어요.',
		createdAt: '2026-10-13T09:02:00Z',
		updatedAt: '2026-10-13T09:02:00Z',
		lastRequestStatus: 'completed'
	},
	{
		id: 'c0000000-0000-4000-8000-000000000004',
		title: '최근 수면 시간이 어떻게 달라졌어?',
		summary: '최근 수면 시간 변화를 살펴봤어요.',
		createdAt: '2026-10-13T05:36:00Z',
		updatedAt: '2026-10-13T05:36:00Z',
		lastRequestStatus: 'completed'
	},
	{
		id: 'c0000000-0000-4000-8000-000000000005',
		title: '산소포화도 기록 보여줘.',
		summary: null,
		createdAt: '2026-10-12T02:00:00Z',
		updatedAt: '2026-10-12T02:00:00Z',
		lastRequestStatus: 'completed'
	}
];

function message(
	conversationIndex: number,
	sequenceNo: number,
	role: CoachingMessage['role'],
	content: string,
	overrides: Partial<CoachingMessage> = {}
): CoachingMessage {
	const conversation = conversations[conversationIndex];
	return {
		id: `m000000${conversationIndex}-0000-4000-8000-00000000000${sequenceNo}`,
		requestId: `r000000${conversationIndex}-0000-4000-8000-000000000001`,
		role,
		sequenceNo,
		content,
		requestStatus: 'completed',
		errorCode: null,
		createdAt: conversation.createdAt,
		evidence: [],
		errorContext: null,
		...overrides
	};
}

export type ConversationFixture = {
	conversation: ConversationItem;
	messages: CoachingMessage[];
	/** assistant 메시지 id별로 명세에 아직 없는 화면 값 */
	extras: Record<string, PendingAnswerExtras>;
};

const answerAboutSleepAndSteps = [
	'수면과 활동량은 이전 7일보다 모두 늘었어요.',
	'최근 7일 평균 수면은 이전 7일보다 18분 늘었고, 평균 걸음 수는 이전 기간보다 13% 늘었어요.',
	'다만 변화의 원인을 단정하기는 어려우니, 두 기록을 함께 보면서 생활 패턴을 확인해 보세요.'
].join('\n');

export const conversationFixtures: ConversationFixture[] = [
	{
		conversation: conversations[0],
		messages: [
			message(0, 1, 'user', '수면이 늘었는데 활동량도 늘었어?'),
			message(0, 2, 'assistant', answerAboutSleepAndSteps, { evidence: sleepAndStepsEvidence })
		],
		extras: {
			'm0000000-0000-4000-8000-000000000002': {
				followUpQuestions: ['활동량이 많던 날 심박수는?', '수면 패턴 더 자세히 보기']
			}
		}
	},
	{
		conversation: conversations[1],
		messages: [
			message(1, 1, 'user', '지난주보다 얼마나 더 걸었어?', { requestStatus: 'generating' }),
			message(
				1,
				2,
				'assistant',
				'지난주보다 하루 평균 907보 더 걸었어요.\n최근 7일 평균 걸음 수는 이전 7일보다 13% 늘었고, 다만',
				{ requestStatus: 'generating' }
			)
		],
		extras: {}
	},
	{
		conversation: conversations[2],
		messages: [
			message(2, 1, 'user', '이 수치면 병이야? 무슨 약 먹어야 해?'),
			message(2, 2, 'assistant', '')
		],
		extras: {
			'm0000002-0000-4000-8000-000000000002': {
				boundary: {
					title: '기록 해석과 생활 습관 안내를 도와드릴 수 있어요.',
					lines: [
						'웨어러블 기록만으로 질병 여부를 판단하거나 복용할 약을 정해드릴 수는 없어요.',
						'건강 상태가 걱정되거나 불편한 증상이 있다면 의사와 상담해 주세요.',
						'약 복용에 관한 내용은 의사 또는 약사에게 확인해 주세요.'
					],
					alternativeTitle:
						'대신 기록된 수면과 활동량을 함께 살펴보고, 생활 습관의 변화를 정리하는 건 도와드릴 수 있어요.',
					alternativeBody: '최근 기록에서 어떤 변화가 있었는지 같이 확인해 볼까요?',
					suggestions: ['최근 수면 패턴을 알려줘', '활동량 변화를 확인해줘']
				}
			}
		}
	},
	{
		conversation: conversations[3],
		messages: [
			message(3, 1, 'user', '최근 수면 시간이 어떻게 달라졌어?'),
			message(
				3,
				2,
				'assistant',
				'최근 7일 평균 수면은 6시간 33분이에요.\n이전 7일 평균보다 18분 늘었어요. 10.05와 10.06은 기록이 없어 평균에서 뺐어요.',
				{ evidence: sleepAndStepsEvidence.slice(0, 2) }
			)
		],
		extras: {}
	},
	{
		conversation: conversations[4],
		messages: [
			message(4, 1, 'user', '산소포화도 기록 보여줘.'),
			message(
				4,
				2,
				'assistant',
				'요청한 기간에는 산소포화도 기록이 없어요.\n수면 중에 워치를 착용하면 산소포화도가 함께 기록돼요.'
			)
		],
		extras: {}
	}
];

/**
 * "한 달 동안 산소포화도 추이는?" 30일(S15P31A403-388). 값이 90~100에 모여 있고 이틀은 기록이 없다.
 * 측정 최저는 10.07, 최고는 10.19. 휴대폰 폭에서는 차트가 가로로 밀린다
 */
export const spo2Month: MetricEvidence[] = [
	makeEvidence(
		'e1000000-0000-4000-8000-000000000060',
		1,
		'oxygen_saturations',
		'2026-10-01',
		datesFrom('2026-10-01', 30).map((_, index) =>
			index === 11 || index === 12 ? null : 95.2 + ((index * 7) % 9) * 0.4
		),
		{
			detail: {
				measured_min: { date: '2026-10-07', value: 88 },
				measured_max: { date: '2026-10-19', value: 99.5 }
			}
		}
	)
];

/** 한 달 수면 근거의 날짜별 산소포화도 30밤. 평균·최저·최고가 실려 오고 두 밤은 90% 미만이 있었다 */
export const sleepSpo2Month: MetricEvidence[] = [
	makeEvidence(
		'e1000000-0000-4000-8000-000000000061',
		1,
		'sleeps',
		'2026-10-01',
		datesFrom('2026-10-01', 30).map((_, index) => 380 + ((index * 13) % 60)),
		{
			detail: {
				nights: datesFrom('2026-10-01', 30).map((date, index) => ({
					...night(date, 380 + ((index * 13) % 60), [70, 85, 205, 20]),
					spo2_avg: 95.5 + ((index * 3) % 10) * 0.1,
					spo2_min: index === 6 ? 86 : 91 + (index % 4),
					spo2_max: index === 18 ? 99.5 : 98,
					spo2_low_count: index === 6 || index === 20 ? 2 : 0,
					spo2_low_times: null
				}))
			}
		}
	)
];
