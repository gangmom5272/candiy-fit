import { describe, expect, it } from 'vitest';
import { ResponseFormatError } from '#lib/api/errors.js';
import { evidenceCases, kindEvidenceJson, sleepSpo2Month } from './fixtures';
import { parseEvidence, parseEvidenceList, parseMessagePage, parseServerMessage } from './parse';

// 합성 근거를 서버 응답처럼 JSON으로 한 번 직렬화해 파서를 통과시킨다
const asJson = (value: unknown) => JSON.parse(JSON.stringify(value));

describe('parseEvidence detail', () => {
	it('날짜별 수면과 운동 기록을 읽는다', () => {
		for (const item of [
			...evidenceCases.sleepStagesWeek,
			...evidenceCases.exerciseSessionsMonth,
			...evidenceCases.heartOneDay
		]) {
			expect(parseEvidence(asJson(item))).toEqual(item);
		}
	});

	it('운동 기록은 화면에 쓰지 않는 키(거리·칼로리 등)를 읽지 않고 버린다', () => {
		const [item] = asJson(evidenceCases.exerciseSessionsMonth);
		item.snapshot.detail.sessions[0] = {
			...item.snapshot.detail.sessions[0],
			end_at: '2026-09-29T10:40:00Z',
			exercise_type: 70,
			category: 'strength',
			calories_kcal: 210.5,
			distance_m: null,
			avg_heart_rate: 118,
			max_heart_rate: 151,
			avg_speed_mps: null
		};
		expect(parseEvidence(item).snapshot.detail.sessions[0]).toEqual(
			evidenceCases.exerciseSessionsMonth[0].snapshot.detail.sessions[0]
		);
	});

	it('쓰는 키의 형식이 다르면 형식 오류로 본다', () => {
		const [item] = asJson(evidenceCases.sleepStagesWeek);
		item.snapshot.detail.nights[0].deep_minutes = '78';
		expect(() => parseEvidence(item)).toThrow(ResponseFormatError);
	});
});

// 282: 근거 하나의 모양이 달라도 대화 전체·완료 이벤트를 버리지 않고 그 근거만 뺀다
describe('근거 목록은 잘못된 근거만 뺀다', () => {
	const good = () => asJson(evidenceCases.sleepStagesWeek)[0];
	const bad = () => {
		const item = good();
		item.id = 'bad';
		item.snapshot.detail.nights[0].deep_minutes = '78';
		return item;
	};
	const message = (evidence: unknown[]) => ({
		id: 'a1',
		requestId: 'r1',
		role: 'assistant',
		sequenceNo: 2,
		content: '답변',
		requestStatus: 'completed',
		errorCode: null,
		createdAt: '2026-10-28T03:00:00Z',
		evidence,
		errorContext: null
	});

	it('지난 메시지: 대화는 그대로 읽고 잘못된 근거만 빠진다', () => {
		const page = parseMessagePage({
			conversation: { id: 'c1', title: '대화', summary: null, createdAt: 'x', updatedAt: 'x' },
			items: [message([good(), bad()])],
			nextBeforeSequenceNo: null
		});
		expect(page.items[0].evidence.map((item) => item.id)).toEqual([good().id]);
	});

	it('실시간 완료 이벤트: 이벤트를 버리지 않고 잘못된 근거만 빠진다', () => {
		const event = parseServerMessage({
			type: 'completed',
			eventSeq: 5,
			requestId: 'r1',
			clientRequestId: 'c1',
			conversationId: 'v1',
			data: {
				assistantMessage: {
					id: 'a1',
					role: 'assistant',
					sequenceNo: 2,
					content: '답변',
					createdAt: 'x'
				},
				evidence: [bad(), good()],
				finishedAt: '2026-10-28T03:00:05Z'
			}
		});
		expect(event.type).toBe('completed');
		if (event.type === 'completed') expect(event.data.evidence).toHaveLength(1);
	});

	it('목록 자리에 배열이 아니면 그대로 형식 오류다', () => {
		expect(() =>
			parseMessagePage({
				conversation: { id: 'c1', title: '대화', summary: null, createdAt: 'x', updatedAt: 'x' },
				items: [message('없음' as unknown as unknown[])],
				nextBeforeSequenceNo: null
			})
		).toThrow(ResponseFormatError);
	});
});

/** S15P31A403-311 1단계: 신체 지표 4종·새 단위·snapshot.kind. 백엔드보다 웹이 먼저 들어가야 메시지가 깨지지 않는다 */
describe('신체 지표와 새 근거 종류', () => {
	const message = (evidence: unknown[], errorContext: unknown = null) => ({
		id: 'a1',
		requestId: 'r1',
		role: 'assistant',
		sequenceNo: 2,
		content: '답변',
		requestStatus: 'completed',
		errorCode: errorContext ? 'HEALTH_DATA_UNAVAILABLE' : null,
		createdAt: '2026-11-06T03:00:00Z',
		evidence,
		errorContext
	});
	const page = (items: unknown[]) =>
		parseMessagePage({
			conversation: { id: 'c1', title: '대화', summary: null, createdAt: 'x', updatedAt: 'x' },
			items,
			nextBeforeSequenceNo: null
		});

	it('신체 4종 근거를 백엔드 형식(total·synced_until null, detail 빈 값) 그대로 읽는다', () => {
		const parsed = evidenceCases.bodyWeek.map((item) => parseEvidence(asJson(item)));
		expect(parsed.map((item) => [item.metric, item.snapshot.unit])).toEqual([
			['weights', 'kg'],
			['body_fats', '%'],
			['basal_metabolic_rates', 'kcal/day'],
			['heights', 'm']
		]);
		for (const item of parsed) {
			expect(item.snapshot.summary.total).toBeNull();
			expect(item.snapshot.data_status.synced_until).toBeNull();
			expect(item.snapshot.detail).toEqual({
				measured_min: null,
				measured_max: null,
				nights: [],
				daily_ranges: [],
				sessions: []
			});
		}
	});

	it('실패 안내 지표 목록에 신체 지표가 와도 메시지를 읽는다', () => {
		const result = page([
			message([], { reason: 'LOOKUP_FAILED', metrics: ['weights', 'heights'] })
		]);
		expect(result.items[0].errorContext).toEqual({
			reason: 'LOOKUP_FAILED',
			metrics: ['weights', 'heights']
		});
	});

	it('snapshot.kind 가 있는 근거는 모양과 관계없이 숨기고 나머지 근거·답변은 그대로 둔다', () => {
		const known = asJson(evidenceCases.bodyWeek)[0];
		const sessions = {
			id: 'k1',
			position: 2,
			metric: 'sleeps',
			periodStart: '2026-11-01',
			periodEnd: '2026-11-05',
			snapshot: { kind: 'sleep_sessions', sessions: [{ start_at: 'x', is_main: false }] }
		};
		const unknownKind = { ...sessions, id: 'k2', snapshot: { kind: 'future_kind' } };
		// 모르는 kind 가 아는 근거 모양을 갖췄어도 그리지 않는다
		const lookalike = { ...known, id: 'k3', snapshot: { ...known.snapshot, kind: 'hourly_steps' } };
		const result = page([message([sessions, known, unknownKind, lookalike])]);
		expect(result.items[0].content).toBe('답변');
		expect(result.items[0].evidence.map((item) => item.id)).toEqual([known.id]);
	});
});

/** S15P31A403-312 2단계: 백엔드 반환 예시(합성) 그대로 읽는다 */
describe('모양이 다른 근거(snapshot.kind)', () => {
	const parse = (...items: unknown[]) => parseEvidenceList({ items: asJson(items) }, 'items');

	it('수면 세션은 화면에 쓰는 키만 읽고 산소포화도 null 을 빈 배열로 바꾸지 않는다', () => {
		const [item] = parse(kindEvidenceJson.sleepSessions);
		expect(item.snapshot).toMatchObject({
			kind: 'sleep_sessions',
			period: { start: '2026-11-04', end: '2026-11-05' }
		});
		if (!('kind' in item.snapshot) || item.snapshot.kind !== 'sleep_sessions')
			throw new Error('수면 세션 아님');
		const [main, nap, countOnly] = item.snapshot.sessions;
		expect(main).toMatchObject({ is_main: true, spo2_low_count: 5 });
		expect(main.spo2_low_times).toHaveLength(5);
		expect(nap).toMatchObject({ is_main: false, spo2_low_count: null, spo2_low_times: null });
		expect(countOnly).toMatchObject({ spo2_low_count: 2, spo2_low_times: null });
		// 심박·단계 같은 나머지 키는 버린다
		expect(main).not.toHaveProperty('avg_heart_rate');
	});

	it('시간대 걸음은 기록 없는 날의 values·total null 을 그대로 둔다', () => {
		const [item] = parse(kindEvidenceJson.hourlySteps);
		if (!('kind' in item.snapshot) || item.snapshot.kind !== 'hourly_steps')
			throw new Error('시간대 걸음 아님');
		expect(item.metric).toBe('steps');
		expect(item.snapshot.days[0]).toEqual({
			date: '2026-11-03',
			is_in_progress: false,
			values: null,
			total: null,
			reason: 'NO_RECORD'
		});
		expect(item.snapshot.days[2].is_in_progress).toBe(true);
		// 기록 없는 날(11.03)이 있어 기간 합계는 null 로 온다(백엔드 규칙)
		expect(item.snapshot.total).toBeNull();
	});

	it('모양이 정해지지 않은 종류(운동 구간)와 필수 키가 빠진 종류 근거는 그 근거만 숨긴다', () => {
		const broken = {
			...kindEvidenceJson.hourlySteps,
			id: 'broken',
			snapshot: { ...kindEvidenceJson.hourlySteps.snapshot, start_hour: undefined }
		};
		const items = parse(kindEvidenceJson.exerciseIntervals, kindEvidenceJson.sleepSessions, broken);
		expect(items.map((item) => item.id)).toEqual([kindEvidenceJson.sleepSessions.id]);
	});

	it('공통 수면 근거의 밤에 산소포화도 칸이 있으면 읽고, 없으면(예전 근거) 비워 둔다', () => {
		const old = asJson(evidenceCases.sleepStagesWeek)[0];
		expect(parseEvidence(old).snapshot.detail.nights[0]).not.toHaveProperty('spo2_low_count');
		old.snapshot.detail.nights[0].spo2_low_count = 0;
		old.snapshot.detail.nights[0].spo2_low_times = [];
		expect(parseEvidence(old).snapshot.detail.nights[0]).toMatchObject({
			spo2_low_count: 0,
			spo2_low_times: []
		});
	});
});

describe('모양이 다른 근거 QA(S15P31A403-312)', () => {
	const parse = (...items: unknown[]) => parseEvidenceList({ items: asJson(items) }, 'items');

	it.each([
		['시작 시각이 날짜만', { start_at: '2026-11-04' }],
		['시각 오프셋 없음', { end_at: '2026-11-05T06:40:00' }],
		['산소포화도 시각이 잘못됨', { spo2_low_times: ['새벽 2시'] }]
	])('%s이면 화면이 깨지지 않게 그 근거만 숨긴다', (_, patch) => {
		const json = asJson(kindEvidenceJson.sleepSessions);
		Object.assign(json.snapshot.sessions[0], patch);
		expect(parse(json, kindEvidenceJson.hourlySteps).map((item) => item.id)).toEqual([
			kindEvidenceJson.hourlySteps.id
		]);
	});

	it('공통 수면 근거의 밤에 산소포화도 칸이 있는데 시각이 잘못되면 그 근거만 숨긴다', () => {
		const old = asJson(evidenceCases.sleepStagesWeek)[0];
		old.snapshot.detail.nights[0].spo2_low_count = 1;
		old.snapshot.detail.nights[0].spo2_low_times = ['2026-10-20 02:00'];
		expect(parse(old)).toEqual([]);
	});

	it('세션이 없는 수면 기록·24시간 시간대 걸음도 읽는다', () => {
		const [empty, fullDay] = parse(
			kindEvidenceJson.sleepSessionsEmpty,
			kindEvidenceJson.hourlyStepsFullDay
		);
		expect(empty.snapshot).toMatchObject({ kind: 'sleep_sessions', sessions: [] });
		expect(fullDay.snapshot).toMatchObject({ kind: 'hourly_steps', start_hour: 0, end_hour: 24 });
	});
});

describe('연결 이벤트(명세 7.2)', () => {
	it.each(['AUTH_REQUIRED', 'AUTH_EXPIRED', 'AUTH_UNAVAILABLE'])(
		'connection_error %s를 읽는다',
		(code) => {
			expect(
				parseServerMessage({ type: 'connection_error', data: { code, message: '안내' } })
			).toEqual({ type: 'connection_error', data: { code, message: '안내' } });
		}
	);

	it('명세에 없는 connection_error 코드는 형식 오류로 본다', () => {
		expect(() =>
			parseServerMessage({ type: 'connection_error', data: { code: 'UNKNOWN', message: '안내' } })
		).toThrow(ResponseFormatError);
	});
});

describe('운동 근거의 횟수·칼로리·거리(S15P31A403-375)', () => {
	/** 서버가 보내는 모양: 화면에 쓰지 않는 키(운동 시간·집계 시각·원천 상세)가 함께 온다 */
	const serverJson = () => {
		const [item] = asJson(evidenceCases.exerciseActivityWeek);
		const activity = item.snapshot.detail.exercise_activity;
		for (const day of activity.days)
			Object.assign(day, { exercise_minutes: 30, computed_at: '2026-10-28T05:00:00Z' });
		activity.summary.exercise_minutes = { total: 135, avg: 27, days_with_data: 5 };
		for (const status of Object.values(activity.source_statuses) as Record<string, unknown>[])
			Object.assign(status, { ready: true, last_data_at: null, last_success_at: null });
		return item;
	};

	it('화면에 쓰는 키만 읽어 근거에 붙인다', () => {
		expect(parseEvidence(serverJson())).toEqual(evidenceCases.exerciseActivityWeek[0]);
	});

	it('키가 없는 예전 근거는 그대로 읽고 exercise_activity 키를 만들지 않는다', () => {
		const [item] = asJson(evidenceCases.exerciseSessionsMonth);
		expect('exercise_activity' in parseEvidence(item).snapshot.detail).toBe(false);
	});

	it('모양이 다르면 이 묶음만 버리고 운동 시간·세션 근거는 남긴다', () => {
		const item = serverJson();
		delete item.snapshot.detail.exercise_activity.summary.exercise_distance_m;
		const parsed = parseEvidence(item);

		expect(parsed.snapshot.detail.exercise_activity).toBeUndefined();
		expect(parsed.snapshot.summary.total).toBe(
			evidenceCases.exerciseActivityWeek[0].snapshot.summary.total
		);
	});
});

describe('날짜별 평균·최저·최고 산소포화도(S15P31A403-388)', () => {
	it('값이 실려 오면 읽고, 근거를 다시 읽어도 같다', () => {
		const [item] = sleepSpo2Month;
		expect(parseEvidence(asJson(item))).toEqual(item);
	});

	it('형식이 달라도 밤 기록은 남기고 그 값만 뺀다', () => {
		const item = asJson(sleepSpo2Month)[0];
		item.snapshot.detail.nights[0].spo2_avg = '높음';
		item.snapshot.detail.nights[0].spo2_min = 140;
		const [night] = parseEvidence(item).snapshot.detail.nights;

		expect(night).not.toHaveProperty('spo2_avg');
		expect(night).not.toHaveProperty('spo2_min');
		expect(night.spo2_max).toBe(98);
		expect(night.spo2_low_count).toBe(0);
	});
});
