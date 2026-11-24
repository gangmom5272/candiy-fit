import { expect, it, vi } from 'vitest';
import { createApiClient } from '#lib/api/client.js';
import { ApiError, ResponseFormatError } from '#lib/api/errors.js';
import { dailyMessageFixture } from '#lib/testing/daily-message-fixture.js';
import { dailyMessageError, getDailyMessage, messageTime } from './daily-message-api';

it.each(['READY', 'PARTIAL', 'EMPTY', 'WAITING'] as const)(
	'%s의 문구·area=null·독립 기준 시각과 notices를 그대로 보존한다',
	async (status) => {
		const data = dailyMessageFixture();
		data.status = status;
		if (status === 'EMPTY' || status === 'WAITING') {
			data.code = status === 'EMPTY' ? 'NO_RECORDS' : 'PENDING_RECORDS';
			data.scoreComputedAt = null;
			data.title =
				status === 'EMPTY'
					? '오늘은 기록 없이도 내 하루를 돌아볼까요?'
					: '오늘의 기록을 아직 확인하지 못했어요';
			data.description =
				status === 'EMPTY'
					? '지금 확인할 수 있는 활동·수면 기록은 없어요.'
					: '괜찮다면 앱의 동기화 상태를 확인해 주세요.';
		}
		if (status === 'PARTIAL') {
			data.code = 'ACTIVE_DAY';
			data.area = 'activity';
			data.title = '차곡차곡 쌓은 움직임, 뿌듯해해도 좋아요';
			data.description = '운동과 걸음 기록에 오늘의 움직임이 담겼어요.';
			data.scoreComputedAt = null;
		}
		data.notices =
			status === 'PARTIAL'
				? [
						{ metric: 'sleeps', code: 'LOOKUP_FAILED', message: '수면 기록은 불러오지 못했어요.' },
						{ metric: 'sleeps', code: 'SYNC_FAILED', message: '최근 동기화에 실패했어요.' }
					]
				: status === 'READY'
					? [{ metric: 'steps', code: 'SYNC_FAILED', message: '이전에 저장된 기록을 보여 드려요.' }]
					: [];
		if (status === 'PARTIAL') data.dataAsOf.sleeps = null;
		const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ data }));
		const signal = new AbortController().signal;
		const client = createApiClient({ fetch });
		client.setAuthHandler({ handleSessionExpired: () => {} });
		expect(await getDailyMessage(signal, client)).toEqual(data);
		const [url, options] = fetch.mock.calls[0];
		expect(url).toBe('/api/v1/health/daily-message');
		expect(options?.method).toBe('GET');
		expect(options?.body).toBeUndefined();
		expect(options?.signal).toBe(signal);
		expect(options?.credentials).toBe('include');
		expect(new Headers(options?.headers).get('Authorization')).toBeNull();
	}
);

it.each([
	'date',
	'status',
	'code',
	'area',
	'title',
	'description',
	'dataAsOf',
	'scoreComputedAt',
	'notices'
])('필수 %s 누락을 정상 데이터로 위장하지 않는다', async (field) => {
	const data: Record<string, unknown> = { ...dailyMessageFixture() };
	delete data[field];
	await expect(
		getDailyMessage(undefined, createApiClient({ fetch: async () => Response.json({ data }) }))
	).rejects.toThrow(ResponseFormatError);
});

it.each([
	{ date: '2026-02-30' },
	{ area: 'heart' },
	{ status: 'FAIL' },
	{ title: null },
	{ dataAsOf: { steps: null, exercises: null } },
	{ scoreComputedAt: 'yesterday' },
	{ notices: [{ metric: 'heart_rates', code: 'SYNC_FAILED', message: '오류' }] }
])('잘못된 필드 형식은 거부한다: %j', async (change) => {
	await expect(
		getDailyMessage(
			undefined,
			createApiClient({
				fetch: async () => Response.json({ data: { ...dailyMessageFixture(), ...change } })
			})
		)
	).rejects.toThrow(ResponseFormatError);
});

it('RFC 3339 UTC의 +00:00 표기도 원본 그대로 보존한다', async () => {
	const data = dailyMessageFixture();
	data.dataAsOf.steps = '2026-10-26T06:00:00+00:00';
	data.scoreComputedAt = '2026-10-26T06:10:00+00:00';
	const client = createApiClient({ fetch: async () => Response.json({ data }) });
	expect(await getDailyMessage(undefined, client)).toEqual(data);
});

it('Date.parse가 다음 달로 보정하는 잘못된 기준 시각도 거부한다', async () => {
	const data = dailyMessageFixture();
	data.dataAsOf.sleeps = '2026-02-30T06:00:00Z';
	const client = createApiClient({ fetch: async () => Response.json({ data }) });
	await expect(getDailyMessage(undefined, client)).rejects.toThrow(ResponseFormatError);
});

it.each(['DATA_NOT_READY', 'LOOKUP_FAILED'])(
	'503 %s만 계약상 카드 재시도를 제공하며 내부 정보는 숨긴다',
	async (reason) => {
		const client = createApiClient({
			fetch: async () =>
				Response.json(
					{
						error: {
							code: 'HEALTH_DATA_UNAVAILABLE',
							message: 'private SQL request-id',
							context: { reason, metrics: [] }
						}
					},
					{ status: 503 }
				)
		});
		const error = await getDailyMessage(undefined, client).catch((error: unknown) => error);
		const view = dailyMessageError(error);
		expect(view.retryable).toBe(true);
		expect(view.preparing ?? false).toBe(reason === 'DATA_NOT_READY');
		expect(view.title).toBe(
			reason === 'DATA_NOT_READY'
				? '첫 기록을 보여 드릴 준비 중이에요'
				: '오늘의 한마디를 불러오지 못했어요'
		);
		expect(JSON.stringify(view)).not.toContain('private');
	}
);

it.each([401, 403, 409, 422, 500, 503])(
	'HTTP %i를 정상 결측이나 무조건 재시도 상태로 바꾸지 않는다',
	(status) => {
		const error = new ApiError({
			status,
			code: status === 409 ? 'CANDIY_USER_NOT_LINKED' : 'OTHER',
			message: 'private',
			details: [],
			retryable: null,
			context: null,
			requestId: 'private'
		});
		const view = dailyMessageError(error);
		expect(view.retryable).toBe(false);
		expect(JSON.stringify(view)).not.toContain('private');
	}
);

it('사용자 타임존과 무관하게 UTC 기준 시각을 KST로 표시한다', () => {
	expect(messageTime('2026-10-28T15:00:00Z')).toBe('10/29 00:00');
	expect(messageTime('2026-10-28T03:00:00Z')).toBe('10/28 12:00');
	expect(messageTime('2026-10-28T15:01:00Z')).toBe('10/29 00:01');
	expect(messageTime('2026-10-28T03:55:00Z')).toBe('10/28 12:55');
	expect(messageTime('2026-10-22T11:40:00Z')).toBe('10/22 20:40');
	expect(messageTime('2026-10-27T20:25:00Z')).toBe('10/28 05:25');
	expect(messageTime('2026-01-02T00:04:00Z')).toBe('01/02 09:04');
});
