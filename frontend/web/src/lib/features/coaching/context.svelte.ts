import { getContext, setContext } from 'svelte';
import type { CoachingSession } from './session.svelte';
import type { ConversationItem, ConversationPage } from './types';

/** 서버 목록 순서(최근 활동순, 같으면 id 큰 쪽 먼저)에서 a가 b보다 앞이면 음수 */
function compareRecent(a: ConversationItem, b: ConversationItem): number {
	const byTime = Date.parse(b.updatedAt) - Date.parse(a.updatedAt);
	if (byTime !== 0) return byTime;
	return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

/**
 * 명세 6.2 대화 목록. 첫 페이지를 읽고, 목록 끝에 닿으면 nextCursor로 이전 대화를 이어 붙인다.
 * 첫 페이지 읽기가 실패하면 목록 대신 안내를 보여 준다.
 */
export class ConversationListState {
	items = $state<ConversationItem[]>([]);
	status = $state<'loading' | 'loaded' | 'error'>('loading');
	/** 이전 대화 이어 읽기 상태. 실패해도 이미 보이는 목록은 그대로 둔다 */
	moreStatus = $state<'idle' | 'loading' | 'error'>('idle');
	nextCursor = $state<string | null>(null);
	hasMore = $derived(this.status === 'loaded' && this.nextCursor !== null);

	#load: (cursor?: string) => Promise<ConversationPage>;
	/** 여러 번 읽을 때 늦게 도착한 이전 응답이 최신 목록을 덮지 않게 한다 */
	#latest = 0;

	constructor(load: (cursor?: string) => Promise<ConversationPage>) {
		this.#load = load;
	}

	/**
	 * 첫 페이지를 다시 읽는다. 질문할 때마다 불리므로 이미 이어 붙인 이전 대화는 버리지 않는다.
	 * 커서는 (최근 활동 시각, id) 기준이라, 첫 페이지보다 오래된 대화는 그대로 두고 그 끝의 커서를 계속 쓴다.
	 * 첫 페이지에 새로 올라온 대화(방금 질문한 대화)는 아래쪽 옛 자리에서 지운다.
	 */
	async load(): Promise<void> {
		const order = ++this.#latest;
		try {
			const page = await this.#load();
			if (order !== this.#latest) return;
			const last = page.items.at(-1);
			const fresh = page.items.map((item) => item.id);
			const older =
				page.nextCursor === null || !last
					? []
					: this.items.filter((item) => !fresh.includes(item.id) && compareRecent(last, item) < 0);
			this.items = [...page.items, ...older];
			if (older.length === 0) this.nextCursor = page.nextCursor;
			this.status = 'loaded';
		} catch {
			if (order !== this.#latest) return;
			this.status = 'error';
		}
	}

	/** 목록 끝에서 이전 대화를 한 페이지 더 읽는다. 읽는 중이면 다시 부르지 않는다 */
	async loadMore(): Promise<void> {
		const cursor = this.nextCursor;
		if (cursor === null || this.moreStatus === 'loading') return;
		this.moreStatus = 'loading';
		try {
			const page = await this.#load(cursor);
			// 읽는 사이 첫 페이지를 다시 읽었어도 커서가 같을 때만 이어 붙인다(다르면 이미 새 기준으로 바뀌었다)
			if (this.nextCursor === cursor) {
				const known = this.items.map((item) => item.id);
				this.items = [...this.items, ...page.items.filter((item) => !known.includes(item.id))].sort(
					compareRecent
				);
				this.nextCursor = page.nextCursor;
			}
			this.moreStatus = 'idle';
		} catch {
			this.moreStatus = 'error';
		}
	}
}

/** 코칭 화면들이 함께 쓰는 연결과 대화 목록. /coaching ↔ /coaching/[id] 이동 중에도 유지된다. */
export type CoachingContext = {
	session: CoachingSession;
	conversations: ConversationListState;
};

const KEY = Symbol('coaching');

export function setCoachingContext(context: CoachingContext): void {
	setContext(KEY, context);
}

export function getCoachingContext(): CoachingContext {
	const context = getContext<CoachingContext | undefined>(KEY);
	if (!context) throw new Error('코칭 layout 안에서만 쓸 수 있습니다.');
	return context;
}
