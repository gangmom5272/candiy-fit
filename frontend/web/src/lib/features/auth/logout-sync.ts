const LOGOUT_KEY = 'candiy-fit:logout-version';
/**
 * 마지막 로그인·가입의 식별자(S15P31A403-426). 세션 쿠키는 브라우저의 모든 탭이 함께 쓰는데 회원 정보는 탭마다 메모리에 있다.
 * 한 탭이 로그아웃 없이 다른 계정으로 로그인하면 나머지 탭은 옛 회원을 보여 주면서 새 회원의 쿠키로 요청하게 된다
 * (프로필을 저장하면 다른 계정이 바뀐다). 로그아웃처럼 로그인도 알려 나머지 탭이 세션을 다시 확인하게 한다
 */
const LOGIN_KEY = 'candiy-fit:login-version';

function read(key: string): string {
	try {
		return typeof window === 'undefined' ? '' : (window.localStorage.getItem(key) ?? '');
	} catch {
		return '';
	}
}

function publish(key: string): void {
	try {
		window.localStorage.setItem(key, crypto.randomUUID());
	} catch {
		// shortcut: 저장소가 차단된 브라우저는 탭 간 전파를 보장하지 않는다. 지원 범위 확장 시 대체 수단을 추가한다.
	}
}

/** 토큰·회원 정보 없이 마지막 로그아웃의 식별자만 공유한다. */
export function getLogoutVersion(): string {
	return read(LOGOUT_KEY);
}

export function publishLogout(): void {
	publish(LOGOUT_KEY);
}

/** 토큰·회원 정보 없이 마지막 로그인·가입의 식별자만 공유한다. */
export function getLoginVersion(): string {
	return read(LOGIN_KEY);
}

export function publishLogin(): void {
	publish(LOGIN_KEY);
}

/** 다른 탭의 로그아웃·로그인을 듣는다. 탭이 다시 보일 때도 불러, 이벤트를 놓친 탭이 최신 공유 상태를 확인하게 한다. */
export function listenForSessionChange(onChange: () => void): () => void {
	const onStorage = (event: StorageEvent) => {
		if (event.key === LOGOUT_KEY || event.key === LOGIN_KEY || event.key === null) onChange();
	};
	const onVisible = () => {
		if (document.visibilityState === 'visible') onChange();
	};
	window.addEventListener('storage', onStorage);
	window.addEventListener('pageshow', onChange);
	document.addEventListener('visibilitychange', onVisible);
	return () => {
		window.removeEventListener('storage', onStorage);
		window.removeEventListener('pageshow', onChange);
		document.removeEventListener('visibilitychange', onVisible);
	};
}
