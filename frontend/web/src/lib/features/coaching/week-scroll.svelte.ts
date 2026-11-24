import { untrack } from 'svelte';

/** 한 화면에 보이는 날 수 */
export const VISIBLE_DAYS = 7;

export type RangeView = 'week' | 'all';

/** 기간 전체 보기 버튼의 이름. 한 달쯤이면 "한 달", 그보다 짧으면 날 수로 적는다 */
export function wholeViewLabel(days: number): string {
	return days >= 28 ? '한 달' : `${days}일`;
}

/** 손을 뗀 뒤 미끄러지는 속도가 16ms 마다 줄어드는 비율 */
const GLIDE_FRICTION = 0.94;
/** 이 속도(px/ms)보다 느려지면 멈춘다 */
const GLIDE_STOP = 0.02;
/** 미끄러지기 시작하는 가장 빠른 속도(px/ms). 세게 튕겨도 기간 끝까지 날아가지 않게 한다 */
const GLIDE_MAX = 2;

/**
 * 일별 차트의 공통 규칙(S15P31A403-418): 처음에는 최근 일주일만 보이고, 기간이 일주일을 넘으면 가로로 넘겨 본다.
 * 한 달을 한 번에 그리면 읽을 것이 너무 많다는 팀 의견으로 정했다. 막대·꺾은선·수면 단계 차트가 같이 쓴다.
 * - 넘기는 방법: 날짜 버튼으로 일주일씩 건너뛰지 않고 사용자가 직접 자유롭게 민다. 마우스로 잡고 끌면 손을 뗀 뒤에도
 *   조금 미끄러지고, 터치·트랙패드는 브라우저 스크롤 그대로다. 차트 아래의 가는 막대(ChartScrollBar)가 지금 자리를 보여 주고,
 *   그 막대를 잡고 끌어도 넘어간다. 날짜 단위로 끊어 멈추지 않는다
 * - 보기 전환: "일주일"과 기간 전체. 전체 보기에서는 넘기지 않고 모든 날을 한 화면에 그린다
 * 차트는 넘어가는 칸(element)의 폭을 7로 나눈 값이 하루 폭이 되게 그려야 한다
 */
export class WeekScroll {
	view = $state<RangeView>('week');
	/** 지금 화면의 맨 왼쪽에 가장 가까운 날 */
	first = $state(0);
	/** 넘긴 정도(0 맨 앞 ~ 1 가장 최근). 아래 막대의 자리와, 폭이 바뀔 때 보던 곳을 지키는 데 쓴다 */
	progress = $state(1);
	/** 마우스로 잡고 끄는 중 */
	free = $state(false);
	/** 가로로 넘어가는 칸. bind:this 로 잇는다 */
	element = $state<HTMLElement>();

	#count: () => number;
	#onmove: (() => void) | undefined;
	#latest: (() => number) | undefined;
	#drag: { x: number; left: number; moved: boolean; at: number; speed: number } | null = null;
	#glide = 0;

	/**
	 * count 는 날 수, onmove 는 넘어가거나 폭이 바뀔 때(팝업 자리를 다시 잡을 때) 부른다.
	 * latest 는 값이 있는 마지막 날의 순서(없으면 -1)다. 처음 자리를 정하는 데 쓴다
	 */
	constructor(count: () => number, onmove?: () => void, latest?: () => number) {
		this.#count = count;
		this.#onmove = onmove;
		this.#latest = latest;
	}

	get count(): number {
		return this.#count();
	}
	/** 일주일을 넘는 기간인지. 넘으면 넘겨 보기와 보기 전환이 붙는다 */
	get available(): boolean {
		return this.count > VISIBLE_DAYS;
	}
	get scrolls(): boolean {
		return this.available && this.view === 'week';
	}
	get lastStart(): number {
		return Math.max(0, this.count - VISIBLE_DAYS);
	}
	get dragging(): boolean {
		return this.#drag?.moved ?? false;
	}
	/** 화면에 보이는 몫(아래 막대에서 손잡이의 폭) */
	get shownShare(): number {
		return this.count > 0 ? Math.min(1, VISIBLE_DAYS / this.count) : 1;
	}
	/**
	 * 휴대폰 폭에서 축 날짜를 적는 날인지(S15P31A403-420). 일곱 칸에 날짜를 모두 적으면 글자가 서로 붙어,
	 * 하루 걸러 적는다. 가장 최근 날부터 세어 넘겨도 같은 날에 날짜가 붙어 있다. 적히지 않은 날은 막대를 눌러 본다
	 */
	labelsOnNarrow(index: number): boolean {
		return (this.count - 1 - index) % 2 === 0;
	}
	/** 지금 화면에서 몇 번째 칸인지(가장자리 처리에 쓴다) */
	position(index: number): number {
		return this.scrolls ? index - this.first : index;
	}

	#max(): number {
		return this.element ? this.element.scrollWidth - this.element.clientWidth : 0;
	}

	/** 넘긴 정도(0~1)로 옮긴다. 아래 막대를 끌 때와 처음 자리를 잡을 때 쓴다 */
	seek(progress: number) {
		if (!this.element || !this.scrolls) return;
		cancelAnimationFrame(this.#glide);
		this.element.scrollLeft = Math.min(Math.max(progress, 0), 1) * this.#max();
	}

	/**
	 * $effect 안에서 부른다. 처음과 날 수·보기가 바뀔 때 가장 최근 일주일로 맞춘다.
	 * 다만 최근 며칠에 기록이 없으면 값이 있는 마지막 날이 오른쪽 끝에 오게 한다(S15P31A403-420).
	 * 일주일쯤 동기화가 안 된 채 한 달을 물으면 처음 화면이 빈 칸 일곱 개뿐이라, 평균은 있는데 차트는 빈 것처럼 보였다.
	 * 그 뒤의 빈 날은 오른쪽으로 넘기면 그대로 보인다
	 */
	reset() {
		const latest = this.#latest?.() ?? -1;
		const start =
			latest < 0
				? this.lastStart
				: Math.min(this.lastStart, Math.max(0, latest - (VISIBLE_DAYS - 1)));
		void this.view;
		const progress = this.lastStart > 0 ? start / this.lastStart : 1;
		this.first = start;
		this.progress = progress;
		untrack(() => this.seek(progress));
	}

	/**
	 * $effect 안에서 부르고 돌려준 함수를 그대로 돌려준다. 창 폭이 바뀌면 하루 폭도 바뀌므로 보던 자리를 다시 맞춘다.
	 * 닫힌 대화 상자 안에서 폭 0으로 만들어졌다가 처음 보일 때도 여기서 맞춰진다
	 */
	observe() {
		const element = this.element;
		if (!element || !this.scrolls) return;
		const observer = new ResizeObserver(() => {
			this.seek(this.progress);
			this.#onmove?.();
		});
		observer.observe(element);
		return () => {
			observer.disconnect();
			cancelAnimationFrame(this.#glide);
		};
	}

	onscroll = () => {
		const element = this.element;
		if (element && element.clientWidth > 0) {
			const max = this.#max();
			this.progress = max > 0 ? element.scrollLeft / max : 1;
			this.first = Math.min(
				Math.max(Math.round(element.scrollLeft / (element.clientWidth / VISIBLE_DAYS)), 0),
				this.lastStart
			);
		}
		this.#onmove?.();
	};

	// 마우스에는 가로 휠이 없는 경우가 많아, 차트를 잡고 끌어도 넘어가게 한다. 터치는 브라우저 스크롤 그대로다
	onpointerdown = (event: PointerEvent) => {
		cancelAnimationFrame(this.#glide);
		if (!this.scrolls || !this.element || event.pointerType !== 'mouse' || event.button !== 0)
			return;
		this.#drag = {
			x: event.clientX,
			left: this.element.scrollLeft,
			moved: false,
			at: event.timeStamp,
			speed: 0
		};
	};

	onpointermove = (event: PointerEvent) => {
		const drag = this.#drag;
		if (!drag || !this.element) return;
		const moved = event.clientX - drag.x;
		if (!drag.moved) {
			if (Math.abs(moved) < 4) return;
			drag.moved = true;
			this.free = true;
			this.element.setPointerCapture(event.pointerId);
		}
		const before = this.element.scrollLeft;
		this.element.scrollLeft = drag.left - moved;
		// 손을 뗄 때의 빠르기. 직전 움직임에 무게를 더 두어 끝에서 멈칫한 경우 미끄러지지 않게 한다
		const elapsed = Math.max(1, event.timeStamp - drag.at);
		drag.speed = drag.speed * 0.3 + ((this.element.scrollLeft - before) / elapsed) * 0.7;
		drag.at = event.timeStamp;
	};

	onpointerup = (event: PointerEvent) => {
		const drag = this.#drag;
		this.#drag = null;
		if (!drag?.moved) return;
		this.free = false;
		// 손을 떼기 전에 멈춰 있었으면 그 자리에 둔다
		if (event.timeStamp - drag.at > 80) return;
		if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		this.#slide(drag.speed);
	};

	/** 손을 뗀 뒤 끌던 빠르기로 조금 더 미끄러지다 멈춘다 */
	#slide(from: number) {
		let speed = Math.min(Math.max(from, -GLIDE_MAX), GLIDE_MAX);
		let last = performance.now();
		const step = (now: number) => {
			const element = this.element;
			if (!element || Math.abs(speed) < GLIDE_STOP) return;
			const elapsed = Math.min(now - last, 48);
			last = now;
			const before = element.scrollLeft;
			element.scrollLeft = before + speed * elapsed;
			// 끝에 닿으면 멈춘다
			if (element.scrollLeft === before) return;
			speed *= GLIDE_FRICTION ** (elapsed / 16);
			this.#glide = requestAnimationFrame(step);
		};
		this.#glide = requestAnimationFrame(step);
	}

	/** 넘어가는 칸에 붙이는 클래스. 브라우저 스크롤바는 숨기고 아래의 가는 막대로 자리를 보인다 */
	get scrollerClass(): string {
		return [
			'overflow-x-auto overscroll-x-contain [scrollbar-width:none] select-none [&::-webkit-scrollbar]:hidden',
			this.free ? 'cursor-grabbing' : 'cursor-grab'
		].join(' ');
	}
}
