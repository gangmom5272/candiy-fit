import { describe, expect, it } from 'vitest';
import { ConversationListState } from './context.svelte';
import type { ConversationItem, ConversationPage } from './types';

const item = (id: string, updatedAt = '2026-10-27T00:00:00Z'): ConversationItem => ({
	id,
	title: id,
	summary: null,
	createdAt: '2026-10-27T00:00:00Z',
	updatedAt,
	lastRequestStatus: 'completed'
});

/** n시(UTC) 활동한 대화. 숫자가 클수록 최근이다 */
const at = (hour: number) =>
	item(`대화${hour}`, `2026-10-27T${String(hour).padStart(2, '0')}:00:00Z`);
const ids = (list: ConversationListState) => list.items.map((entry) => entry.id);

describe('ConversationListState', () => {
	it('여러 번 읽으면 마지막 요청의 결과만 반영한다', async () => {
		const pending: Array<(page: ConversationPage) => void> = [];
		const list = new ConversationListState(
			() => new Promise<ConversationPage>((resolve) => pending.push(resolve))
		);

		const first = list.load();
		const second = list.load();
		pending[1]({ items: [item('최신')], nextCursor: null });
		await second;
		pending[0]({ items: [item('늦게 온 이전 응답')], nextCursor: null });
		await first;

		expect(list.items.map((entry) => entry.id)).toEqual(['최신']);
		expect(list.status).toBe('loaded');
	});

	it('실패하면 error로 표시한다', async () => {
		const list = new ConversationListState(async () => {
			throw new Error('network');
		});
		await list.load();
		expect(list.status).toBe('error');
	});

	it('목록 끝에서 nextCursor로 이전 대화를 이어 붙이고, 마지막 페이지면 더 읽지 않는다', async () => {
		const cursors: Array<string | undefined> = [];
		const pages: Record<string, ConversationPage> = {
			first: { items: [at(9), at(8)], nextCursor: 'c8' },
			c8: { items: [at(7), at(6)], nextCursor: 'c6' },
			c6: { items: [at(5)], nextCursor: null }
		};
		const list = new ConversationListState(async (cursor) => {
			cursors.push(cursor);
			return pages[cursor ?? 'first'];
		});

		await list.load();
		expect(list.hasMore).toBe(true);
		await list.loadMore();
		await list.loadMore();
		expect(ids(list)).toEqual(['대화9', '대화8', '대화7', '대화6', '대화5']);
		expect(list.hasMore).toBe(false);

		await list.loadMore();
		expect(cursors).toEqual([undefined, 'c8', 'c6']);
	});

	it('읽는 중에는 이어 읽기를 다시 보내지 않는다', async () => {
		let calls = 0;
		const list = new ConversationListState(async (cursor) => {
			calls += 1;
			return cursor ? { items: [at(7)], nextCursor: null } : { items: [at(9)], nextCursor: 'c9' };
		});
		await list.load();

		await Promise.all([list.loadMore(), list.loadMore()]);
		expect(calls).toBe(2);
		expect(ids(list)).toEqual(['대화9', '대화7']);
	});

	it('이어 읽기가 실패하면 보이던 목록은 두고 error로 표시하며, 다시 부르면 같은 커서로 읽는다', async () => {
		let fail = true;
		const cursors: Array<string | undefined> = [];
		const list = new ConversationListState(async (cursor) => {
			cursors.push(cursor);
			if (!cursor) return { items: [at(9)], nextCursor: 'c9' };
			if (fail) throw new Error('network');
			return { items: [at(7)], nextCursor: null };
		});
		await list.load();

		await list.loadMore();
		expect(list.moreStatus).toBe('error');
		expect(ids(list)).toEqual(['대화9']);
		expect(list.status).toBe('loaded');

		fail = false;
		await list.loadMore();
		expect(list.moreStatus).toBe('idle');
		expect(ids(list)).toEqual(['대화9', '대화7']);
		expect(cursors).toEqual([undefined, 'c9', 'c9']);
	});

	it('질문 뒤 첫 페이지를 다시 읽어도 이어 붙인 이전 대화와 그 끝의 커서는 그대로 둔다', async () => {
		let firstPage: ConversationPage = { items: [at(9), at(8)], nextCursor: 'c8' };
		const list = new ConversationListState(async (cursor) => {
			if (!cursor) return firstPage;
			return cursor === 'c8'
				? { items: [at(7), at(6)], nextCursor: 'c6' }
				: { items: [at(5)], nextCursor: null };
		});
		await list.load();
		await list.loadMore();

		// 대화6에 다시 질문해 맨 위로 올라왔다(활동 시각 10시). 대화8은 첫 페이지 밖으로 밀렸다
		const moved = item('대화6', '2026-10-27T10:00:00Z');
		firstPage = { items: [moved, at(9)], nextCursor: 'c9' };
		await list.load();

		expect(ids(list)).toEqual(['대화6', '대화9', '대화8', '대화7']);
		expect(list.nextCursor).toBe('c6');

		await list.loadMore();
		expect(ids(list)).toEqual(['대화6', '대화9', '대화8', '대화7', '대화5']);
	});

	it('첫 페이지만 읽은 상태에서 다시 읽으면 새 첫 페이지와 그 커서로 바꾼다', async () => {
		let firstPage: ConversationPage = { items: [at(9), at(8)], nextCursor: 'c8' };
		const list = new ConversationListState(async () => firstPage);
		await list.load();

		firstPage = { items: [at(10), at(9)], nextCursor: 'c9' };
		await list.load();

		// 첫 페이지에서 밀려난 대화8은 커서(c8) 기준으로는 아직 남은 대화라 끝에 두고 커서도 유지한다
		expect(ids(list)).toEqual(['대화10', '대화9', '대화8']);
		expect(list.nextCursor).toBe('c8');
	});

	it('다시 읽은 첫 페이지가 마지막 페이지면 이어 붙였던 대화는 지운다(서버에 더 없다)', async () => {
		let firstPage: ConversationPage = { items: [at(9)], nextCursor: 'c9' };
		const list = new ConversationListState(async (cursor) =>
			cursor ? { items: [at(7)], nextCursor: null } : firstPage
		);
		await list.load();
		await list.loadMore();

		firstPage = { items: [at(9), at(7)], nextCursor: null };
		await list.load();
		expect(ids(list)).toEqual(['대화9', '대화7']);
		expect(list.hasMore).toBe(false);
	});
});
