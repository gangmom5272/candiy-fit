import { expect, it, vi } from 'vitest';
import { createApiClient, type ApiClient } from '#lib/api/client.js';
import { ApiError, ResponseFormatError } from '#lib/api/errors.js';
import { getAppConnection, type AppConnection, type FirstRecord } from './app-connection-api';

const connectedAt = '2026-11-11T07:00:00Z';
const unconfirmed: FirstRecord = { status: 'UNCONFIRMED', firstArrivedAt: null };

function dataFor(appConnection: unknown, firstRecord: unknown = unconfirmed) {
	return { appConnection, firstRecord };
}

function clientFor(data: unknown): ApiClient {
	return createApiClient({ fetch: async () => Response.json({ data }) });
}

it.each<AppConnection>([
	{ status: 'NOT_CONNECTED', connectedAt: null },
	{ status: 'CONNECTED', connectedAt },
	{ status: 'UNKNOWN', connectedAt: null }
])('서버의 $status 상태와 최초 보고 시각을 그대로 보존한다', async (connection) => {
	await expect(getAppConnection(undefined, clientFor(dataFor(connection)))).resolves.toEqual({
		...connection,
		firstRecord: unconfirmed
	});
});

it('신호·no-store·계정 검증 전 인증 부작용 차단 옵션을 전달한다', async () => {
	const connection = { status: 'CONNECTED', connectedAt };
	const request = vi.fn<ApiClient['request']>().mockResolvedValue(dataFor(connection));
	const client: ApiClient = { request, setAuthHandler: vi.fn() };
	const signal = new AbortController().signal;
	await expect(getAppConnection(signal, client)).resolves.toEqual({
		...connection,
		firstRecord: unconfirmed
	});
	expect(request).toHaveBeenCalledExactlyOnceWith('/health/data-status', {
		signal,
		cache: 'no-store',
		auth: false
	});
});

it.each([
	'2000-01-01T00:00:00Z',
	'2099-12-31T23:59:59Z',
	'2024-02-29T07:00:00Z',
	'2026-11-11T07:00:00.123456Z',
	'2026-11-11T07:00:00+00:00'
])('유효한 UTC 보고 시각은 신선도 판정 없이 원문을 보존한다: %s', async (value) => {
	const connection = { status: 'CONNECTED', connectedAt: value };
	await expect(getAppConnection(undefined, clientFor(dataFor(connection)))).resolves.toEqual({
		...connection,
		firstRecord: unconfirmed
	});
});

it.each([null, false])(
	'기록 미도착과 지표 준비 여부(%s)를 CONNECTED의 반대 근거로 쓰지 않는다',
	async (ready) => {
		const connection = { status: 'CONNECTED', connectedAt };
		await expect(
			getAppConnection(
				undefined,
				clientFor({
					appConnection: connection,
					firstRecord: { status: 'UNCONFIRMED', firstArrivedAt: null },
					metrics: [{ metric: 'steps', ready, lastSuccessAt: null }]
				})
			)
		).resolves.toEqual({ ...connection, firstRecord: unconfirmed });
	}
);

it('기록 존재 여부로 UNKNOWN을 CONNECTED나 NOT_CONNECTED로 바꾸지 않는다', async () => {
	const connection = { status: 'UNKNOWN', connectedAt: null };
	await expect(
		getAppConnection(
			undefined,
			clientFor({
				appConnection: connection,
				firstRecord: { status: 'CONFIRMED', firstArrivedAt: connectedAt },
				metrics: [{ metric: 'steps', ready: true }]
			})
		)
	).resolves.toEqual({
		...connection,
		firstRecord: { status: 'CONFIRMED', firstArrivedAt: connectedAt }
	});
});

it.each(['2000-01-01T00:00:00Z', '2026-11-11T07:01:00.123456Z', '2026-11-11T07:01:00+00:00'])(
	'확인된 첫 도착 UTC 시각은 앱 설정 시각과 별개로 원문을 보존한다: %s',
	async (value) => {
		const connection = { status: 'CONNECTED', connectedAt };
		const firstRecord = { status: 'CONFIRMED', firstArrivedAt: value };
		await expect(
			getAppConnection(undefined, clientFor(dataFor(connection, firstRecord)))
		).resolves.toEqual({ ...connection, firstRecord });
	}
);

it.each([undefined, null, [], {}, 'invalid', 0])(
	'필수 firstRecord 누락이나 형식 오류를 미확인 기록으로 보충하지 않는다: %j',
	async (firstRecord) => {
		await expect(
			getAppConnection(
				undefined,
				clientFor({ appConnection: { status: 'CONNECTED', connectedAt }, firstRecord })
			)
		).rejects.toThrow(ResponseFormatError);
	}
);

it.each([undefined, null, 'confirmed', 'PENDING', true, 1])(
	'firstRecord의 누락되거나 알려지지 않은 상태를 거부한다: %j',
	async (status) => {
		await expect(
			getAppConnection(
				undefined,
				clientFor(dataFor({ status: 'CONNECTED', connectedAt }, { status, firstArrivedAt: null }))
			)
		).rejects.toThrow(ResponseFormatError);
	}
);

it.each([
	{ status: 'CONFIRMED', firstArrivedAt: null },
	{ status: 'CONFIRMED', firstArrivedAt: undefined },
	{ status: 'UNCONFIRMED', firstArrivedAt: connectedAt },
	{ status: 'UNCONFIRMED', firstArrivedAt: undefined }
])('첫 기록 상태와 필수 도착 시각의 모순을 거부한다: %j', async (firstRecord) => {
	await expect(
		getAppConnection(
			undefined,
			clientFor(dataFor({ status: 'CONNECTED', connectedAt }, firstRecord))
		)
	).rejects.toThrow(ResponseFormatError);
});

it.each([
	0,
	true,
	{},
	'yesterday',
	'2026-11-11',
	'2026-11-11T07:00:00',
	'2026-11-11T16:00:00+09:00',
	'2026-02-30T07:00:00Z',
	'2026-11-11T24:00:00Z'
])('CONFIRMED의 실제 UTC firstArrivedAt 형식을 검증한다: %j', async (firstArrivedAt) => {
	await expect(
		getAppConnection(
			undefined,
			clientFor(
				dataFor({ status: 'CONNECTED', connectedAt }, { status: 'CONFIRMED', firstArrivedAt })
			)
		)
	).rejects.toThrow(ResponseFormatError);
});

it.each([undefined, null, [], 'invalid', 0, {}, { appConnection: null }, { appConnection: [] }])(
	'누락되거나 잘못된 appConnection 응답을 UNKNOWN으로 위장하지 않는다: %j',
	async (data) => {
		await expect(getAppConnection(undefined, clientFor(data))).rejects.toThrow(ResponseFormatError);
	}
);

it.each([undefined, null, 'connected', 'PENDING', true, 1])(
	'필수 상태 누락과 알 수 없는 상태를 거부한다: %j',
	async (status) => {
		await expect(
			getAppConnection(undefined, clientFor({ appConnection: { status, connectedAt: null } }))
		).rejects.toThrow(ResponseFormatError);
	}
);

it.each(['NOT_CONNECTED', 'CONNECTED', 'UNKNOWN'])(
	'%s의 필수 connectedAt 누락을 null로 보충하지 않는다',
	async (status) => {
		await expect(
			getAppConnection(undefined, clientFor({ appConnection: { status } }))
		).rejects.toThrow(ResponseFormatError);
	}
);

it.each(['NOT_CONNECTED', 'UNKNOWN'])('%s에는 connectedAt=null만 허용한다', async (status) => {
	await expect(
		getAppConnection(undefined, clientFor({ appConnection: { status, connectedAt } }))
	).rejects.toThrow(ResponseFormatError);
});

it.each([null, 0, true, {}, []])(
	'CONNECTED의 connectedAt은 문자열이어야 한다: %j',
	async (value) => {
		await expect(
			getAppConnection(
				undefined,
				clientFor({ appConnection: { status: 'CONNECTED', connectedAt: value } })
			)
		).rejects.toThrow(ResponseFormatError);
	}
);

it.each([
	'',
	'yesterday',
	'2026-11-11',
	'2026-11-11T07:00:00',
	'2026-11-11T16:00:00+09:00',
	'2026-11-11T07:00:00-00:00',
	'2026-02-29T07:00:00Z',
	'2026-02-30T07:00:00Z',
	'2026-13-01T07:00:00Z',
	'2026-11-11T24:00:00Z',
	'2026-11-11T07:60:00Z',
	'2026-11-11T07:00:60Z',
	'2026-11-11T07:00:00Z '
])('CONNECTED의 실제 UTC 날짜·시각 형식을 검증한다: %j', async (value) => {
	await expect(
		getAppConnection(
			undefined,
			clientFor({ appConnection: { status: 'CONNECTED', connectedAt: value } })
		)
	).rejects.toThrow(ResponseFormatError);
});

it.each([
	{ status: 401, code: 'AUTH_REQUIRED' },
	{ status: 401, code: 'AUTH_EXPIRED' },
	{ status: 503, code: 'HEALTH_DATA_UNAVAILABLE' },
	{ status: 409, code: 'CANDIY_USER_NOT_LINKED' },
	{ status: 500, code: 'INTERNAL_ERROR' }
])(
	'HTTP $status $code를 정상 상태로 바꾸거나 전역 인증을 지우지 않는다',
	async ({ status, code }) => {
		const fetch = vi.fn<typeof globalThis.fetch>(async () =>
			Response.json({ error: { code, message: '요청 실패' } }, { status })
		);
		const client = createApiClient({ fetch });
		const handleSessionExpired = vi.fn();
		client.setAuthHandler({ handleSessionExpired });
		const error = await getAppConnection(undefined, client).catch((value: unknown) => value);
		expect(error).toBeInstanceOf(ApiError);
		expect(error).toMatchObject({ status, code });
		expect(fetch).toHaveBeenCalledOnce();
		expect(handleSessionExpired).not.toHaveBeenCalled();
	}
);

it('JSON 오류 계약이 없는 HTTP 실패도 상태가 아니라 오류로 보존한다', async () => {
	const client = createApiClient({
		fetch: async () => new Response('<h1>Bad Gateway</h1>', { status: 502 })
	});
	await expect(getAppConnection(undefined, client)).rejects.toMatchObject({
		name: 'ApiError',
		status: 502,
		code: null
	});
});

it.each([new TypeError('Failed to fetch'), new DOMException('취소됨', 'AbortError')])(
	'네트워크·취소 오류를 그대로 전달한다: $name',
	async (failure) => {
		const client = createApiClient({
			fetch: async () => {
				throw failure;
			}
		});
		await expect(getAppConnection(new AbortController().signal, client)).rejects.toBe(failure);
	}
);

it('완료 결과를 캐시하지 않고 다음 조회의 연결·첫 기록 상태를 반환한다', async () => {
	const confirmed: FirstRecord = { status: 'CONFIRMED', firstArrivedAt: connectedAt };
	const request = vi
		.fn<ApiClient['request']>()
		.mockResolvedValueOnce(dataFor({ status: 'NOT_CONNECTED', connectedAt: null }))
		.mockResolvedValueOnce(dataFor({ status: 'CONNECTED', connectedAt }, confirmed));
	const client: ApiClient = { request, setAuthHandler: vi.fn() };
	await expect(getAppConnection(undefined, client)).resolves.toEqual({
		status: 'NOT_CONNECTED',
		connectedAt: null,
		firstRecord: unconfirmed
	});
	await expect(getAppConnection(undefined, client)).resolves.toEqual({
		status: 'CONNECTED',
		connectedAt,
		firstRecord: confirmed
	});
	expect(request).toHaveBeenCalledTimes(2);
});
