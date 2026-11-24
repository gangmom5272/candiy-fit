<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import Toast from '#lib/components/feedback/Toast.svelte';
	import CoachingProgress from '#lib/features/coaching/components/CoachingProgress.svelte';
	import CoachingWelcome from '#lib/features/coaching/components/CoachingWelcome.svelte';
	import ConversationListToggle from '#lib/features/coaching/components/ConversationListToggle.svelte';
	import ConversationSidebar from '#lib/features/coaching/components/ConversationSidebar.svelte';
	import { getCoachingContext } from '#lib/features/coaching/context.svelte.js';
	import type { CoachingRequest } from '#lib/features/coaching/session.svelte.js';

	const LIST_ID = 'coaching-conversation-list';
	/** 오류 토스트를 보여 주는 시간 */
	const TOAST_DURATION_MS = 3000;
	/** 다시 보내기 버튼을 누를 수 있도록 버튼이 있으면 더 오래 보여 준다 */
	const RETRY_TOAST_DURATION_MS = 6000;
	const { session, conversations } = getCoachingContext();

	// 시안: 첫 화면은 대화 목록이 접힌 상태로 시작한다
	let isListOpen = $state(false);
	let isDrawerOpen = $state(false);
	/** 이 화면에서 보낸 새 대화의 첫 질문. 접수되어 대화 ID가 생기면 대화 화면으로 넘어간다 */
	let pending = $state<CoachingRequest | null>(null);
	let isSubmitting = $state(false);

	/** 다시 보낼 질문이 있으면 retryContent에 담는다 */
	let errorToast = $state<{ message: string; retryContent: string | null } | null>(null);
	let toastTimer: ReturnType<typeof setTimeout> | undefined;

	const isWaiting = $derived(isSubmitting || pending?.phase === 'sending');

	$effect(() => {
		const conversationId = pending?.conversationId;
		if (!conversationId) return;
		goto(resolve('/(app)/coaching/[conversationId]', { conversationId }));
	});

	// 새 오류가 올 때마다 토스트로 잠시 안내한다. 같은 문구여도 오류·질문 객체가 새로 생기면 다시 띄운다.
	$effect(() => {
		const rejected = pending?.phase === 'rejected' ? pending : null;
		const message = session.commonError?.message ?? rejected?.error?.message;
		if (!message) return;
		const retryContent = rejected?.error?.retryable ? rejected.content : null;
		untrack(() => showErrorToast(message, retryContent));
	});

	$effect(() => () => clearTimeout(toastTimer));

	function showErrorToast(message: string, retryContent: string | null) {
		errorToast = { message, retryContent };
		clearTimeout(toastTimer);
		toastTimer = setTimeout(
			() => (errorToast = null),
			retryContent === null ? TOAST_DURATION_MS : RETRY_TOAST_DURATION_MS
		);
	}

	async function handleSubmit(content: string) {
		if (isSubmitting) return;
		errorToast = null;
		clearTimeout(toastTimer);
		isSubmitting = true;
		try {
			pending = await session.ask(content, null);
		} finally {
			isSubmitting = false;
		}
	}
</script>

<svelte:head>
	<title>AI 코치 | 캔디핏</title>
</svelte:head>

<!--
	코칭은 2단 구조라 기본 콘텐츠 폭(Home) 대신 공통 헤더와 같은 폭·좌우 여백의 shell을 쓴다 (docs/design-system.md).
	데스크톱(lg 이상)에서는 (app) 레이아웃이 화면 높이로 고정되고, 이 화면은 헤더·오프라인 안내를 뺀 남은 높이를 채운다(flex-1)
-->
<main
	class="fit-height mx-auto flex w-full max-w-[1792px] gap-8 px-5 py-9 break-keep sm:px-8 lg:min-h-[36rem] lg:flex-1 lg:py-(--page-padding-y)"
>
	<ConversationSidebar
		id={LIST_ID}
		items={conversations.items}
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
		isNewConversation
		bind:open={isListOpen}
		bind:drawerOpen={isDrawerOpen}
	/>

	<div class="flex min-w-0 flex-1 flex-col pt-12 lg:gap-(--title-gap) lg:pt-0">
		<!--
			좁은 화면(lg 미만): 대화 화면과 같이 머리말(제목)을 두지 않고, 서랍을 여는 목록 버튼만 공통 헤더 아래 왼쪽에
			동그랗게 띄운다(대화 화면의 상자 왼쪽 위 버튼과 같은 자리·모양). 페이지가 스크롤돼도 그 자리에 있다(fixed).
			제목은 탭 제목(svelte:head)과 보조기기용 제목(sr-only)으로 남기고, 위 여백(pt-12)은 버튼 자리다.
		-->
		<div
			class="fixed top-[calc(var(--app-header-height,4rem)+0.75rem)] left-6 z-40 rounded-full border border-border bg-card p-0.5 shadow-[0_6px_16px_rgb(44_46_53/0.12)] sm:left-8 lg:hidden"
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
			넓은 화면(lg 이상): 시안처럼 제목을 입력창 왼쪽 끝(가운데 80rem 영역)에 맞춘다. 목록 버튼은 왼쪽 레일에 있다.
			첫 칸(빈 div)은 정렬용이다. 세로는 목록 버튼(목록 테두리 1px + 위 여백 16px + 버튼 40px)과 가운데를 맞춘다.
			대화 화면 제목도 같은 높이다.
		-->
		<header
			class="mt-[17px] hidden h-10 grid-cols-[minmax(0,1fr)_minmax(0,80rem)_minmax(0,1fr)] lg:grid"
		>
			<div></div>
			<!-- 페이지 제목(30px·줄간격 39px)이 버튼 높이(40px) 안에 들어가 grid 칸이 늘지 않고 가운데가 맞는다 -->
			<h1 class="text-page-title">AI 코치</h1>
		</header>

		<CoachingWelcome
			isGenerating={isWaiting}
			onsubmit={handleSubmit}
			notice={isWaiting ? notice : undefined}
		/>
	</div>
</main>

{#if errorToast}
	{@const retryContent = errorToast.retryContent}
	<Toast
		kind="error"
		message={errorToast.message}
		action={retryContent === null
			? undefined
			: { label: '다시 보내기', onclick: () => handleSubmit(retryContent) }}
	/>
{/if}

{#snippet notice()}
	<!-- 첫 질문을 보내 대화가 만들어지기 전까지. 답변 작성 중과 같은 진행 안내 모양 -->
	<CoachingProgress text="질문을 보내고 있어요" />
{/snippet}

<style>
	/*
	 * 노트북·모니터(lg 이상)에서 세로 스크롤 없이 보이도록 간격을 화면 높이에 맞춰 줄인다.
	 * 높이 640px에서 최소값, 1080px(Figma)에서 시안 값이 되고 그 사이는 고르게 바뀐다(마이페이지와 같은 방식).
	 */
	@media (width >= 64rem) {
		.fit-height {
			--page-padding-y: clamp(1rem, calc(1rem + (100dvh - 640px) * 0.0455), 2.25rem);
			--title-gap: clamp(2rem, calc(2rem + (100dvh - 640px) * 0.2545), 9rem);
			--welcome-gap: clamp(2rem, calc(2rem + (100dvh - 640px) * 0.0545), 3.5rem);
			--welcome-example-height: clamp(8rem, calc(8rem + (100dvh - 640px) * 0.0727), 10rem);
		}
	}
</style>
