import { ResponseFormatError, isRecord, toApiError } from './errors';

const API_PREFIX = '/api/v1';

/**
 * 인증 상태는 features/auth가 갖고, client는 주입받은 함수로 세션 만료만 알린다.
 * client가 auth를 import하지 않으므로 순환 의존이 생기지 않는다.
 */
export type AuthHandler = {
	/** 계정·로그인 세션이 바뀌면 증가한다. 이전 요청의 인증 오류는 새 세션에 적용하지 않는다. */
	readonly sessionVersion?: number;
	/** 일반 API가 401 `AUTH_EXPIRED`(웹 세션 7일 고정 만료, 명세 1.4)로 거절됐을 때 부른다. 웹은 갱신이 없다. */
	handleSessionExpired(): void;
};

export type RequestOptions = {
	method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
	body?: unknown;
	/**
	 * 401 `AUTH_EXPIRED`를 세션 만료로 처리해 AuthHandler에 알린다.
	 * 인증 API(login·signup·session·logout)는 결과를 직접 분류하므로 false로 호출한다.
	 */
	auth?: boolean;
	signal?: AbortSignal;
	cache?: RequestCache;
};

export type ApiClient = {
	/** 성공 응답의 `data`를 그대로 돌려준다. 204는 undefined다. 형태 검증은 feature API에서 한다. */
	request(path: string, options?: RequestOptions): Promise<unknown>;
	setAuthHandler(handler: AuthHandler | null): void;
};

type ApiClientOptions = {
	/** API server origin. 같은 origin이면 빈 문자열이다. */
	baseUrl?: string;
	fetch?: typeof fetch;
};

async function readJson(response: Response): Promise<unknown> {
	const text = await response.text();
	if (text === '') return undefined;
	try {
		return JSON.parse(text);
	} catch {
		return undefined;
	}
}

async function readData(response: Response): Promise<unknown> {
	if (response.status === 204) return undefined;

	const body = await readJson(response);
	if (!response.ok) throw toApiError(response, body);
	if (!isRecord(body) || !('data' in body)) throw new ResponseFormatError('data');
	return body.data;
}

export function createApiClient({
	baseUrl = '',
	fetch: fetchFn
}: ApiClientOptions = {}): ApiClient {
	let authHandler: AuthHandler | null = null;

	function send(path: string, options: RequestOptions): Promise<Response> {
		const headers = new Headers();
		if (options.body !== undefined) headers.set('Content-Type', 'application/json');

		// 웹 인증은 HttpOnly 세션 쿠키다(명세 1.4·1.5). 쿠키와 Authorization을 함께 보내면 서버가 401로 거절하므로 header를 붙이지 않는다.
		return (fetchFn ?? fetch)(`${baseUrl}${API_PREFIX}${path}`, {
			method: options.method ?? 'GET',
			headers,
			body: options.body === undefined ? undefined : JSON.stringify(options.body),
			credentials: 'include',
			...(options.cache ? { cache: options.cache } : {}),
			signal: options.signal
		});
	}

	return {
		async request(path, options = {}) {
			const requestAuth = authHandler;
			const sessionVersion = requestAuth?.sessionVersion;
			const response = await send(path, options);

			if (response.status === 401 && options.auth !== false && authHandler) {
				const error = toApiError(response, await readJson(response));
				// 401의 다른 코드(AUTH_REQUIRED 등)는 화면이 처리하도록 그대로 던진다
				if (
					error.code === 'AUTH_EXPIRED' &&
					!options.signal?.aborted &&
					authHandler === requestAuth &&
					authHandler.sessionVersion === sessionVersion
				)
					authHandler.handleSessionExpired();
				throw error;
			}

			return readData(response);
		},

		setAuthHandler(handler) {
			authHandler = handler;
		}
	};
}

export const api = createApiClient();
