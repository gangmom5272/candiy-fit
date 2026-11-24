<script lang="ts">
	import type { WeekScroll } from '../week-scroll.svelte';

	/**
	 * 넘겨 보는 일별 차트 아래의 가는 막대(S15P31A403-418). 기간 전체에서 지금 어디를 보고 있는지 보여 주고,
	 * 잡고 끌거나 눌러서 그 자리로 옮긴다. 브라우저 스크롤바는 숨겼고, 날짜 버튼으로 일주일씩 건너뛰는 대신
	 * 사용자가 직접 자유롭게 넘기게 한다는 결정에 따라 넘길 수 있다는 표시를 이 막대가 맡는다.
	 * 키보드는 차트(넘어가는 칸)에 포커스를 두고 방향키로 넘기므로 이 막대는 보조기기에 읽히지 않게 한다
	 */
	type Props = { range: WeekScroll };

	let { range }: Props = $props();

	let track: HTMLElement | undefined = $state();
	let held = $state(false);

	/** 누른 곳이 손잡이의 가운데가 되게 옮긴다 */
	function follow(event: PointerEvent) {
		if (!track) return;
		const box = track.getBoundingClientRect();
		const handle = box.width * range.shownShare;
		const room = box.width - handle;
		if (room <= 0) return;
		range.seek((event.clientX - box.left - handle / 2) / room);
	}
</script>

{#if range.scrolls}
	<!-- 누르기 쉽게 위아래 여백까지 잡을 수 있는 영역으로 두고, 보이는 막대는 그 안에 가늘게 그린다 -->
	<div
		bind:this={track}
		class="group mt-1 cursor-pointer touch-none py-2"
		aria-hidden="true"
		data-range-bar
		onpointerdown={(event) => {
			held = true;
			event.currentTarget.setPointerCapture(event.pointerId);
			follow(event);
		}}
		onpointermove={(event) => {
			if (held) follow(event);
		}}
		onpointerup={() => (held = false)}
		onpointercancel={() => (held = false)}
	>
		<div class="relative h-1 rounded-full bg-border">
			<div
				class={[
					'absolute inset-y-0 rounded-full transition-colors',
					held ? 'bg-brand' : 'bg-coral-main group-hover:bg-brand'
				]}
				style:width={`${range.shownShare * 100}%`}
				style:left={`${range.progress * (1 - range.shownShare) * 100}%`}
				data-range-handle
			></div>
		</div>
	</div>
{/if}
