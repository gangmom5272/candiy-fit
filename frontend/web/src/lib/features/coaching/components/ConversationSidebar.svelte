<script lang="ts">
	import { tick, type ComponentProps } from 'svelte';
	import { prefersReducedMotion } from 'svelte/motion';
	import { fade, fly } from 'svelte/transition';
	import { afterNavigate } from '$app/navigation';
	import type { ConversationItem } from '../types';
	import ConversationList from './ConversationList.svelte';
	import ConversationListToggle from './ConversationListToggle.svelte';
	import NewConversationLink from './NewConversationLink.svelte';

	type Props = {
		/** 넓은 화면 목록(레일)의 id. 서랍은 `${id}-drawer` */
		id: string;
		items: ConversationItem[];
		activeId?: string | null;
		/** 목록을 처음 불러오는 중. 목록(ConversationList)에 그대로 넘긴다 */
		loading?: boolean;
		emptyText?: string;
		/** 목록을 불러오지 못했을 때 다시 불러오기. 목록(ConversationList)에 그대로 넘긴다 */
		onretry?: () => void;
		/** 지금 새 대화(코칭 첫 화면)에 있는지. 새 대화 버튼을 두지 않는다 */
		isNewConversation?: boolean;
		/** 이전 대화 이어 읽기. 목록(ConversationList)에 그대로 넘긴다 */
		more?: ComponentProps<typeof ConversationList>['more'];
		/** 넓은 화면(lg 이상)에서 목록을 펼쳤는지. 접으면 버튼만 있는 얇은 레일이 남는다 */
		open?: boolean;
		/** 좁은 화면 서랍이 열렸는지. 여는 버튼은 페이지 제목 왼쪽에 있다 */
		drawerOpen?: boolean;
	};

	let {
		id,
		items,
		activeId = null,
		loading = false,
		emptyText,
		onretry,
		isNewConversation = false,
		more,
		open = $bindable(true),
		drawerOpen = $bindable(false)
	}: Props = $props();

	/** 레일 폭 변화(CSS)와 같은 시간. 움직임 줄이기 설정이면 바로 바뀐다 */
	const duration = (ms: number) => (prefersReducedMotion.current ? 0 : ms);

	let dialog: HTMLDialogElement | undefined = $state();

	async function setOpen(next: boolean) {
		open = next;
		await tick();
		// 누른 버튼은 레일 ↔ 목록 머리말로 바뀌지만 같은 자리에 다시 생긴다. 포커스도 그 버튼으로 옮긴다.
		// 접는 동안 목록이 흐려지며 잠시 남아 있으므로 펼침 상태(aria-expanded)로 새 버튼을 고른다
		document
			.querySelector<HTMLElement>(`#${id} [aria-controls="${id}"][aria-expanded="${next}"]`)
			?.focus();
	}

	// 열 때는 바로 모달로 띄우고, 닫을 때는 패널이 다 들어간 뒤(onoutroend) 모달을 닫는다
	$effect(() => {
		if (dialog && drawerOpen && !dialog.open) dialog.showModal();
	});

	// 서랍에서 대화를 고르면(주소가 바뀌면) 닫는다
	afterNavigate(() => (drawerOpen = false));

	// 넓은 화면에서 서랍이 열려 있으면 서랍은 CSS 로 숨지만 모달은 남아 화면을 막는다.
	// 서랍을 연 채 넓어질 때와, 처음부터 넓은데 열린 상태로 들어온 경우 모두 닫는다
	$effect(() => {
		const wide = window.matchMedia('(width >= 64rem)');
		const close = () => {
			if (wide.matches) drawerOpen = false;
		};
		close();
		wide.addEventListener('change', close);
		return () => wide.removeEventListener('change', close);
	});
</script>

<!--
	넓은 화면: 펼치면 목록, 접으면 버튼만 있는 레일. 버튼은 두 경우 모두 맨 위 왼쪽(위 py-4·왼쪽 pl-3) 같은 자리다.
	폭이 부드럽게 바뀌고(옆 대화 영역도 따라 움직인다), 목록은 고정 폭이라 줄바꿈이 흔들리지 않고 가장자리부터 가려진다.
-->
<div
	{id}
	class={[
		'relative hidden min-h-0 shrink-0 overflow-hidden lg:block',
		'transition-[width] duration-300 ease-[cubic-bezier(0.2,0,0,1)] motion-reduce:transition-none',
		open ? 'w-67' : 'w-16'
	]}
>
	{#if open}
		<div
			class="absolute inset-y-0 left-0 z-10 w-67"
			in:fade={{ duration: duration(200), delay: duration(60) }}
			out:fade={{ duration: duration(150) }}
		>
			<ConversationList
				{items}
				{activeId}
				{loading}
				{emptyText}
				{onretry}
				{isNewConversation}
				{more}
				controls={id}
				oncollapse={() => setOpen(false)}
			/>
		</div>
	{:else}
		<!-- 접힌 레일은 테두리·배경 없이 버튼만 보인다(빈 상자가 화면 높이만큼 남지 않게).
		     테두리 두께(1px)는 투명하게 남겨 펼친 목록의 버튼과 자리가 1px 도 어긋나지 않게 한다 -->
		<div class="flex h-full flex-col items-start gap-2 border border-transparent py-4 pl-3">
			<ConversationListToggle controls={id} expanded={false} ontoggle={() => setOpen(true)} />
			{#if !isNewConversation}
				<NewConversationLink compact />
			{/if}
		</div>
	{/if}
</div>

<!--
	좁은 화면: 왼쪽에서 밀려 나오는 서랍. 바깥(어두운 부분)을 누르거나 Esc·대화 선택으로 닫는다.
	서랍 안에는 눈에 보이는 목록 버튼을 두지 않는다(여는 버튼은 화면에 떠 있는 동그란 버튼 하나뿐이라 헷갈리지 않게).
	대신 보조기기·키보드만 닿는 닫기 버튼을 목록 끝에 둔다(키보드 포커스가 오면 그때만 보인다).
	dialog 는 화면 전체를 덮는 투명 판이고, 어두운 판과 목록 패널을 그 안에 둔다. 기본 ::backdrop 은 사라질 때
	움직임을 줄 수 없어 쓰지 않는다. Esc(cancel)도 바로 닫지 않고 패널이 들어간 뒤 닫는다.
-->
<dialog
	bind:this={dialog}
	id="{id}-drawer"
	class="m-0 h-dvh max-h-dvh w-dvw max-w-none bg-transparent p-0 backdrop:bg-transparent lg:hidden"
	aria-label="대화 목록"
	oncancel={(event) => {
		event.preventDefault();
		drawerOpen = false;
	}}
	onclose={() => (drawerOpen = false)}
>
	{#if drawerOpen}
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div
			class="absolute inset-0 bg-text-primary/30"
			transition:fade={{ duration: duration(200) }}
			onclick={() => (drawerOpen = false)}
			data-drawer-backdrop
		></div>
		<div
			class="relative h-full w-[min(20rem,85vw)] p-3"
			in:fly={{ x: -320, duration: duration(280), opacity: 1 }}
			out:fly={{ x: -320, duration: duration(220), opacity: 1 }}
			onoutroend={() => dialog?.close()}
		>
			<ConversationList
				{items}
				{activeId}
				{loading}
				{emptyText}
				{onretry}
				{isNewConversation}
				{more}
				controls="{id}-drawer"
			/>
			<!-- 화면 읽기 프로그램·키보드용 닫기. 목록 다음 순서라 열릴 때 포커스는 목록으로 먼저 간다 -->
			<button
				type="button"
				class="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-5 focus-visible:right-5 focus-visible:z-10 focus-visible:rounded-lg focus-visible:bg-card focus-visible:px-3 focus-visible:py-1.5 focus-visible:text-sm focus-visible:font-semibold focus-visible:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
				onclick={() => (drawerOpen = false)}
			>
				대화 목록 닫기
			</button>
		</div>
	{/if}
</dialog>
