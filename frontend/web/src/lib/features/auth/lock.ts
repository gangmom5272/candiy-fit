const AUTH_LOCK_NAME = 'candiy-fit-auth';

let localQueue: Promise<unknown> = Promise.resolve();

function getLockManager(): LockManager | undefined {
	return typeof navigator === 'undefined' ? undefined : navigator.locks;
}

/**
 * 명세 1.5: 같은 브라우저의 모든 탭을 통틀어 login·signup·refresh·logout을 하나씩 보낸다.
 * Web Locks가 없는 환경(미지원 브라우저, secure context가 아닌 http 접속, 테스트)에서는
 * 현재 탭 안에서만 순서를 지키며 탭 간 직렬화는 보장하지 않는다. 지원 범위는 docs/api-integration.md 참조.
 */
export function withAuthLock<T>(
	task: () => Promise<T>,
	locks: LockManager | undefined = getLockManager()
): Promise<T> {
	if (locks) return locks.request(AUTH_LOCK_NAME, task);

	const run = localQueue.then(task, task);
	localQueue = run.catch(() => undefined);
	return run;
}
