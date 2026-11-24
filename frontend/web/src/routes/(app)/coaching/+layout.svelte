<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import { listConversations } from '#lib/features/coaching/api.js';
	import { createCoachingSession } from '#lib/features/coaching/connection.js';
	import {
		ConversationListState,
		setCoachingContext
	} from '#lib/features/coaching/context.svelte.js';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	// 첫 화면에서 질문하고 대화 화면으로 넘어가도 연결과 진행 중인 질문이 이어지도록 layout에 둔다.
	// 소켓은 첫 질문 때 연결한다. 코칭을 떠나면 닫지만 접수된 질문은 서버에서 계속 처리된다(명세 7.6).
	const session = createCoachingSession();
	// 처음 20개, 목록 끝까지 내리면 20개씩 더 읽는다(명세 6.2 limit 기본값 20, 최대 100)
	const conversations = new ConversationListState((cursor) =>
		listConversations({ limit: 20, cursor })
	);
	setCoachingContext({ session, conversations });
	conversations.load();

	// 질문 접수(대화 생성·순서 변경)와 완료·실패(상태 변경) 때 서버 목록이 바뀌므로 다시 읽는다(명세 6.2).
	// 접수되지 않은 질문은 서버에 남지 않아 제외한다.
	const listVersion = $derived(
		session.requests
			.filter((request) => request.requestId)
			.map((request) => `${request.requestId}:${request.isInProgress ? 'open' : request.phase}`)
			.join(',')
	);
	$effect(() => {
		if (listVersion) untrack(() => conversations.load());
	});

	onDestroy(() => session.dispose());
</script>

{@render children()}
