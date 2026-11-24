<script lang="ts">
	import { SPO2_TIMES_PREVIEW, type Spo2LowState } from '../evidence-view';
	import { formatClockTime } from '../format';

	/**
	 * 수면 중 산소포화도 90% 미만 한 줄(S15P31A403-312). 네 가지 상태를 서로 다른 글로 보여 준다.
	 * 시각이 많으면 앞의 몇 개만 두고 "외 N회"를 눌러 전체를 펼친다. 원본 목록은 자르지 않는다
	 * bare: 표의 칸처럼 이름표가 머리글에 따로 있을 때 값만 쓴다(S15P31A403-404)
	 */
	let { state, bare = false }: { state: Spo2LowState; bare?: boolean } = $props();

	const LABEL = $derived(bare ? '' : '산소포화도 90% 미만 ');
</script>

{#if state.kind === 'times'}
	{@const preview = state.times.slice(0, SPO2_TIMES_PREVIEW)}
	{@const rest = state.times.length - preview.length}
	<div class="text-[13px] text-text-primary">
		{#if rest > 0}
			<!-- 펼치면 앞 몇 개를 다시 쓰지 않고 전체 목록 한 줄로 바꾼다 -->
			<details class="group">
				<summary class="cursor-pointer list-none [&::-webkit-details-marker]:hidden">
					<span class="font-bold">{LABEL}{state.times.length}회</span>
					<span class="group-open:hidden"> · {preview.map(formatClockTime).join(', ')}</span>
					<span class="font-bold text-primary group-open:hidden"> 외 {rest}회 보기</span>
					<span class="hidden font-bold text-primary group-open:inline"> 접기</span>
				</summary>
				<p class="mt-1 leading-relaxed">{state.times.map(formatClockTime).join(', ')}</p>
			</details>
		{:else}
			<span class="font-bold">{LABEL}{state.times.length}회</span>
			· {preview.map(formatClockTime).join(', ')}
		{/if}
	</div>
{:else}
	<p class="text-[13px] text-text-primary">
		{#if !bare}
			<span class="font-bold">{LABEL}</span>
			·
		{/if}
		{state.kind === 'none'
			? '없음'
			: state.kind === 'count-only'
				? `${state.count}회 (시각 정보 없음)`
				: '측정 없음'}
	</p>
{/if}
