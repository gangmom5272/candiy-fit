/** 맨 아래에서 이 거리 안이면 사용자가 최신 메시지를 보고 있는 것으로 본다 */
const NEAR_BOTTOM_PX = 80;
/** 휠·터치·키보드·스크롤바 조작 뒤 이 시간 안의 스크롤만 사용자가 움직인 것으로 본다 */
const USER_INPUT_MS = 1000;
/** scrollend가 없는 브라우저(Safari 등)에서 부드러운 이동이 끝났다고 보는 시간 */
const SMOOTH_FALLBACK_MS = 800;
const SCROLL_KEYS = new Set(['ArrowUp', 'PageUp', 'Home']);

export type FollowLatest = {
	/**
	 * 질문을 보냈거나 다른 대화를 열었을 때. 위로 올려 둔 상태여도 최신 메시지로 내려간다.
	 * smooth면 부드럽게 내려가고, 내려가는 동안 내용이 늘어나도 새 맨 아래까지 이어서 간다.
	 */
	pin(options?: { smooth?: boolean }): void;
	/**
	 * 따라가기를 푼다. 맨 위에서 더 이전 메시지를 불러올 때 쓴다. 위쪽에 내용이 붙어 높이가 늘어도
	 * 맨 아래로 끌려가지 않는다(키보드·코드로 맨 위에 와서 사용자 스크롤로 풀리지 않았을 때도)
	 */
	release(): void;
	destroy(): void;
};

/**
 * 대화 내용이 늘어날 때 최신 메시지가 보이도록 맨 아래로 따라간다.
 * - 글자 수가 아니라 실제 높이 변화(스트리밍 글자, 근거 카드, 오류 안내, 지난 메시지)를 본다.
 * - 데스크톱은 대화 영역(log)이 스스로 스크롤하고, 모바일은 페이지 전체가 스크롤한다.
 * - 사용자가 위로 올려 지난 내용을 읽는 중이면 끌어내리지 않는다.
 *
 * @param log 대화 영역. lg 이상에서만 overflow-y가 auto다
 * @param content log 안에서 메시지를 담는 요소. 높이가 바뀌면 따라간다
 */
export function followLatest(log: HTMLElement, content: HTMLElement): FollowLatest {
	const scrollsItself = () => getComputedStyle(log).overflowY !== 'visible';

	function isNearBottom() {
		if (scrollsItself()) {
			return log.scrollHeight - log.scrollTop - log.clientHeight <= NEAR_BOTTOM_PX;
		}
		const page = document.documentElement;
		return page.scrollHeight - window.scrollY - window.innerHeight <= NEAR_BOTTOM_PX;
	}

	function scrollToBottom(behavior: ScrollBehavior = 'auto') {
		if (scrollsItself()) log.scrollTo({ top: log.scrollHeight, behavior });
		else window.scrollTo({ top: document.documentElement.scrollHeight, behavior });
	}

	const currentTop = () => (scrollsItself() ? log.scrollTop : window.scrollY);
	const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

	let isPinned = true;
	let lastTop = currentTop();
	let lastUserInput = -Infinity;
	let isSmoothing = false;
	let smoothTimer: ReturnType<typeof setTimeout> | undefined;

	function stopSmoothing() {
		isSmoothing = false;
		clearTimeout(smoothTimer);
		lastTop = currentTop();
	}

	function smoothToBottom() {
		isSmoothing = true;
		clearTimeout(smoothTimer);
		smoothTimer = setTimeout(stopSmoothing, SMOOTH_FALLBACK_MS);
		scrollToBottom('smooth');
	}

	const markUserInput = () => {
		lastUserInput = performance.now();
		// 부드럽게 내려가는 중에 사용자가 직접 움직이면 그쪽을 따른다
		if (isSmoothing) stopSmoothing();
	};
	const handleKeydown = (event: KeyboardEvent) => {
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, [contenteditable="true"]')) return;
		if (SCROLL_KEYS.has(event.key) || (event.key === ' ' && event.shiftKey)) markUserInput();
	};
	/**
	 * 대화 영역 스크롤바를 누른 경우만 사용자 조작으로 본다. 스크롤바를 누르면 target이 log 자체다.
	 * 안쪽 버튼(다시 질문하기 등)을 누른 것은 스크롤이 아니다. 누른 뒤 버튼이 사라지며 내용이 줄어
	 * 위치가 올라가면 사용자가 올린 것으로 잘못 보고 따라가기를 풀게 된다.
	 */
	const handlePointerdown = (event: PointerEvent) => {
		if (event.target === log) markUserInput();
	};

	/**
	 * 사용자가 직접 위로 올렸을 때만 따라가기를 푼다.
	 * - 자동으로 내린 스크롤의 이벤트는 내용이 더 늘어난 뒤에 도착할 수 있어서,
	 *   그때 "맨 아래가 아니다"만 보고 풀면 근거 카드처럼 마지막에 붙는 내용을 놓친다.
	 * - 내용이 잠깐 줄거나 레이아웃이 바뀌어 위치가 올라간 것은 사용자가 올린 것이 아니다.
	 */
	const handleScroll = () => {
		const top = currentTop();
		const isUserScroll = performance.now() - lastUserInput < USER_INPUT_MS;
		if (top < lastTop - 1 && isUserScroll) isPinned = isNearBottom();
		else if (isNearBottom()) isPinned = true;
		lastTop = top;
	};
	const observer = new ResizeObserver(() => {
		if (!isPinned) return;
		// 부드럽게 내려가는 중이면 늘어난 맨 아래로 목표만 바꾼다
		scrollToBottom(isSmoothing ? 'smooth' : 'auto');
	});

	const inputEvents = ['wheel', 'touchmove'] as const;
	for (const type of inputEvents) {
		log.addEventListener(type, markUserInput, { passive: true });
		window.addEventListener(type, markUserInput, { passive: true });
	}
	log.addEventListener('pointerdown', handlePointerdown, { passive: true });
	window.addEventListener('keydown', handleKeydown);
	log.addEventListener('scroll', handleScroll, { passive: true });
	window.addEventListener('scroll', handleScroll, { passive: true });
	observer.observe(content);
	scrollToBottom();
	lastTop = currentTop();

	return {
		pin({ smooth = false } = {}) {
			isPinned = true;
			if (smooth && !prefersReducedMotion()) {
				smoothToBottom();
				return;
			}
			stopSmoothing();
			scrollToBottom();
			lastTop = currentTop();
		},
		release() {
			isPinned = false;
			stopSmoothing();
		},
		destroy() {
			clearTimeout(smoothTimer);
			observer.disconnect();
			for (const type of inputEvents) {
				log.removeEventListener(type, markUserInput);
				window.removeEventListener(type, markUserInput);
			}
			log.removeEventListener('pointerdown', handlePointerdown);
			window.removeEventListener('keydown', handleKeydown);
			log.removeEventListener('scroll', handleScroll);
			window.removeEventListener('scroll', handleScroll);
		}
	};
}
