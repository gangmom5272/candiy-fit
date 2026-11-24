<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { conversations } from '../fixtures';
	import ConversationListToggle from './ConversationListToggle.svelte';
	import ConversationSidebar from './ConversationSidebar.svelte';

	// 넓은 화면 레일·목록은 lg(1024px) 이상에서만, 좁은 화면 서랍은 lg 미만에서만 보인다
	const { Story } = defineMeta({
		title: 'Features/coaching/ConversationSidebar',
		component: ConversationSidebar,
		parameters: {
			docs: {
				description: {
					component:
						'대화 목록 묶음. 넓은 화면은 펼친 목록 ↔ 버튼만 있는 레일, 좁은 화면은 왼쪽 서랍. 버튼은 모든 경우 맨 위 왼쪽 같은 자리다.'
				}
			}
		}
	});
</script>

<script lang="ts">
	// 서랍은 화면이 처음 뜰 때 닫힌다(afterNavigate). 실제 화면처럼 제목 옆 버튼으로 연다
	let drawerOpen = $state(false);
	// 열고 접을 때 목록 폭이 부드럽게 바뀌고 옆 본문도 따라 움직인다
	let wideOpen = $state(true);
</script>

<Story name="넓은 화면 · 펼침" exportName="WideOpen" asChild>
	<div class="flex h-[40rem] gap-8 p-6">
		<ConversationSidebar
			id="story-wide-open"
			items={conversations}
			activeId={conversations[0].id}
			open
		/>
		<p class="text-sm text-text-secondary">본문 자리</p>
	</div>
</Story>

<Story name="넓은 화면 · 접힘(레일)" exportName="WideRail" asChild>
	<div class="flex h-[40rem] gap-8 p-6">
		<ConversationSidebar
			id="story-wide-rail"
			items={conversations}
			activeId={conversations[0].id}
			open={false}
		/>
		<p class="text-sm text-text-secondary">본문 자리</p>
	</div>
</Story>

<Story name="넓은 화면 · 열고 접기 (움직임)" exportName="WideToggle" asChild>
	<div class="flex h-[40rem] gap-8 p-6">
		<ConversationSidebar
			id="story-wide-toggle"
			items={conversations}
			activeId={conversations[0].id}
			bind:open={wideOpen}
		/>
		<p
			class="min-w-0 flex-1 rounded-3xl border border-border bg-card p-6 text-sm text-text-secondary"
		>
			본문 자리. 목록 버튼을 누르면 목록 폭이 바뀌고 이 상자도 함께 움직인다
		</p>
	</div>
</Story>

<!-- 좁은 화면(뷰포트를 휴대폰으로)에서 버튼을 누르면 서랍이 열린다 -->
<Story name="좁은 화면 · 서랍" exportName="NarrowDrawer" asChild>
	<div class="flex items-center gap-3 p-6">
		<ConversationListToggle
			controls="story-narrow-drawer"
			expanded={drawerOpen}
			ontoggle={() => (drawerOpen = true)}
		/>
		<h1 class="text-[28px] font-bold">AI 코치</h1>
	</div>
	<ConversationSidebar
		id="story-narrow"
		items={conversations}
		activeId={conversations[0].id}
		bind:drawerOpen
	/>
</Story>
