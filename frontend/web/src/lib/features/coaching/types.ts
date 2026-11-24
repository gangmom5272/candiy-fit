/**
 * 명세 2.2 공개 복수 지표 코드. 신체 4종(체중·체지방률·기초대사량·키)은 S15P31A403-311에서 추가했다.
 * 근거와 실패 안내(errorContext.metrics) 모두 이 목록으로 검사하므로, 백엔드보다 먼저 넣어야 메시지가 깨지지 않는다
 */
export const HEALTH_METRICS = [
	'steps',
	'heart_rates',
	'oxygen_saturations',
	'sleeps',
	'exercises',
	'weights',
	'body_fats',
	'basal_metabolic_rates',
	'heights'
] as const;
export const REQUEST_STATUSES = ['accepted', 'generating', 'completed', 'failed'] as const;
export const MISSING_REASONS = [
	'NO_RECORD',
	'NULL_VALUE',
	'ZERO_STEPS',
	'UNSYNCED',
	'BEFORE_LINK'
] as const;
export const COACHING_STAGES = [
	'reading_history',
	'fetching_health_data',
	'generating_answer'
] as const;
export const HEALTH_ERROR_REASONS = ['DATA_NOT_READY', 'LOOKUP_FAILED'] as const;

/** 명세 2.2 공개 복수 지표 코드 */
export type HealthMetric = (typeof HEALTH_METRICS)[number];

/** 신체 지표. 날짜 값이 그날 마지막 측정값이라 웹에서 합치거나 평균 내지 않는다(summary.total 은 항상 null) */
export const BODY_METRICS: readonly HealthMetric[] = [
	'weights',
	'body_fats',
	'basal_metabolic_rates',
	'heights'
];

/** 명세 6.2·6.3 요청 상태 */
export type RequestStatus = (typeof REQUEST_STATUSES)[number];

export type MissingReason = (typeof MISSING_REASONS)[number];

/** 명세 7.4 status 이벤트의 진행 단계 */
export type CoachingStage = (typeof COACHING_STAGES)[number];

/** 명세 2.4 건강 조회 실패 사유. 메시지·요청 조회의 errorContext와 WS error에 쓰인다. */
export type HealthErrorContext = {
	reason: (typeof HEALTH_ERROR_REASONS)[number];
	metrics: HealthMetric[];
};

type DatedValue = { date: string; value: number };

/** 명세 2.3 근거 snapshot. 공통 원형을 snake_case 그대로 쓴다. */
export type EvidenceSnapshot = {
	metric: HealthMetric;
	period: { start: string; end: string };
	/** 신체 지표: 체중 kg, 체지방률 %, 기초대사량 kcal/day, 키 m(화면은 cm) */
	unit: 'steps' | 'bpm' | '%' | 'min' | 'kg' | 'kcal/day' | 'm';
	summary: {
		avg: number | null;
		daily_min: DatedValue | null;
		daily_max: DatedValue | null;
		total: number | null;
		days_with_data: number;
		days_used_for_summary: number;
		completed_days: number;
		missing_days: string[];
		unsynced_days: string[];
		linked_from: string | null;
	};
	days: {
		date: string;
		value: number | null;
		missing: boolean;
		reason: MissingReason | null;
		is_in_progress: boolean;
	}[];
	in_progress: {
		date: string;
		value: number | null;
		missing: boolean;
		as_of: string | null;
	} | null;
	data_status: {
		last_data_at: string | null;
		synced_until: string | null;
		last_sync_status: 'success' | 'fail' | null;
		last_success_at: string | null;
		checked_at: string;
	};
	detail: {
		measured_min: DatedValue | null;
		measured_max: DatedValue | null;
		nights: SleepNight[];
		daily_ranges: unknown[];
		sessions: ExerciseSession[];
		/** 운동 지표에만 온다(S15P31A403-375). 예전 근거·다른 지표에는 키가 없다 */
		exercise_activity?: ExerciseActivity;
	};
};

/** 명세 2.2 detail.nights. 주 수면만, 날짜는 깬 날(KST) */
export type SleepNight = {
	date: string;
	start_at: string;
	end_at: string;
	sleep_minutes: number | null;
	deep_minutes: number | null;
	rem_minutes: number | null;
	light_minutes: number | null;
	awake_minutes: number | null;
	/** 그 밤 수면 중 평균·최저·최고 산소포화도(%). 키가 없거나 형식이 다른 근거는 undefined */
	spo2_avg?: number;
	spo2_min?: number;
	spo2_max?: number;
	/**
	 * 90% 미만 산소포화도 횟수·시각(S15P31A403-312). 키가 없는 예전 근거는 undefined 로 두어
	 * "측정 없음"(null)과 구분한다. 네 가지 조합은 evidence-view.ts spo2LowState 를 따른다
	 */
	spo2_low_count?: number | null;
	spo2_low_times?: string[] | null;
};

/** 명세 2.2 detail.sessions 중 화면에 쓰는 값. 거리·칼로리·심박 등 나머지 키는 읽지 않는다 */
export type ExerciseSession = {
	date: string;
	start_at: string;
	duration_minutes: number;
	type_name: string;
	title: string | null;
	/** 운동 인정 여부. false면 목록에는 두고 운동 시간·횟수에서는 뺀다 */
	counts_as_exercise: boolean;
};

/** 운동 활동값 중 화면에 쓰는 세 항목. 운동 시간(exercise_minutes)은 공통 days·summary 와 같은 값이라 읽지 않는다 */
export const EXERCISE_ACTIVITY_FIELDS = [
	'exercise_count',
	'exercise_calories_kcal',
	'exercise_distance_m'
] as const;
export type ExerciseActivityField = (typeof EXERCISE_ACTIVITY_FIELDS)[number];

/** 값을 가져오는 원천. 수집에 실패하면 이전에 저장된 기록 기준이라고 알린다 */
export const EXERCISE_ACTIVITY_SOURCES = [
	'exercise_sessions',
	'calories_burned',
	'distances'
] as const;
export type ExerciseActivitySource = (typeof EXERCISE_ACTIVITY_SOURCES)[number];

/** 한 항목의 기간 통계. 값이 있는 완료 날짜만 센다(오늘·연동 전 제외). 값이 하나도 없으면 total·avg 가 null 이다 */
export type ExerciseActivityStat = {
	total: number | null;
	avg: number | null;
	days_with_data: number;
};

export type ExerciseActivityDay = {
	date: string;
	/** 인정된 운동만 센다 */
	exercise_count: number | null;
	/** 미인정 운동까지 포함한 운동 기록의 값. 하루 전체 소모 칼로리가 아니다 */
	exercise_calories_kcal: number | null;
	/** 미인정 운동까지 포함. 단위 m. 하루 이동 거리가 아니다 */
	exercise_distance_m: number | null;
	is_in_progress: boolean;
	/** 횟수가 없는 이유(운동 시간과 같은 판정) */
	reason: MissingReason | null;
	calories_reason: MissingReason | null;
	distance_reason: MissingReason | null;
};

/**
 * 운동 근거의 detail.exercise_activity(backend/docs/exercise-activity-detail-318.md). AI 가 운동 요약·추이에서 읽는 값이다.
 * summary 는 서버가 낸 값을 그대로 쓴다. 나눠 조회한 기간을 합친 범위는 null 이다(웹에서 다시 더하지 않는다)
 */
export type ExerciseActivity = {
	days: ExerciseActivityDay[];
	summary: Record<ExerciseActivityField, ExerciseActivityStat> | null;
	source_statuses: Partial<
		Record<ExerciseActivitySource, { last_sync_status: 'success' | 'fail' | null }>
	>;
};

/** 명세 2.3 근거 중 날짜별 지표 공통 원형(snapshot 에 kind 가 없다) */
export type MetricEvidence = {
	id: string;
	position: number;
	metric: HealthMetric;
	periodStart: string;
	periodEnd: string;
	snapshot: EvidenceSnapshot;
};

/** 낮잠 포함 전체 수면 세션 한 건(kind sleep_sessions). 화면에 쓰는 키만 둔다 */
export type SleepSession = {
	session_id: string;
	/** 주 수면이면 true. false 는 낮잠만이 아니라 밤에 다시 잠든 두 번째 수면도 된다 */
	is_main: boolean;
	/** 종료 시각의 KST 날짜. 밤·아침·낮 라벨은 start_at 으로 계산한다 */
	date: string;
	start_at: string;
	end_at: string;
	sleep_minutes: number | null;
	session_minutes: number;
	spo2_low_count: number | null;
	spo2_low_times: string[] | null;
};

export type SleepSessionsSnapshot = {
	kind: 'sleep_sessions';
	period: { start: string; end: string };
	sessions: SleepSession[];
};

/** 시간대별 걸음(kind hourly_steps). values[i] 는 start_hour + i 시의 걸음 */
export type HourlyStepsSnapshot = {
	kind: 'hourly_steps';
	period: { start: string; end: string };
	start_hour: number;
	end_hour: number;
	days: {
		date: string;
		is_in_progress: boolean;
		values: number[] | null;
		total: number | null;
		reason: 'NO_RECORD' | 'NULL_VALUE' | null;
	}[];
	/** 기간 합. 값이 하나도 없으면 null */
	total: number | null;
};

/** 명세 2.3 근거 중 모양이 다른 종류(snapshot.kind). 웹이 그리는 종류만 둔다 */
export type KindEvidence = Omit<MetricEvidence, 'snapshot'> & {
	snapshot: SleepSessionsSnapshot | HourlyStepsSnapshot;
};

/** 명세 2.3 근거 */
export type Evidence = MetricEvidence | KindEvidence;

export function isKindEvidence(item: Evidence): item is KindEvidence {
	return 'kind' in item.snapshot;
}

/** 명세 6.3 대화 정보 */
export type ConversationSummary = {
	id: string;
	title: string;
	summary: string | null;
	createdAt: string;
	updatedAt: string;
};

/** 명세 6.2 대화 목록 항목. 대화는 첫 질문과 함께 생기므로 lastRequestStatus는 null이 아니다(BE 확정 2026-10-26). */
export type ConversationItem = {
	id: string;
	title: string;
	summary: string | null;
	createdAt: string;
	updatedAt: string;
	lastRequestStatus: RequestStatus;
};

/** 명세 6.3 메시지 */
export type CoachingMessage = {
	id: string;
	requestId: string;
	role: 'user' | 'assistant';
	sequenceNo: number;
	content: string;
	requestStatus: RequestStatus;
	errorCode: string | null;
	createdAt: string;
	evidence: Evidence[];
	errorContext: HealthErrorContext | null;
};

/** 명세 6.2 응답. nextCursor가 null이면 마지막 페이지다(BE 확정 2026-10-26). */
export type ConversationPage = {
	items: ConversationItem[];
	nextCursor: string | null;
};

/**
 * 명세 6.3 응답. items는 sequenceNo 오름차순이다. nextBeforeSequenceNo는 이번 페이지의 가장 작은 sequenceNo이며
 * 다음 조회의 beforeSequenceNo(그 번호 미만)로 그대로 넘긴다. null이면 더 이전 메시지가 없다(BE 확정 2026-10-26).
 */
export type MessagePage = {
	conversation: ConversationSummary;
	items: CoachingMessage[];
	nextBeforeSequenceNo: number | null;
};

/** 명세 6.4·7.4의 assistantMessage */
export type AssistantMessageSnapshot = {
	id: string;
	role: 'assistant';
	sequenceNo: number;
	content: string;
	createdAt: string;
};

/** 명세 6.4 요청 상태. request_snapshot 이벤트의 data와 같다. */
export type RequestSnapshot = {
	requestId: string;
	clientRequestId: string;
	conversationId: string;
	status: RequestStatus;
	userMessageId: string;
	assistantMessage: AssistantMessageSnapshot;
	evidence: Evidence[];
	errorCode: string | null;
	createdAt: string;
	finishedAt: string | null;
	errorContext: HealthErrorContext | null;
};

/**
 * 시안의 화면 요소 중 API 명세에 아직 없는 값.
 * BE·AI 담당 확인 전까지 더미 데이터로만 채운다.
 */
export type PendingAnswerExtras = {
	/** 진단·처방 질문에 대한 경계 안내 */
	boundary?: BoundaryNoticeContent;
	/** 답변 아래 "이어서 물어보기" 추천 질문 */
	followUpQuestions?: string[];
};

export type BoundaryNoticeContent = {
	title: string;
	lines: string[];
	alternativeTitle: string;
	alternativeBody: string;
	suggestions: string[];
};

/** 명세 7.3 질문 메시지 */
export type QuestionMessage = {
	type: 'question';
	clientRequestId: string;
	conversationId: string | null;
	content: string;
};

/** 명세 7.4 요청 이벤트 공통 틀. 접수 전 error는 requestId·conversationId가 null이다. */
type RequestEventBase = {
	clientRequestId: string | null;
	requestId: string | null;
	conversationId: string | null;
	eventSeq: number;
};

export type CoachingErrorData = {
	code: string;
	message: string;
	retryable: boolean;
	accepted: boolean;
	reason: HealthErrorContext['reason'] | null;
	metrics: HealthMetric[] | null;
};

/** 명세 7.4·7.5 서버 → 클라이언트 요청 이벤트 */
export type CoachingRequestEvent = RequestEventBase &
	(
		| {
				type: 'accepted';
				data: {
					userMessageId: string;
					assistantMessageId: string;
					userSequenceNo: number;
					assistantSequenceNo: number;
					conversation: Omit<ConversationSummary, 'summary'>;
				};
		  }
		| { type: 'status'; data: { stage: CoachingStage } }
		| { type: 'delta'; data: { text: string } }
		| { type: 'evidence'; data: { items: Evidence[] } }
		| {
				type: 'completed';
				data: {
					assistantMessage: AssistantMessageSnapshot;
					evidence: Evidence[];
					finishedAt: string;
				};
		  }
		| { type: 'error'; data: CoachingErrorData }
		| { type: 'request_snapshot'; data: RequestSnapshot }
	);

/**
 * 명세 7.2 connection_error 코드. 웹에는 연결 시점에 AUTH_REQUIRED(재로그인)·AUTH_UNAVAILABLE(일시 장애)만 온다.
 * 질문 시점 인증 실패는 connection_error가 아니라 요청 단위 error로 온다(명세 v47)
 */
export const CONNECTION_ERROR_CODES = [
	'AUTH_REQUIRED',
	'AUTH_EXPIRED',
	'AUTH_UNAVAILABLE'
] as const;

/** 명세 7.2 연결 이벤트 */
export type CoachingConnectionEvent =
	| { type: 'ready'; data: { protocolVersion: string } }
	| {
			type: 'connection_error';
			data: { code: (typeof CONNECTION_ERROR_CODES)[number]; message: string };
	  };
