export type ApiErrorDetail = {
	field: string;
	reason: string;
};

type ApiErrorInit = {
	status: number;
	code: string | null;
	message: string;
	details: ApiErrorDetail[];
	retryable: boolean | null;
	context: unknown;
	requestId: string | null;
};

/** 명세 1.2의 오류 응답. `details`는 422, `retryable`은 refresh 503에서만 값이 있다. */
export class ApiError extends Error {
	readonly status: number;
	readonly code: string | null;
	readonly details: ApiErrorDetail[];
	readonly retryable: boolean | null;
	readonly context: unknown;
	readonly requestId: string | null;

	constructor(init: ApiErrorInit) {
		super(init.message);
		this.name = 'ApiError';
		this.status = init.status;
		this.code = init.code;
		this.details = init.details;
		this.retryable = init.retryable;
		this.context = init.context;
		this.requestId = init.requestId;
	}
}

/** 성공 응답이 명세의 형태와 다를 때 사용한다. */
export class ResponseFormatError extends Error {
	constructor(field: string) {
		super(`응답 형식이 명세와 다릅니다: ${field}`);
		this.name = 'ResponseFormatError';
	}
}

export function isApiError(error: unknown, code?: string): error is ApiError {
	return error instanceof ApiError && (code === undefined || error.code === code);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readDetails(value: unknown): ApiErrorDetail[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((item) =>
		isRecord(item) && typeof item.field === 'string' && typeof item.reason === 'string'
			? [{ field: item.field, reason: item.reason }]
			: []
	);
}

/** 오류 응답 body를 읽는다. 명세 형식이 아니면(proxy 오류 page 등) code는 null이다. */
export function toApiError(response: Response, body: unknown): ApiError {
	const error = isRecord(body) && isRecord(body.error) ? body.error : {};

	return new ApiError({
		status: response.status,
		code: typeof error.code === 'string' ? error.code : null,
		message:
			typeof error.message === 'string'
				? error.message
				: `요청에 실패했습니다. (${response.status})`,
		details: readDetails(error.details),
		retryable: typeof error.retryable === 'boolean' ? error.retryable : null,
		context: error.context,
		requestId: response.headers.get('X-Request-Id')
	});
}
