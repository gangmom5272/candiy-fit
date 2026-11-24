<script lang="ts">
	import { groupEvidenceByMetric, isMergedGroup } from '../evidence';
	import {
		coverageNote,
		groupCoverage,
		formatEvidencePeriod,
		hasAnyValue,
		showsChart
	} from '../evidence-view';
	import { METRIC_LABELS } from '../format';
	import { isKindEvidence, type Evidence, type KindEvidence } from '../types';

	type Props = {
		evidence: Evidence[];
		onopen?: () => void;
	};

	let { evidence, onopen }: Props = $props();

	const groups = $derived(groupEvidenceByMetric(evidence));
	const first = $derived(groups[0]);
	/** 모양이 다른 근거(수면 세션·시간대 걸음). 지표 근거 없이 이것만 와도 카드를 보여 준다(S15P31A403-312) */
	const kinds = $derived(
		evidence
			.filter((item): item is KindEvidence => isKindEvidence(item))
			.sort((a, b) => a.position - b.position)
	);
	const KIND_LABELS: Record<KindEvidence['snapshot']['kind'], string> = {
		sleep_sessions: '수면 기록',
		hourly_steps: '시간대 걸음'
	};
	const period = $derived(first?.current ?? kinds[0]);
	const days = $derived(first?.current.snapshot.days.length ?? 0);
	// 상세 창에 차트가 하나도 없으면(기록이 하루 이하·하루 기간) "차트 보기"라고 부르지 않는다
	const hasChart = $derived(groups.some((group) => showsChart(group.current.snapshot)));
	const titleId = $props.id();
</script>

{#if period}
	<section
		class="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-border bg-card px-6 py-5 break-keep"
		aria-labelledby={titleId}
	>
		<div class="flex min-w-0 flex-col gap-3">
			<h2 id={titleId} class="text-card-title">이 답변에서 사용한 기록</h2>
			<!-- "최근"은 "이번 주·지난주" 질문에서 틀린 말이 되어 일수만 쓴다 -->
			<p class="text-base text-text-primary">
				<!--
					날짜 범위는 한 덩어리(inline-block)로 둔다. 줄에 자리가 모자라면 덩어리째 다음 줄로 내려가므로
					"~" 앞뒤에서 갈리지 않는다(320px 화면에서 "10.07"만 떨어졌다). 줄바꿈을 아예 막으면(nowrap) 글자를
					200%로 키웠을 때 카드 밖으로 나가 잘리므로, 덩어리가 카드보다 넓을 때만 그 안에서 줄을 나눈다
				-->
				{days > 1 ? `${days}일 · ` : ''}<span class="inline-block max-w-full"
					>{formatEvidencePeriod(period.periodStart, period.periodEnd)}</span
				>
				{#if first && isMergedGroup(first)}
					<!-- 한 주씩 여러 번, 또는 31일 제한으로 둘로 나눠 조회한 기록이다. 처음 날부터 마지막 날까지를 한 범위로 보여 준다 -->
					<span class="text-text-secondary">· {first.parts.length}번 나눠 조회</span>
				{/if}
				{#if first?.comparison}
					· 비교
					<span class="inline-block max-w-full"
						>{formatEvidencePeriod(first.comparison.periodStart, first.comparison.periodEnd)}</span
					>
				{/if}
			</p>
			<p class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-secondary">
				사용 데이터:
				{#each groups as group (group.metric)}
					{@const cover = groupCoverage(group)}
					{@const note = coverageNote(cover)}
					<span class={hasAnyValue(cover) ? 'text-primary' : 'text-text-secondary'}>
						{METRIC_LABELS[group.metric]}{#if note}<span class="text-text-secondary"
								>{` · ${note}`}</span
							>{/if}
					</span>
				{/each}
				{#each kinds as item (item.id)}
					<span class="text-primary">{KIND_LABELS[item.snapshot.kind]}</span>
				{/each}
			</p>
		</div>
		<button
			type="button"
			class="flex items-center gap-1 rounded-lg px-2 py-1 text-lg font-bold text-primary hover:bg-primary-soft"
			onclick={onopen}
		>
			{hasChart ? '차트 보기' : '기록 보기'}
			<svg
				class="size-4"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2"
				aria-hidden="true"
			>
				<path d="M5 12h14m-6-6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		</button>
	</section>
{/if}
