import type { ExerciseActivityField, HealthMetric, RequestStatus } from './types';

const KST = 'Asia/Seoul';

/** 시안의 표기를 따른다. 걸음은 "활동"으로 보여 준다. */
export const METRIC_LABELS: Record<HealthMetric, string> = {
	sleeps: '수면',
	steps: '활동',
	heart_rates: '심박',
	oxygen_saturations: '산소포화도',
	exercises: '운동',
	weights: '체중',
	body_fats: '체지방률',
	basal_metabolic_rates: '기초대사량',
	heights: '키'
};

export const REQUEST_STATUS_LABELS: Record<RequestStatus, string> = {
	accepted: '질문 접수',
	generating: '답변 작성 중',
	completed: 'AI 답변 완료',
	failed: '답변 실패'
};

function formatMinutes(minutes: number): string {
	const rounded = Math.round(Math.abs(minutes));
	const hours = Math.floor(rounded / 60);
	const rest = rounded % 60;
	if (hours === 0) return `${rest}분`;
	return rest === 0 ? `${hours}시간` : `${hours}시간 ${rest}분`;
}

/** 지표 값을 단위와 함께 표시한다. 값이 없으면 0이 아닌 "-"를 돌려준다. */
export function formatMetricValue(metric: HealthMetric, value: number | null): string {
	if (value === null) return '-';

	switch (metric) {
		case 'sleeps':
		case 'exercises':
			return formatMinutes(value);
		// AI 답변은 걸음 수를 "9,559걸음"으로 쓴다. 창도 같은 단위로 맞춘다(S15P31A403-427). 처음에는 시안을 따라 "보"였다
		case 'steps':
			return `${Math.round(value).toLocaleString('ko-KR')}걸음`;
		// AI 답변은 심박을 소수 첫째 자리까지 쓴다(ai eval scoring 의 표시 자리수). 창이 정수로 반올림하면
		// 답변 73.8bpm 과 창 74bpm 처럼 달라 보여 같은 자리수로 맞춘다(S15P31A403-420). .0 은 뺀다
		case 'heart_rates':
			return `${oneDecimal(value)}bpm`;
		case 'oxygen_saturations':
			return `${value.toFixed(1)}%`;
		// 신체 지표는 그날 마지막 측정값이다. 소수 한 자리까지, .0 은 뺀다
		case 'weights':
			return `${oneDecimal(value)}kg`;
		case 'body_fats':
			return `${oneDecimal(value)}%`;
		case 'basal_metabolic_rates':
			return `${Math.round(value).toLocaleString('ko-KR')}kcal`;
		// 키는 m로 저장·전달되고 화면에서만 cm로 바꾼다(백엔드 합의)
		case 'heights':
			return `${oneDecimal(metersToCentimeters(value))}cm`;
	}
}

/**
 * 운동 횟수·칼로리·거리 값(S15P31A403-375). AI 가 답변에 쓰는 표시(ai tools/exercise_activity.py)와 같게 맞춘다:
 * 횟수·칼로리는 반올림한 정수, 거리는 m 를 km 소수 한 자리로.
 * [average]면 횟수만 소수 한 자리까지 둔다 — 하루 평균 0.4회를 0회로 쓰면 운동을 안 한 것처럼 읽힌다
 */
export function formatExerciseActivity(
	field: ExerciseActivityField,
	value: number,
	average = false
): string {
	switch (field) {
		case 'exercise_count':
			return `${average ? oneDecimal(value) : Math.round(value).toLocaleString('ko-KR')}회`;
		case 'exercise_calories_kcal':
			return `${Math.round(value).toLocaleString('ko-KR')}kcal`;
		case 'exercise_distance_m':
			return `${(Math.round(value / 100) / 10).toFixed(1)}km`;
	}
}

/** 키 m → cm. 1.725 × 100 의 소수 오차를 없앤다 */
export function metersToCentimeters(meters: number): number {
	return Math.round(meters * 1000) / 10;
}

function oneDecimal(value: number): string {
	return (Math.round(value * 10) / 10).toFixed(1).replace(/\.0$/, '');
}

/** 이전 기간 대비 차이. 부호를 붙인다. */
export function formatMetricDelta(metric: HealthMetric, delta: number): string {
	const sign = delta > 0 ? '+' : delta < 0 ? '-' : '';
	return `${sign}${formatMetricValue(metric, Math.abs(delta))}`;
}

/** `2026-10-07` → `10.07` */
export function formatShortDate(date: string): string {
	const [, month, day] = date.split('-');
	return `${month}.${day}`;
}

const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'] as const;

/**
 * `2026-10-23` → `10.23(금)`. 근거 날짜는 KST 달력 날짜라 UTC 자정으로 읽어 요일만 구한다(건강 화면과 같은 방식).
 * "지지난주 금요일"처럼 요일로 묻는 답변을 근거에서 바로 맞춰 볼 수 있게 한다(S15P31A403-262)
 */
export function formatDayWithWeekday(date: string): string {
	return `${formatShortDate(date)}(${WEEKDAYS[new Date(`${date}T00:00:00Z`).getUTCDay()]})`;
}

/** `2026-10-01`, `2026-10-07` → `2026.10.01 ~ 10.07`. 해가 다르면 끝 날짜에도 연도를 붙인다. */
export function formatPeriod(start: string, end: string): string {
	const startYear = start.slice(0, 4);
	const endText = end.slice(0, 4) === startYear ? formatShortDate(end) : end.replaceAll('-', '.');
	return `${start.replaceAll('-', '.')} ~ ${endText}`;
}

function kstParts(date: Date) {
	const parts = new Intl.DateTimeFormat('ko-KR', {
		timeZone: KST,
		year: 'numeric',
		month: '2-digit',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit',
		hourCycle: 'h23'
	}).formatToParts(date);
	const get = (type: Intl.DateTimeFormatPartTypes) =>
		parts.find((part) => part.type === type)?.value ?? '';

	return {
		date: `${get('year')}-${get('month')}-${get('day')}`,
		monthDay: `${get('month')}.${get('day')}`,
		time: `${get('hour')}:${get('minute')}`
	};
}

/** 대화 목록 시각. 1분 이내는 "방금 전", 오늘·어제는 시각, 그 이전은 `MM.DD` (모두 KST). */
export function formatConversationTime(iso: string, now: Date = new Date()): string {
	const target = new Date(iso);
	if (now.getTime() - target.getTime() < 60_000) return '방금 전';

	const targetParts = kstParts(target);
	if (targetParts.date === kstParts(now).date) return `오늘 ${targetParts.time}`;

	const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
	if (targetParts.date === kstParts(yesterday).date) return `어제 ${targetParts.time}`;

	return targetParts.monthDay;
}

/** 수집 중 값의 기준 시각. `2026-10-29T05:30:00Z` → `14:30` (KST) */
export function formatClockTime(iso: string): string {
	return kstParts(new Date(iso)).time;
}

/**
 * 잠든 시각(start_at)으로 붙이는 수면 라벨(경민 님 계산 규칙 11.1, S15P31A403-312).
 * KST 18~24시 → 그날 밤, 0~6시 → 전날 밤, 6~10시 → 그날 아침, 10~18시 → 그날 낮. "11월 4일 밤"처럼 절대 날짜로 쓴다.
 * 조회 날짜(깬 날)와 다를 수 있다. 시각은 Z·+09:00 등 오프셋이 붙은 RFC 3339 를 모두 읽는다
 */
export function formatSleepLabel(startAt: string): string {
	const { day, hour } = sleepDay(startAt);
	const period = hour < 6 || hour >= 18 ? '밤' : hour < 10 ? '아침' : '낮';
	return `${day.getUTCMonth() + 1}월 ${day.getUTCDate()}일 ${period}`;
}

function sleepDay(startAt: string): { day: Date; hour: number } {
	const { date, time } = kstParts(new Date(startAt));
	const hour = Number(time.slice(0, 2));
	const day = new Date(`${date}T00:00:00Z`);
	if (hour < 6) day.setUTCDate(day.getUTCDate() - 1);
	return { day, hour };
}
