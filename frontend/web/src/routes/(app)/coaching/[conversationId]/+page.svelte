<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { getConversationMessages } from '#lib/features/coaching/api.js';
	import AssistantMessage from '#lib/features/coaching/components/AssistantMessage.svelte';
	import CoachingErrorNotice from '#lib/features/coaching/components/CoachingErrorNotice.svelte';
	import ConversationListToggle from '#lib/features/coaching/components/ConversationListToggle.svelte';
	import ConversationSidebar from '#lib/features/coaching/components/ConversationSidebar.svelte';
	import ConversationSkeleton from '#lib/features/coaching/components/ConversationSkeleton.svelte';
	import EvidenceDialog from '#lib/features/coaching/components/EvidenceDialog.svelte';
	import QuestionInput from '#lib/features/coaching/components/QuestionInput.svelte';
	import UserMessage from '#lib/features/coaching/components/UserMessage.svelte';
	import { getCoachingContext } from '#lib/features/coaching/context.svelte.js';
	import { copyWithoutDecoration } from '#lib/features/coaching/copy.js';
	import { followLatest, type FollowLatest } from '#lib/features/coaching/follow-latest.js';
	import { getCoachingErrorMessage } from '#lib/features/coaching/session.svelte.js';
	import type { Evidence, MessagePage } from '#lib/features/coaching/types.js';
	import {
		getErrorText,
		withErrorHint,
		getProgressText,
		toAssistantMessage
	} from '#lib/features/coaching/view.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const LIST_ID = 'coaching-conversation-list';
	const OLDER_BUTTON =
		'rounded-full border border-border bg-card px-4 py-2 text-sm font-semibold text-text-secondary hover:bg-primary-soft hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-wait';
	const { session, conversations } = getCoachingContext();

	type History =
		{ status: 'loading' } | { status: 'loaded'; page: MessagePage } | { status: 'error' };

	// 시안: 대화 화면은 대화 목록이 펼쳐진 상태로 시작한다
	let isListOpen = $state(true);
	let isDrawerOpen = $state(false);
	let question = $state('');
	let history = $state<History>({ status: 'loading' });
	let dialogEvidence: Evidence[] = $state([]);
	let isDialogOpen = $state(false);
	let messageLog: HTMLElement | undefined = $state();
	let messageContent: HTMLElement | undefined = $state();
	let follow: FollowLatest | undefined;

	/** 이 대화에서 지금 연결로 보낸 질문 */
	const liveRequests = $derived(
		session.requests.filter((request) => request.conversationId === data.conversationId)
	);
	/**
	 * 지난 메시지(6.3). 같은 요청이 실시간 질문에도 있으면 저장본은 빼고 실시간 쪽을 보여 준다.
	 * 서버는 접수 때 질문과 빈 답변(generating)을 저장하므로 첫 질문 직후 조회 결과에 이미 들어 있고,
	 * 저장본은 다시 불러오지 않아 그 시점에 멈춰 있다.
	 */
	const savedMessages = $derived.by(() => {
		if (history.status !== 'loaded') return [];
		const liveIds = new Set(liveRequests.map((request) => request.requestId));
		return history.page.items.filter((item) => !liveIds.has(item.requestId));
	});
	const title = $derived(
		(history.status === 'loaded' ? history.page.conversation.title : null) ??
			liveRequests.find((request) => request.conversationTitle)?.conversationTitle ??
			'새 대화'
	);
	const isGenerating = $derived(session.isBusy(data.conversationId));
	/** 새로고침·화면 이동 뒤 진행 중으로 저장된 질문은 상태 조회로 끝까지 따라가고 입력을 잠근다 */
	function resumeInProgress(page: MessagePage) {
		for (const item of page.items) {
			if (item.role !== 'user') continue;
			if (item.requestStatus !== 'accepted' && item.requestStatus !== 'generating') continue;
			session.resume({
				requestId: item.requestId,
				conversationId: page.conversation.id,
				content: item.content,
				status: item.requestStatus
			});
		}
	}

	/** 더 이전 메시지(6.3 beforeSequenceNo) 불러오기 상태. 첫 페이지는 최근 메시지부터 서버 기본 개수만 온다 */
	let olderStatus = $state<'idle' | 'loading' | 'error'>('idle');

	/**
	 * 이력 조회 세대. 대화를 옮기거나 다시 불러올 때마다 올린다. 응답이 돌아왔을 때 세대가 다르면 버린다.
	 * 대화 ID만 비교하면 A → B → A로 돌아왔을 때 처음 보낸 느린 A 응답이 통과해 새 이력을 덮어쓴다.
	 */
	let historyRun = 0;
	/** 지금 세대의 조회를 취소하는 신호. 세대가 바뀌면 이전 조회(첫 페이지·이전 메시지)를 함께 끊는다 */
	let historyAbort: AbortController | undefined;

	function startHistoryRun() {
		historyAbort?.abort();
		historyAbort = new AbortController();
		historyRun += 1;
		return { run: historyRun, signal: historyAbort.signal };
	}

	/**
	 * 지난 메시지 위에 더 이전 메시지를 붙인다. 붙이기 전에 보던 메시지가 화면에서 그대로 보이도록
	 * 늘어난 높이만큼 스크롤을 내려 준다(데스크톱은 대화 영역, 좁은 화면은 페이지가 스크롤한다).
	 */
	async function loadOlder() {
		if (history.status !== 'loaded' || olderStatus === 'loading') return;
		const before = history.page.nextBeforeSequenceNo;
		if (before === null) return;
		const conversationId = data.conversationId;
		const run = historyRun;
		// 버튼 바로 아래 첫 메시지를 기준으로 삼는다
		const anchor = messageContent?.querySelector(
			':scope > :not([data-older])'
		) as HTMLElement | null;
		const anchorTop = anchor?.getBoundingClientRect().top;
		olderStatus = 'loading';
		// 위에 붙는 내용 때문에 맨 아래로 끌려가지 않게 한다
		follow?.release();
		try {
			const older = await getConversationMessages(conversationId, {
				beforeSequenceNo: before,
				signal: historyAbort?.signal
			});
			if (run !== historyRun || history.status !== 'loaded') return;
			const known = new Set(history.page.items.map((item) => item.id));
			history = {
				status: 'loaded',
				page: {
					...history.page,
					items: [...older.items.filter((item) => !known.has(item.id)), ...history.page.items],
					nextBeforeSequenceNo: older.nextBeforeSequenceNo
				}
			};
			olderStatus = 'idle';
			await tick();
			if (anchor && anchorTop !== undefined) {
				const moved = anchor.getBoundingClientRect().top - anchorTop;
				if (messageLog && getComputedStyle(messageLog).overflowY !== 'visible')
					messageLog.scrollTop += moved;
				else window.scrollBy(0, moved);
			}
		} catch {
			if (run === historyRun) olderStatus = 'error';
		}
	}

	async function loadHistory(conversationId: string) {
		const { run, signal } = startHistoryRun();
		history = { status: 'loading' };
		olderStatus = 'idle';
		try {
			const page = await getConversationMessages(conversationId, { signal });
			if (run !== historyRun) return;
			history = { status: 'loaded', page };
			resumeInProgress(page);
		} catch {
			if (run === historyRun) history = { status: 'error' };
		}
	}

	$effect(() => {
		const conversationId = data.conversationId;
		untrack(() => loadHistory(conversationId));
		// 다른 대화로 옮기거나 화면을 떠나면 진행 중인 조회를 취소하고, 늦게 온 응답을 버리도록 세대를 올린다
		return () => {
			historyRun += 1;
			historyAbort?.abort();
		};
	});

	// 대화 내용 높이가 바뀌면(스트리밍·근거·오류 안내·지난 메시지) 최신 메시지로 따라간다
	$effect(() => {
		if (!messageLog || !messageContent) return;
		const current = followLatest(messageLog, messageContent);
		follow = current;
		return () => current.destroy();
	});

	// 다른 대화를 열면 위로 올려 둔 상태여도 그 대화의 최신 메시지부터 보여 준다
	$effect(() => {
		void data.conversationId;
		untrack(() => follow?.pin());
	});

	function handleAsk(content: string) {
		// 지난 내용을 읽던 중이어도 새 질문과 생성 중인 답변으로 부드럽게 내려간다
		follow?.pin({ smooth: true });
		void session.ask(content, data.conversationId);
	}

	function handleOpenEvidence(evidence: Evidence[]) {
		dialogEvidence = evidence;
		isDialogOpen = true;
	}
</script>

<!--
	탭 제목에는 대화 제목(첫 질문 문장)을 넣지 않는다(S15P31A403-439). 탭 제목은 브라우저 방문 기록에 그대로 남아,
	컴퓨터를 같이 쓰는 사람이 건강 질문을 볼 수 있었다. 대화 제목은 화면 안의 머리말과 대화 목록에만 둔다
-->
<svelte:head>
	<title>AI 코치 | 캔디핏</title>
</svelte:head>

<!--
	복사에서 코치 캐릭터를 뺀다(copy.ts). 대화 영역에만 걸면 대화 목록·머리말에서 시작한 선택은 처리되지 않아
	문서 전체에 건다. 선택에 캐릭터가 없으면 브라우저 기본 복사 그대로다(438 점검)
-->
<svelte:document oncopy={copyWithoutDecoration} />

<!--
	모든 화면 크기에서 화면 높이에 맞춰 페이지 전체가 스크롤되지 않게 하고, 대화 상자는 고정한 채
	대화 내용만 상자 안에서 스크롤한다(좁은 화면은 목록이 서랍이라 대화 상자 하나). 머리말(목록 버튼·제목)과 입력창은 항상 보인다.
	(app) 레이아웃이 화면 높이로 고정되고, 이 화면은 공통 헤더·오프라인 안내를 뺀 남은 높이를 채운다(flex-1).
	헤더 높이를 빼서 계산하면 오프라인 안내가 뜰 때 그만큼 문서 스크롤이 하나 더 생긴다.
	코칭은 2단 구조라 기본 콘텐츠 폭(Home) 대신 공통 헤더와 같은 폭·좌우 여백의 shell을 쓴다 (docs/design-system.md).
-->
<main
	class={[
		// 휴대폰·태블릿은 대화 상자를 넓게 쓰려고 바깥 여백을 줄인다. 데스크톱은 첫 화면과 같은 여백
		'fit-height mx-auto grid w-full max-w-[1792px] gap-8 px-3 py-3 break-keep sm:px-5 sm:py-4 lg:px-8 lg:py-(--page-padding-y)',
		'min-h-0 flex-1 grid-rows-[minmax(0,1fr)] lg:min-h-[36rem]',
		// 첫 칸은 목록 폭을 따른다(펼침 16.75rem ↔ 레일 4rem). 목록 폭이 부드럽게 바뀌면 대화 영역도 따라 움직인다
		'lg:grid-cols-[auto_minmax(0,1fr)]'
	]}
>
	<ConversationSidebar
		id={LIST_ID}
		items={conversations.items}
		activeId={data.conversationId}
		loading={conversations.status === 'loading'}
		emptyText={conversations.status === 'error'
			? '대화 목록을 불러오지 못했어요.'
			: '아직 대화가 없어요.'}
		onretry={conversations.status === 'error' ? () => conversations.load() : undefined}
		more={{
			hasMore: conversations.hasMore,
			status: conversations.moreStatus,
			load: () => conversations.loadMore()
		}}
		bind:open={isListOpen}
		bind:drawerOpen={isDrawerOpen}
	/>

	<section class="relative flex min-h-0 min-w-0 flex-col rounded-3xl border border-border bg-card">
		<!--
			좁은 화면(lg 미만): 대화 상자가 작아 머리말(제목·대화 제목·구분선)을 두지 않고, 서랍을 여는 목록 버튼만
			상자 왼쪽 위에 동그랗게 띄운다. 대화 내용은 버튼 아래로 지나가며 스크롤된다(위 여백 pt-16이 버튼 자리).
			제목은 탭 제목(svelte:head)과 보조기기용 제목(sr-only)으로 남긴다.
		-->
		<div
			class="absolute top-3 left-3 z-10 rounded-full border border-border bg-card p-0.5 shadow-[0_6px_16px_rgb(44_46_53/0.12)] lg:hidden"
		>
			<ConversationListToggle
				class="rounded-full"
				controls="{LIST_ID}-drawer"
				expanded={isDrawerOpen}
				ontoggle={() => (isDrawerOpen = true)}
			/>
		</div>
		<h1 class="sr-only lg:hidden">AI 코치</h1>

		<!--
			넓은 화면(lg 이상) 머리말: 제목 줄은 목록 버튼과 같은 높이다(카드 테두리 1px + 위 여백 16px + 줄 높이 40px, 첫 화면과 같다).
			아래 줄에 대화 제목을 전체 폭으로 두고, 한 줄 선으로 머리말과 대화 내용을 나눈다. 대화를 내려도 내용은 선 아래에서만 움직인다.
			선은 카드 끝까지 긋지 않고 본문 글자와 같은 좌우 여백 안에 둔다. 새 대화 버튼은 대화 목록 맨 위(접으면 레일)에 있다.
		-->
		<header class="hidden px-9 pt-4 lg:block">
			<div class="flex h-10 items-center gap-3">
				<h1 class="text-page-title">AI 코치</h1>
			</div>
			<!-- 대화 요약(summary)은 AI 문맥용 누적 요약이라 여러 줄이 될 수 있어 화면에는 제목만 둔다 -->
			<p class="mt-2 truncate border-b border-border pb-5 text-sm text-text-secondary">
				{title}
			</p>
		</header>

		<!-- 스크롤 영역이라 키보드로도 스크롤할 수 있게 포커스를 받는다 -->
		<!--
			세로 스크롤바가 둥근 카드의 위·아래 모서리까지 닿아 카드 밖으로 튀어나와 보였다(S15P31A403-438).
			스크롤바 길을 위아래로 모서리 둥글기(24px)만큼 줄이고 바탕을 비워 카드 안에 들어오게 한다
		-->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			bind:this={messageLog}
			class="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain px-4 pt-16 pb-5 sm:px-6 sm:pb-6 lg:px-9 lg:pt-6 lg:pb-8 [&::-webkit-scrollbar-track]:my-6 [&::-webkit-scrollbar-track]:bg-transparent"
			role="log"
			aria-label="대화 내용"
			tabindex="0"
		>
			<!--
				이 요소의 높이 변화를 보고 최신 메시지로 따라간다(followLatest).
				읽기 폭(S15P31A403-326): 넓은 화면에서 답변 한 줄이 화면 끝까지 늘어나지 않게 대화를 가운데 64rem 열에 둔다.
				답변 들여쓰기(pl-9)를 빼면 본문 한 줄이 한국어 약 55~60자다. WCAG 1.4.8(AAA)의 CJK 40자 권장보다 넓지만,
				40자 열은 화면이 비어 보여 팀이 화면 폭과의 균형으로 정했다. 좁은 화면은 원래 폭 그대로다
			-->
			<div
				bind:this={messageContent}
				class="mx-auto flex w-full max-w-(--coaching-reading-width) flex-col gap-6"
			>
				{#if history.status === 'loading' && liveRequests.length === 0}
					<!-- 지난 메시지를 불러오는 동안 질문·답변 모양으로 자리를 잡는다. 보낸 질문이 있으면 그 질문부터 보여 준다 -->
					<ConversationSkeleton />
				{:else if history.status === 'error' && liveRequests.length === 0}
					<CoachingErrorNotice
						message="이전 대화를 불러오지 못했어요. 잠시 후 다시 시도해 주세요."
						retryLabel="다시 불러오기"
						onretry={() => loadHistory(data.conversationId)}
					/>
				{/if}

				{#if history.status === 'loaded' && history.page.nextBeforeSequenceNo !== null}
					<!-- 오래 이어진 대화는 최근 메시지부터 오므로, 맨 위에서 더 이전 메시지를 불러온다 -->
					<div class="flex justify-center" data-older>
						{#if olderStatus === 'error'}
							<p role="alert" class="flex items-center gap-3 text-sm text-text-secondary">
								이전 대화를 불러오지 못했어요.
								<button type="button" class={OLDER_BUTTON} onclick={loadOlder}>다시 불러오기</button
								>
							</p>
						{:else}
							<button
								type="button"
								class={OLDER_BUTTON}
								aria-busy={olderStatus === 'loading'}
								disabled={olderStatus === 'loading'}
								onclick={loadOlder}
							>
								{olderStatus === 'loading' ? '불러오는 중…' : '이전 대화 보기'}
							</button>
						{/if}
					</div>
				{/if}

				{#each savedMessages as message (message.id)}
					{#if message.role === 'user'}
						<UserMessage content={message.content} />
					{:else}
						<AssistantMessage
							{message}
							errorMessage={message.requestStatus === 'failed' && message.errorCode
								? withErrorHint(
										message.errorCode,
										getCoachingErrorMessage(message.errorCode, message.errorContext)
									)
								: undefined}
							onquestion={(text) => (question = text)}
							onopenevidence={() => handleOpenEvidence(message.evidence)}
						/>
					{/if}
				{/each}

				{#each liveRequests as request (request.clientRequestId)}
					{@const message = toAssistantMessage(request)}
					<UserMessage content={request.content} />
					<AssistantMessage
						{message}
						progressText={getProgressText(request)}
						errorMessage={getErrorText(request)}
						onretry={request.error?.retryable && !isGenerating
							? () => handleAsk(request.content)
							: undefined}
						onquestion={(text) => (question = text)}
						onopenevidence={() => handleOpenEvidence(request.evidence)}
					/>
				{/each}
			</div>
		</div>

		<!-- 입력창은 대화 상자 아래에 고정돼 있다(대화 내용만 위에서 스크롤한다) -->
		<div
			class="flex shrink-0 flex-col gap-3 rounded-b-3xl bg-card px-4 pt-2 pb-4 sm:gap-4 sm:px-6 sm:pb-5 lg:px-9 lg:pb-6 [&>*]:mx-auto [&>*]:w-full [&>*]:max-w-(--coaching-reading-width)"
		>
			{#if session.commonError}
				<!-- 특정 질문에 붙일 수 없는 오류(명세 7.3). 답변 실패와 같은 오류 상자 -->
				<CoachingErrorNotice message={session.commonError.message} />
			{/if}
			<QuestionInput bind:value={question} {isGenerating} onsubmit={handleAsk} />
			<!-- 보조 글자 하한(12px)을 지킨다. 좁은 화면은 짧은 문구를 쓰고, 그래도 넘치면 두 줄이 된다. 넓은 화면은 전체 문구 -->
			<p class="text-xs leading-4 break-keep text-text-secondary">
				<span class="lg:hidden">AI 코치는 참고용이며, 진단·처방을 제공하지 않아요.</span>
				<span class="hidden lg:inline"
					>AI 코치는 생활 습관 참고용이며, 진단이나 처방을 제공하지 않아요.</span
				>
			</p>
		</div>
	</section>

	<EvidenceDialog evidence={dialogEvidence} bind:open={isDialogOpen} />
</main>

<style>
	/* 첫 화면(../+page.svelte)과 같은 위아래 여백. 두 화면을 오가도 목록 버튼·제목 높이가 같다 */
	main {
		/* 대화·입력창의 읽기 폭(S15P31A403-326). 메시지 열과 입력창이 같은 폭으로 가운데 정렬된다 */
		--coaching-reading-width: 64rem;
	}

	@media (width >= 64rem) {
		.fit-height {
			--page-padding-y: clamp(1rem, calc(1rem + (100dvh - 640px) * 0.0455), 2.25rem);
		}
	}
</style>
