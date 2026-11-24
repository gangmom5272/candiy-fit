<script lang="ts">
	import { untrack } from 'svelte';
	import { averageDelta, groupEvidenceByMetric, isMergedGroup } from '../evidence';
	import {
		beforeLinkDays,
		calendarAverage,
		chartBars,
		chartExtremes,
		chartSkipReason,
		coverage,
		dayBars,
		dayHighlights,
		dayListItems,
		exerciseActivityView,
		formatEvidencePeriod,
		groupCoverage,
		hasAnyValue,
		listsAllDays,
		MAX_LISTED_SESSIONS,
		measuredNote,
		measuredRange,
		MIN_DAYS_FOR_TREND,
		nightSpo2Items,
		MISSING_REASON_LABELS,
		sessionItems,
		showsChart,
		sleepStageAverages,
		spo2Summary,
		spo2SummaryNote,
		stageNote,
		type Coverage,
		type SessionItem
	} from '../evidence-view';
	import {
		formatClockTime,
		formatDayWithWeekday,
		formatMetricDelta,
		formatMetricValue,
		formatShortDate,
		METRIC_LABELS
	} from '../format';
	import {
		BODY_METRICS,
		isKindEvidence,
		type Evidence,
		type HealthMetric,
		type KindEvidence,
		type MetricEvidence
	} from '../types';
	import { Button } from '#lib/components/ui/button/index.js';
	import { isLineChart } from '../chart';
	import type { RangeView } from '../week-scroll.svelte';
	import DailyBarChart from './DailyBarChart.svelte';
	import DailyLineChart from './DailyLineChart.svelte';
	import DayCalendar from './DayCalendar.svelte';
	import ExerciseActivitySection from './ExerciseActivitySection.svelte';
	import KindEvidencePanel from './KindEvidencePanel.svelte';
	import SleepStageBars from './SleepStageBars.svelte';
	import Spo2LowTimes from './Spo2LowTimes.svelte';

	type Props = {
		evidence: Evidence[];
		open?: boolean;
	};

	let { evidence, open = $bindable(false) }: Props = $props();

	const AVERAGE_LABELS: Record<HealthMetric, string> = {
		sleeps: '평균 수면',
		steps: '평균 걸음 수',
		heart_rates: '평균 심박',
		oxygen_saturations: '평균 산소포화도',
		exercises: '평균 운동 시간',
		weights: '평균 체중',
		body_fats: '평균 체지방률',
		basal_metabolic_rates: '평균 기초대사량',
		heights: '평균 키'
	};
	/** 기록이 하루뿐일 때 카드 제목. 평균이라고 부르면 여러 날을 모은 값처럼 읽힌다 */
	const DAY_LABELS: Record<HealthMetric, string> = {
		sleeps: '수면 시간',
		steps: '걸음 수',
		heart_rates: '평균 심박',
		oxygen_saturations: '산소포화도',
		exercises: '운동 시간',
		weights: '체중',
		body_fats: '체지방률',
		basal_metabolic_rates: '기초대사량',
		heights: '키'
	};
	const CHART_TITLES: Record<HealthMetric, string> = {
		sleeps: '일별 수면 시간',
		steps: '일별 걸음 수',
		heart_rates: '일별 평균 심박',
		oxygen_saturations: '일별 산소포화도',
		exercises: '일별 운동 시간',
		weights: '일별 체중',
		body_fats: '일별 체지방률',
		basal_metabolic_rates: '일별 기초대사량',
		heights: '일별 키'
	};
	const UNIT_LABELS: Record<HealthMetric, string> = {
		sleeps: '시간',
		steps: '걸음',
		heart_rates: 'bpm',
		oxygen_saturations: '%',
		exercises: '분',
		weights: 'kg',
		body_fats: '%',
		basal_metabolic_rates: 'kcal',
		heights: 'cm'
	};

	let dialog: HTMLDialogElement | undefined = $state();
	/** 고른 탭. 날짜별 지표는 지표 코드, 모양이 다른 근거(kind)는 근거 id */
	let selectedKey: string | null = $state(null);
	/** 창 안을 얼마나 내렸는지(0~1). 내릴 내용이 없으면 null이라 막대를 두지 않는다 */
	let scrollProgress: number | null = $state(null);
	/**
	 * 창을 연 횟수. 차트의 보기(일주일·기간 전체)를 창을 열 때마다 처음으로 되돌리는 데 쓴다(S15P31A403-439).
	 * 이 창은 화면에 한 번만 만들어져 계속 쓰이므로, 차트를 다시 만들지 않으면 앞에서 고른 "한 달"이 다음 답변의 차트에도 남는다
	 */
	let openCount = $state(0);
	/**
	 * 창이 열려 있는 동안 차트마다 고른 보기. 탭을 옮겼다 돌아오면 그 탭에서 고른 보기로 다시 시작하게 한다.
	 * 다른 탭의 차트에는 번지지 않고, 창을 다시 열면 비운다
	 */
	let chartViews: Record<string, RangeView> = $state({});

	const groups = $derived(groupEvidenceByMetric(evidence));
	/** 모양이 다른 근거(수면 세션·시간대 걸음)는 지표 묶음 뒤에 근거마다 탭 하나로 둔다(S15P31A403-312) */
	const kinds = $derived(
		evidence
			.filter((item): item is KindEvidence => isKindEvidence(item))
			.sort((a, b) => a.position - b.position)
	);
	const KIND_LABELS: Record<KindEvidence['snapshot']['kind'], string> = {
		sleep_sessions: '수면 기록',
		hourly_steps: '시간대 걸음'
	};
	const tabs = $derived([
		...groups.map((group) => ({ key: group.metric as string, label: METRIC_LABELS[group.metric] })),
		...kinds.map((item) => ({ key: item.id, label: KIND_LABELS[item.snapshot.kind] }))
	]);
	const activeKey = $derived(
		tabs.find((tab) => tab.key === selectedKey)?.key ?? tabs[0]?.key ?? null
	);
	const selected = $derived(groups.find((group) => group.metric === activeKey) ?? null);
	const selectedKind = $derived(kinds.find((item) => item.id === activeKey) ?? null);
	const delta = $derived(selected ? averageDelta(selected) : null);
	const tabId = $props.id();

	$effect(() => {
		if (!dialog) return;
		if (open && !dialog.open) {
			// 다른 답변의 근거를 열 수도 있으므로 열 때마다 첫 지표부터 보여 준다
			selectedKey = null;
			openCount = untrack(() => openCount) + 1;
			chartViews = {};
			dialog.showModal();
			dialog.scrollTop = 0;
			// showModal 은 창 안 첫 버튼(닫기)에 포커스를 주고, 그 포커스는 마우스로 열어도 테두리가 보인다.
			// 제목으로 옮겨 닫기 버튼에 테두리가 먼저 뜨지 않게 한다. 보조기기는 창 이름과 같은 제목부터 읽는다
			dialog.querySelector<HTMLElement>('[data-dialog-title]')?.focus();
		}
		if (!open && dialog.open) dialog.close();
	});

	function updateScrollProgress() {
		if (!dialog) return;
		const max = dialog.scrollHeight - dialog.clientHeight;
		scrollProgress = max > 1 ? Math.min(1, dialog.scrollTop / max) : null;
	}

	// 창이 열리거나 지표 탭·화면 크기가 바뀌면 내용 길이가 달라져 다시 잰다
	$effect(() => {
		if (!dialog) return;
		const observer = new ResizeObserver(updateScrollProgress);
		observer.observe(dialog);
		for (const child of dialog.children) observer.observe(child);
		return () => observer.disconnect();
	});

	function handleTabKeydown(event: KeyboardEvent, index: number) {
		if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
		event.preventDefault();
		const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
		selectedKey = tabs[next].key;
		document.getElementById(`${tabId}-tab-${next}`)?.focus();
	}

	/** 기록이 하루뿐이면 그 날짜를 붙여 "10.27 수면 시간"처럼, 아니면 평균으로 부른다 */
	function statTitle(item: MetricEvidence, cover: Coverage, averageTitle: string): string {
		if (cover.recordedDays !== 1) return averageTitle;
		const day = item.snapshot.days.find((entry) => !entry.missing && !entry.is_in_progress);
		return day ? `${formatDayWithWeekday(day.date)} ${DAY_LABELS[item.metric]}` : averageTitle;
	}

	/**
	 * 주 단위 막대의 가장 높은 주와 낮은 주(S15P31A403-418). 주별 차트는 색도 평균선도 없이 비슷한 막대만 늘어서
	 * 무엇을 보라는 것인지 읽히지 않았다. 막대에 이미 그린 값 가운데서 고르므로 새로 계산하는 값은 없다.
	 * 주가 셋보다 적거나 값이 모두 같으면 달지 않는다
	 */
	function weekExtremes(weeks: ReturnType<typeof chartBars>['bars']) {
		const valued = weeks.flatMap((week) =>
			week.value === null ? [] : [{ key: week.key, value: week.value }]
		);
		if (valued.length < 3) return null;
		const max = valued.reduce((best, week) => (week.value > best.value ? week : best));
		const min = valued.reduce((best, week) => (week.value < best.value ? week : best));
		return max.value === min.value ? null : { max, min: min.value === 0 ? null : min };
	}

	function periodText(item: MetricEvidence): string {
		return formatEvidencePeriod(item.periodStart, item.periodEnd);
	}
</script>

{#snippet stat(
	title: string,
	value: string,
	caption: string | null,
	tone: 'default' | 'primary' | 'muted' = 'default'
)}
	<dt class="text-[13px] font-bold text-text-primary">{title}</dt>
	<dd
		class={[
			'mt-2 text-metric whitespace-nowrap',
			tone === 'primary' && 'text-brand',
			tone === 'muted' && 'text-text-primary'
		]}
	>
		{value}
	</dd>
	{#if caption}
		<dd class="mt-1 text-xs text-text-primary">{caption}</dd>
	{/if}
{/snippet}

{#snippet dayList(items: ReturnType<typeof dayListItems>, metric: HealthMetric)}
	<ol
		class="grid grid-cols-3 gap-x-3 gap-y-4 rounded-2xl border border-border bg-surface px-5 py-5 sm:grid-cols-7"
		data-day-list
	>
		{#each items as item (item.kind === 'day' ? item.date : `${item.start}-gap`)}
			{#if item.kind === 'day'}
				<!-- 날짜 줄은 한 줄로 두어 칸마다 값 줄 높이가 같게 한다. "수집 중"은 날짜 뒤가 아니라 값 아래에 둔다 -->
				<li class="flex min-w-0 flex-col gap-2">
					<span class="text-[13px] whitespace-nowrap text-text-primary">
						{formatDayWithWeekday(item.date)}
					</span>
					<span class="text-sm font-bold">{formatMetricValue(metric, item.value)}</span>
					{#if item.inProgress}
						<span class="-mt-1 text-xs text-primary">수집 중</span>
					{/if}
				</li>
			{:else}
				<!-- 이어지는 기록 없는 날은 한 칸으로 묶는다. 이틀 이상이면 두 칸을 쓴다 -->
				<li class={['flex min-w-0 flex-col gap-2', item.count > 1 && 'col-span-2']}>
					<span class="text-[13px] text-text-primary">
						{item.count > 1
							? `${formatShortDate(item.start)} ~ ${formatShortDate(item.end)}`
							: formatDayWithWeekday(item.start)}
					</span>
					<span class="text-sm text-text-primary">
						{item.reason ? MISSING_REASON_LABELS[item.reason] : '기록 없음'}
						{item.count > 1 ? `· ${item.count}일` : ''}
					</span>
				</li>
			{/if}
		{/each}
	</ol>
{/snippet}

<!-- 좁은 칸의 값 한 쌍: 이름, 값, 아래에 날짜 같은 보조 글. 세 칸을 한 줄에 놓을 때 쓴다 -->
{#snippet miniStat(
	name: string,
	value: string,
	caption: string | null,
	tone: 'default' | 'primary' = 'default'
)}
	<div class="min-w-0">
		<dt class="text-[13px] font-bold text-text-primary">{name}</dt>
		<dd class={['mt-1 text-metric-sm whitespace-nowrap', tone === 'primary' && 'text-brand']}>
			{value}
		</dd>
		{#if caption}
			<dd class="mt-1 text-xs text-text-primary">{caption}</dd>
		{/if}
	</div>
{/snippet}

<!--
	날짜별 산소포화도 표(S15P31A403-404). 밤마다 두 줄이던 목록을 한 줄로 줄였다. "산소포화도 90% 미만"은
	줄마다 되풀이하지 않고 머리글에 한 번만 쓴다
-->
{#snippet spo2NightList(nights: ReturnType<typeof nightSpo2Items>)}
	<div class="rounded-2xl border border-border bg-surface">
		<table class="w-full text-sm" data-spo2-nights>
			<caption class="sr-only">날짜별 수면 중 산소포화도</caption>
			<thead>
				<tr class="border-b border-border text-left text-[13px] text-text-primary">
					<th scope="col" class="w-28 py-3 pr-2 pl-5 font-bold sm:w-44">날짜</th>
					<th scope="col" class="w-16 px-2 py-3 font-bold sm:w-32">평균</th>
					<th scope="col" class="py-3 pr-5 pl-2 font-bold">90% 미만</th>
				</tr>
			</thead>
			<tbody class="divide-y divide-border">
				{#each nights as night (night.key)}
					<tr class="align-top">
						<th scope="row" class="py-3 pr-2 pl-5 text-left font-semibold whitespace-nowrap">
							{formatDayWithWeekday(night.date)}
						</th>
						<td class="px-2 py-3 whitespace-nowrap tabular-nums">
							{night.average !== null
								? formatMetricValue('oxygen_saturations', night.average)
								: '-'}
						</td>
						<td class="py-3 pr-5 pl-2"><Spo2LowTimes state={night.state} bare /></td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/snippet}

{#snippet sessionList(items: SessionItem[])}
	<ol class="flex flex-col divide-y divide-border rounded-2xl border border-border bg-surface px-5">
		{#each items as item (item.start_at)}
			<li class="flex items-center justify-between gap-3 py-3">
				<div class="flex min-w-0 flex-col gap-1">
					<span class="text-[13px] text-text-primary">
						{formatDayWithWeekday(item.date)} · {formatClockTime(item.start_at)}
					</span>
					<span
						class={['truncate text-sm font-bold', !item.counts_as_exercise && 'text-text-primary']}
					>
						{item.title ?? item.type_name}
					</span>
				</div>
				<div class="flex shrink-0 items-center gap-2">
					{#if item.isLongest}
						<span class="rounded-full bg-primary-soft px-2 py-0.5 text-xs font-bold text-primary"
							>가장 길게</span
						>
					{:else if !item.counts_as_exercise}
						<span class="text-xs text-text-primary">운동 미인정</span>
					{/if}
					<span
						class={[
							'text-sm font-bold whitespace-nowrap',
							!item.counts_as_exercise && 'text-text-primary'
						]}
					>
						{formatMetricValue('exercises', item.duration_minutes)}
					</span>
				</div>
			</li>
		{/each}
	</ol>
{/snippet}

<!--
	창 안 오른쪽 스크롤바는 숨기고, 맨 위 막대가 내린 만큼 왼쪽부터 차오르게 해 전체 길이와 위치를 알린다.
	차트 안의 가장자리 흐림·팝업(z-20·30)이 막대 밑을 지나가며 막대 위에 그려져 막대가 중간에 잘려 보였다.
	차트는 isolate 로 자기 층에 가두고 막대는 z-30 으로 둔다(S15P31A403-438).
	스크롤 자체(휠·터치·키보드)는 그대로라 막대는 보조기기에 읽히지 않는 꾸밈이다.
-->
<dialog
	bind:this={dialog}
	class="m-auto max-h-[calc(100dvh-2rem)] w-[min(56rem,calc(100vw-2rem))] [scrollbar-width:none] overflow-x-hidden overflow-y-auto rounded-3xl bg-card p-0 break-keep text-text-primary backdrop:bg-text-primary/30 [&::-webkit-scrollbar]:hidden"
	aria-labelledby={`${tabId}-title`}
	onclose={() => (open = false)}
	onscroll={updateScrollProgress}
>
	{#if scrollProgress !== null}
		<div
			class="pointer-events-none sticky top-0 z-30 -mb-1.5 h-1.5 bg-primary-soft"
			aria-hidden="true"
			data-scroll-progress
		>
			<div class="h-full rounded-r-full bg-brand" style:width={`${scrollProgress * 100}%`}></div>
		</div>
	{/if}
	{#if activeKey}
		<div class="flex flex-col gap-6 px-[20px] py-6 sm:px-8 sm:py-8">
			<header class="flex items-start justify-between gap-4">
				<div>
					<!-- 누르는 요소가 아니므로 포커스 테두리는 보이지 않게 한다. Tab 을 누르면 닫기 버튼으로 간다 -->
					<h2
						id={`${tabId}-title`}
						data-dialog-title
						tabindex="-1"
						class="text-section-title outline-none"
					>
						참조 데이터 상세
					</h2>
					<p class="mt-2 text-[15px] text-text-secondary">
						AI 답변에 사용된 건강 기록을 확인할 수 있어요.
					</p>
				</div>
				<Button
					variant="ghost"
					size="icon"
					class="shrink-0 text-muted-foreground"
					aria-label="닫기"
					onclick={() => (open = false)}
				>
					<svg
						class="size-5"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						aria-hidden="true"
					>
						<path d="M6 6l12 12M18 6 6 18" stroke-linecap="round" />
					</svg>
				</Button>
			</header>

			{#if tabs.length > 1}
				<div class="flex flex-wrap gap-3" role="tablist" aria-label="지표">
					{#each tabs as tab, index (tab.key)}
						{@const isSelected = tab.key === activeKey}
						<button
							id={`${tabId}-tab-${index}`}
							type="button"
							role="tab"
							aria-selected={isSelected}
							aria-controls={`${tabId}-panel`}
							tabindex={isSelected ? 0 : -1}
							class={[
								'min-w-24 rounded-xl border px-6 py-3 text-sm font-bold transition-colors',
								isSelected
									? 'border-primary-soft bg-primary-soft text-primary'
									: 'border-border bg-card text-text-secondary hover:bg-surface'
							]}
							onclick={() => (selectedKey = tab.key)}
							onkeydown={(event) => handleTabKeydown(event, index)}
						>
							{tab.label}
						</button>
					{/each}
				</div>
			{/if}

			{#if selected}
				{@const snapshot = selected.current.snapshot}
				{@const label = METRIC_LABELS[selected.metric]}
				{@const cover = groupCoverage(selected)}
				{@const merged = isMergedGroup(selected)}
				{@const compareCover = selected.comparison ? coverage(selected.comparison.snapshot) : null}
				{@const bars = chartBars(snapshot)}
				{@const skipReason = chartSkipReason(snapshot)}
				{@const range = measuredRange(snapshot)}
				{@const stages = sleepStageAverages(snapshot)}
				{@const spo2Nights = nightSpo2Items(snapshot)}
				{@const spo2 = spo2Summary(snapshot)}
				<!--
					산소포화도는 요약을 한 상자에 모은다(S15P31A403-388, 앱 384와 같다): 상자 위에 지표 이름을 한 번 쓰고
					상자 안에 평균·최저·최고를 나란히 둔다. 따로 있던 "측정 최저·최고" 묶음과 "가장 높은 날·낮은 날" 칸은
					같은 내용이 반복되어 두지 않는다
				-->
				{@const compact = selected.metric === 'oxygen_saturations'}
				{@const todayOnly = cover.recordedDays === 0 && cover.today !== null}
				<!-- 비교 기간이 있어도 상자를 나누지 않고 한 줄에 평균·이전 기간 대비·최저·최고를 둔다(S15P31A403-404) -->
				{@const oneRow = compact && range !== null && !todayOnly}
				{@const sessions = sessionItems(snapshot)}
				{@const activity = exerciseActivityView(snapshot)}
				{@const items = dayListItems(snapshot.days)}
				{@const linkedOut = beforeLinkDays(snapshot)}
				<!-- 물은 기간이 통째로 앱 연동 전인지. "8월 10일 걸음 수"처럼 연동 전 하루만 물은 경우다(S15P31A403-439) -->
				{@const allBeforeLink = linkedOut > 0 && linkedOut === snapshot.days.length}
				{@const inProgress = snapshot.days.some((day) => day.is_in_progress)}
				<!--
					일별 차트는 처음에 일주일 보기다(S15P31A403-418). 창을 다시 열거나 탭·답변이 바뀌면 차트를 새로 만들어
					앞에서 고른 보기가 다른 차트로 따라오지 않게 한다. 같은 창에서 그 탭으로 돌아왔을 때만 chartViews 에
					기억해 둔 보기로 시작한다
				-->
				{@const chartKey = `${openCount}:${selected.metric}:${selected.current.id}`}
				<!--
					날짜별 기록 달력(S15P31A403-430). 걸음·운동·수면·심박은 기간 길이와 관계없이 이틀 이상이면 달력을 쓴다.
					네 지표 모두 그날 상세에만 있는 값이 있다(운동 기록, 산소포화도, 측정 최저·최고, 시간대별 걸음).
					처음에는 걸음만 15일 이상일 때 달력을 썼다. 날짜를 눌러도 더 보여 줄 값이 없어 짧은 기간은 목록이 한눈에 들어왔기 때문인데,
					438에서 시간대별 걸음이 달력 상세에 들어가면서 14일 이하 걸음 근거에서는 그 값을 볼 길이 없어졌다(S15P31A403-439).
					운동·수면은 그날 운동 기록과 산소포화도를 달력 상세에 넣어, 따로 있던 "운동 기록"·"수면 중 산소포화도" 묶음을 두지 않는다.
					달력을 쓰는 지표인지와 원의 기준이 있는지는 calendarAverage 가 정한다(산소포화도·신체 지표, 평균이 없거나 0이면 null)
				-->
				{@const calendarBase = calendarAverage(snapshot)}
				{@const showsCalendar = calendarBase !== null && hasAnyValue(cover) && cover.periodDays > 1}
				<div
					id={`${tabId}-panel`}
					class="flex flex-col gap-6"
					role={tabs.length > 1 ? 'tabpanel' : undefined}
				>
					<div class="flex flex-wrap items-end justify-between gap-4">
						<div>
							<p class="text-[13px] font-bold text-text-secondary">
								{cover.periodDays === 1 ? '하루 기록' : `${cover.periodDays}일 기간`}
							</p>
							<p class="mt-1 text-sm font-bold">{periodText(selected.current)}</p>
							{#if selected.comparison}
								<p class="mt-1 text-sm text-text-secondary">
									비교 기간 {periodText(selected.comparison)}
								</p>
							{/if}
							{#if merged}
								<p class="mt-1 text-sm text-text-secondary" data-evidence-split>
									{selected.parts.length}번 나눠 조회한 기록을 이어서 보여 줘요
								</p>
							{/if}
							<!--
								위 기간과 날짜가 겹치는 조회는 기간 구분에 섞지 않고 따로 적는다(S15P31A403-418).
								답변이 이 평균을 말할 수 있어 지우지는 않는다
							-->
							{#each selected.overlaps as overlap (overlap.id)}
								<p class="mt-1 text-sm text-text-secondary" data-evidence-overlap>
									함께 조회한 기간 {periodText(overlap)} · 평균 {formatMetricValue(
										selected.metric,
										overlap.snapshot.summary.avg
									)}
								</p>
							{/each}
						</div>
						<div class="text-[13px] sm:text-right">
							<!-- 다 기록된 기간은 "7일 / 7일"이 기간 표시와 같은 말이라 쓰지 않는다 -->
							{#if cover.periodDays > 1 && cover.recordedDays < cover.countedDays}
								<p class="font-bold text-text-secondary">
									기록된 날 · {cover.recordedDays}일 / {cover.countedDays}일
								</p>
							{/if}
							<p class="mt-1 text-text-secondary">출처 · 내 건강 기록 (CANDiY API)</p>
						</div>
					</div>

					{#if merged}
						<!--
							나눠 조회한 기간마다 AI 가 받은 평균을 그대로 보여 준다(S15P31A403-368). 전체 평균은 웹에서 다시 내지 않는다.
							"주별로 알려 줘" 같은 답변의 숫자를 여기서 바로 맞춰 볼 수 있다
						-->
						<section class="flex flex-col gap-3" data-evidence-detail="parts">
							<h3 class="text-card-title">조회한 기간별 평균</h3>
							<ol class="divide-y divide-border rounded-2xl border border-border bg-surface px-6">
								{#each selected.parts as part (part.id)}
									{@const partCover = coverage(part.snapshot)}
									<li class="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-3">
										<div class="flex min-w-0 flex-col gap-1">
											<span class="text-sm font-bold">{periodText(part)}</span>
											{#if partCover.recordedDays < partCover.countedDays}
												<span class="text-[13px] text-text-primary">
													{partCover.countedDays}일 중 {partCover.recordedDays}일 기록
												</span>
											{/if}
										</div>
										<span class="text-sm font-bold whitespace-nowrap">
											{formatMetricValue(selected.metric, part.snapshot.summary.avg)}
										</span>
									</li>
								{/each}
							</ol>
						</section>
					{:else if oneRow && range}
						<section class="flex flex-col gap-3" data-evidence-detail="spo2-summary">
							<h3 class="text-card-title">{label}</h3>
							<dl
								class={[
									'grid gap-3 rounded-2xl border border-border bg-surface px-6 py-5',
									selected.comparison ? 'grid-cols-2 sm:grid-cols-4' : 'grid-cols-3'
								]}
							>
								{@render miniStat(
									'평균',
									formatMetricValue(selected.metric, snapshot.summary.avg),
									null
								)}
								{#if selected.comparison && compareCover}
									<!-- 기록이 적어 믿기 어려운 비교는 강조색을 빼고 참고용이라고 붙인다(다른 지표의 비교 상자와 같은 기준) -->
									{@const isReference =
										delta !== null && !(cover.isReliable && compareCover.isReliable)}
									{@render miniStat(
										isReference ? '이전 기간 대비 · 참고용' : '이전 기간 대비',
										delta === null ? '-' : formatMetricDelta(selected.metric, delta),
										isReference
											? cover.periodDays === 1 && compareCover.periodDays === 1
												? '하루 기록끼리 비교했어요'
												: '기록이 적은 기간이 섞여 있어요'
											: compareCover.recordedDays === 0
												? '이전 기간 기록 없음'
												: null,
										isReference ? 'default' : 'primary'
									)}
								{/if}
								{@render miniStat(
									'최저',
									formatMetricValue(selected.metric, range.min?.value ?? null),
									range.min ? formatDayWithWeekday(range.min.date) : null
								)}
								{@render miniStat(
									'최고',
									formatMetricValue(selected.metric, range.max?.value ?? null),
									range.max ? formatDayWithWeekday(range.max.date) : null
								)}
							</dl>
						</section>
					{:else if hasAnyValue(cover) || selected.comparison}
						<!-- 기록이 없고 비교도 없으면 "-" 카드 대신 아래 안내만 둔다 -->
						{#if compact}<h3 class="-mb-3 text-card-title">{label}</h3>{/if}
						<dl
							class="flex flex-wrap gap-x-4 gap-y-4 rounded-2xl border border-border bg-surface px-6 py-5 sm:grid sm:grid-cols-2 sm:gap-x-6"
						>
							<div class="min-w-0">
								<!-- 지난날 기록 없이 오늘 수집 중 값만 있으면("오늘 몇 걸음?") 평균 "-" 대신 그 값을 보여 준다 -->
								{#if cover.recordedDays === 0 && cover.today}
									{@render stat(
										`오늘 ${DAY_LABELS[selected.metric]}`,
										formatMetricValue(selected.metric, cover.today.value),
										cover.today.asOf
											? `${formatClockTime(cover.today.asOf)} 기준 · 수집 중`
											: '수집 중'
									)}
								{:else}
									{@render stat(
										statTitle(selected.current, cover, AVERAGE_LABELS[selected.metric]),
										formatMetricValue(selected.metric, snapshot.summary.avg),
										cover.recordedDays === 0 && cover.periodDays > 1 ? '기록 없음' : null
									)}
								{/if}
							</div>
							{#if selected.comparison && compareCover}
								<!-- 이전 기간 평균 칸은 두지 않고 차이만 보여 준다(S15P31A403-393). 비교 기간은 머리말에 있다. -->
								<!-- 두 칸은 가로로 나란히 둔다. 넓은 화면은 반씩 나누고, 좁은 화면은 글자 폭만큼만 차지해 휴대폰에서도 한 줄에 들어간다 -->
								<!-- AI와 같은 기준(3일 이상·절반 이상)에 못 미치면 차이가 크게 보여도 믿기 어려워 강조색을 빼고 참고용이라고 붙인다 -->
								{@const isReference =
									delta !== null && !(cover.isReliable && compareCover.isReliable)}
								<div class="min-w-0">
									{@render stat(
										isReference ? '이전 기간 대비 · 참고용' : '이전 기간 대비',
										delta === null ? '-' : formatMetricDelta(selected.metric, delta),
										isReference
											? cover.periodDays === 1 && compareCover.periodDays === 1
												? '하루 기록끼리 비교했어요'
												: '기록이 적은 기간이 섞여 있어요'
											: compareCover.recordedDays === 0
												? '이전 기간 기록 없음'
												: null,
										isReference ? 'muted' : 'primary'
									)}
								</div>
							{/if}
						</dl>
						{#if compact && range}
							<!-- 지난날 기록 없이 오늘 값만 있을 때는 위 상자가 "오늘 값"이라, 최저·최고는 아래에 한 줄로 따로 둔다 -->
							<dl
								class="-mt-3 grid grid-cols-2 gap-3 rounded-2xl border border-border bg-surface px-6 py-5"
							>
								{@render miniStat(
									'최저',
									formatMetricValue(selected.metric, range.min?.value ?? null),
									range.min ? formatDayWithWeekday(range.min.date) : null
								)}
								{@render miniStat(
									'최고',
									formatMetricValue(selected.metric, range.max?.value ?? null),
									range.max ? formatDayWithWeekday(range.max.date) : null
								)}
							</dl>
						{/if}
					{/if}

					{#if showsChart(snapshot)}
						{@const title =
							bars.unit === 'week'
								? CHART_TITLES[selected.metric].replace(
										'일별',
										BODY_METRICS.includes(selected.metric) ? '주별 마지막 측정' : '주별 평균'
									)
								: CHART_TITLES[selected.metric]}
						<section class="flex flex-col gap-3">
							<div class="flex items-center justify-between">
								<h3 class="text-card-title">{title}</h3>
								<p class="text-[13px] text-text-secondary">단위 · {UNIT_LABELS[selected.metric]}</p>
							</div>
							{#key chartKey}
								{#if isLineChart(selected.metric)}
									<DailyLineChart
										metric={selected.metric}
										bars={bars.bars}
										daily={bars.unit === 'day'}
										average={!merged && cover.recordedDays >= MIN_DAYS_FOR_TREND
											? snapshot.summary.avg
											: null}
										label={`${title} 꺾은선 차트`}
										bind:view={
											() => chartViews[chartKey] ?? 'week',
											(value) => (chartViews[chartKey] = value)
										}
									/>
								{:else}
									{@const weekly = bars.unit === 'week' ? weekExtremes(bars.bars) : null}
									{@const previousAverage = selected.comparison?.snapshot.summary.avg ?? null}
									<DailyBarChart
										metric={selected.metric}
										bars={bars.bars}
										daily={bars.unit === 'day'}
										average={!merged && cover.recordedDays >= MIN_DAYS_FOR_TREND
											? snapshot.summary.avg
											: null}
										extremes={weekly ??
											(bars.unit === 'day' && !merged ? chartExtremes(snapshot) : null)}
										previous={previousAverage !== null && delta !== null && compareCover
											? {
													average: previousAverage,
													delta: formatMetricDelta(selected.metric, delta),
													reference: !(cover.isReliable && compareCover.isReliable)
												}
											: null}
										label={`${title} 막대 차트`}
										bind:view={
											() => chartViews[chartKey] ?? 'week',
											(value) => (chartViews[chartKey] = value)
										}
									/>
								{/if}
							{/key}
						</section>
					{:else if skipReason}
						<!-- 값은 있는데 차트가 없을 때 빠진 게 아니라는 걸 알린다(하루 기간·기록 하루·모두 0) -->
						<p class="text-sm text-text-secondary" data-chart-skip>{skipReason}</p>
					{:else if !hasAnyValue(cover)}
						<p
							class="rounded-2xl border border-border bg-surface px-6 py-8 text-center text-sm break-keep text-text-primary"
						>
							{#if allBeforeLink}
								<!-- 전에는 "이 기간에는 기록이 없어요" 아래에 "1일은 앱 연동 전이라 기간에서 뺐어요"가 따로 붙어, 하루를 물었는데 그 하루를 뺐다는 말이 되었다 -->
								앱을 연동하기 전이라 {label} 기록이 없어요.
							{:else}
								이 기간에는 {label} 기록이 없어요.
								{#if snapshot.summary.unsynced_days.length > 0}
									<br />아직 동기화되지 않은 날이 있어요. 앱을 열어 동기화해 주세요.
								{/if}
							{/if}
						</p>
					{/if}

					<!-- 답변이 하루 평균 말고 측정 최고·수면 단계·운동 기록을 말해도 근거에서 찾을 수 있게 한다(248) -->
					{#if range && !compact}
						<section class="flex flex-col gap-3" data-evidence-detail="measured">
							<h3 class="text-card-title">측정 최저·최고</h3>
							<dl
								class="grid grid-cols-2 gap-4 rounded-2xl border border-border bg-surface px-6 py-5"
							>
								{#each [['측정 최저', range.min] as const, ['측정 최고', range.max] as const] as [name, point] (name)}
									<div class="min-w-0">
										<dt class="text-[13px] font-bold text-text-primary">
											{name}{point ? ` · ${formatDayWithWeekday(point.date)}` : ''}
										</dt>
										<dd class="mt-1 text-metric-sm whitespace-nowrap">
											{formatMetricValue(selected.metric, point?.value ?? null)}
										</dd>
									</div>
								{/each}
							</dl>
							<p class="text-[13px] text-text-secondary">{measuredNote(cover.periodDays)}</p>
						</section>
					{/if}

					<!--
						하루 수면 기록에는 달력이 없어 잠든·깬 시각을 볼 곳이 없었다. 답변은 "밤 11시 37분에 잠들어 7시 19분에 깼다"고
						말하는데 창에서 찾을 수 없었다(S15P31A403-439). 여러 날이면 달력의 그날 상세에 있다
					-->
					{#if selected.metric === 'sleeps' && !showsCalendar && snapshot.detail.nights.length === 1}
						{@const night = snapshot.detail.nights[0]}
						<section class="flex flex-col gap-3" data-evidence-detail="sleep-time">
							<h3 class="text-card-title">잠든·깬 시각</h3>
							<dl
								class="grid grid-cols-2 gap-4 rounded-2xl border border-border bg-surface px-6 py-5"
							>
								{#each [['잠든 시각', night.start_at] as const, ['깬 시각', night.end_at] as const] as [name, time] (name)}
									<div class="min-w-0">
										<dt class="text-[13px] font-bold text-text-primary">{name}</dt>
										<dd class="mt-1 text-metric-sm whitespace-nowrap">{formatClockTime(time)}</dd>
									</div>
								{/each}
							</dl>
						</section>
					{/if}

					{#if stages.length > 0}
						{@const counted = Math.max(...stages.map((stage) => stage.nights))}
						{@const skipped = snapshot.detail.nights.length - counted}
						<section class="flex flex-col gap-3" data-evidence-detail="sleep-stages">
							<!-- 단계 기록이 하루뿐이면 평균이 아니라 그날 값이라 "평균"을 붙이지 않는다(S15P31A403-420) -->
							<h3 class="text-card-title">{counted === 1 ? '수면 단계' : '수면 단계 평균'}</h3>
							<dl
								class="grid grid-cols-2 gap-4 rounded-2xl border border-border bg-surface px-6 py-5 sm:grid-cols-4"
							>
								{#each stages as stage (stage.label)}
									<div class="min-w-0">
										<dt class="text-[13px] font-bold text-text-primary">{stage.label}</dt>
										<dd class="mt-1 text-metric-sm whitespace-nowrap">
											{formatMetricValue('sleeps', stage.minutes)}
										</dd>
									</div>
								{/each}
							</dl>
							<p class="text-[13px] text-text-secondary">
								{stageNote(cover.periodDays, counted, skipped)}
							</p>
						</section>
						<!--
							날마다 단계가 어땠는지 쌓은 막대로 보여 준다(S15P31A403-404). 하루뿐이면 두지 않는다.
							위 차트가 주 단위로 묶이는 긴 기간에도 둔다(S15P31A403-418). 최근 7일부터 가로로 넘겨 보므로 길어도 읽을 수 있다.
							다른 차트·목록과 같은 묶음 제목(text-card-title)을 쓴다(S15P31A403-412). 처음에는 "수면 단계 평균" 아래의
							작은 제목(14px)으로 두었는데, 기준에 없는 제목 단계라 옆 묶음과 크기가 달라 보였다
						-->
						{#if counted >= MIN_DAYS_FOR_TREND}
							<section class="flex flex-col gap-3" data-evidence-detail="sleep-stage-days">
								<h3 class="text-card-title">날짜별 수면 단계</h3>
								{#key chartKey}
									<SleepStageBars
										bars={bars.unit === 'day' ? bars.bars : dayBars(snapshot)}
										nights={snapshot.detail.nights}
										label="날짜별 수면 단계 막대 차트"
										bind:view={
											() => chartViews[`${chartKey}:stages`] ?? 'week',
											(value) => (chartViews[`${chartKey}:stages`] = value)
										}
									/>
								{/key}
							</section>
						{/if}
					{/if}

					{#if spo2Nights.length > 0 && !showsCalendar}
						<!--
							날짜별 산소포화도 90% 미만(S15P31A403-312). 날짜는 창의 다른 곳과 같은 기록 날짜(깬 날)로 쓴다(S15P31A403-420).
							418에서 "밤"을 떼고 잠든 날짜만 남겼더니, 10.27 하루 기록인데 여기만 10월 26일이라 다른 날처럼 읽혔다
						-->
						<section class="flex flex-col gap-3" data-evidence-detail="spo2-low">
							<h3 class="text-card-title">수면 중 산소포화도</h3>
							{#if spo2}
								<!-- 밤이 많으면(한 달 등) 평균·최저·최고를 먼저 보여 주고, 날짜별 목록은 날짜별 전체 보기처럼 접어 둔다 -->
								<dl
									class="grid grid-cols-3 gap-3 rounded-2xl border border-border bg-surface px-6 py-5"
									data-spo2-summary
								>
									{@render miniStat(
										'평균',
										formatMetricValue('oxygen_saturations', spo2.average),
										null
									)}
									{@render miniStat(
										'최저',
										formatMetricValue('oxygen_saturations', spo2.lowest?.value ?? null),
										spo2.lowest ? formatDayWithWeekday(spo2.lowest.date) : null
									)}
									{@render miniStat(
										'최고',
										formatMetricValue('oxygen_saturations', spo2.highest?.value ?? null),
										spo2.highest ? formatDayWithWeekday(spo2.highest.date) : null
									)}
								</dl>
								<p class="text-[13px] text-text-secondary">{spo2SummaryNote(spo2.lowNights)}</p>
								<details>
									<summary
										class="cursor-pointer rounded-lg py-1 text-sm font-bold text-text-secondary hover:text-text-primary"
									>
										날짜별 전체 보기 ({spo2.nights}일)
									</summary>
									<div class="mt-3">{@render spo2NightList(spo2Nights)}</div>
								</details>
							{:else}
								{@render spo2NightList(spo2Nights)}
							{/if}
						</section>
					{/if}

					<!-- 운동 횟수·칼로리·거리(S15P31A403-375). 답변이 운동 시간 말고 이 값을 말해도 근거에서 찾을 수 있다 -->
					{#if activity && !showsCalendar}
						<ExerciseActivitySection view={activity} />
					{/if}

					{#if sessions.length > 0 && !showsCalendar}
						<section class="flex flex-col gap-3" data-evidence-detail="sessions">
							<h3 class="text-card-title">운동 기록 · {sessions.length}개</h3>
							{@render sessionList(sessions.slice(0, MAX_LISTED_SESSIONS))}
							{#if sessions.length > MAX_LISTED_SESSIONS}
								<details>
									<summary
										class="cursor-pointer rounded-lg py-1 text-sm font-bold text-text-secondary hover:text-text-primary"
									>
										나머지 {sessions.length - MAX_LISTED_SESSIONS}개 보기
									</summary>
									<div class="mt-3">
										{@render sessionList(sessions.slice(MAX_LISTED_SESSIONS))}
									</div>
								</details>
							{/if}
							{#if sessions.some((session) => !session.counts_as_exercise)}
								<p class="text-[13px] text-text-secondary">
									운동으로 인정되지 않은 기록은 운동 시간에서 뺐어요.
								</p>
							{/if}
						</section>
					{/if}

					<!-- 하루 기간은 위 카드와 같은 값 하나뿐이라 목록을 두지 않는다 -->
					{#if hasAnyValue(cover) && cover.periodDays > 1}
						<section class="flex flex-col gap-3">
							<h3 class="text-card-title">날짜별 기록</h3>
							{#if listsAllDays(snapshot) && !showsCalendar}
								{@render dayList(items, selected.metric)}
							{:else}
								<!--
									달력이 있으면 이 상자를 두지 않는다(S15P31A403-435). 최고·최저 날은 원 가운데 체크로, 오늘(수집 중)은 노란 원으로,
									기록 없는 날은 점선 원으로 달력에 이미 보인다
								-->
								{#if !listsAllDays(snapshot) && !showsCalendar}
									<!-- 긴 기간은 가장 높은·낮은 날과 기록 없는 날을 먼저 보여 준다. 산소포화도는 위 요약·차트와 겹쳐 접는 줄만 둔다 -->
									<ul
										class={[
											'grid grid-cols-2 gap-4 rounded-2xl border border-border bg-surface px-5 py-5 sm:grid-cols-3',
											compact && 'hidden'
										]}
									>
										{#each dayHighlights(snapshot) as highlight (highlight.label)}
											<li class="flex min-w-0 flex-col gap-1">
												<span class="text-[13px] text-text-primary">
													{highlight.label} · {formatDayWithWeekday(highlight.date)}
												</span>
												<span class="text-sm font-bold">
													{formatMetricValue(selected.metric, highlight.value)}
												</span>
											</li>
										{/each}
										{#if cover.countedDays > cover.recordedDays}
											<li class="flex min-w-0 flex-col gap-1">
												<span class="text-[13px] text-text-primary">기록 없는 날</span>
												<span class="text-sm font-bold">
													{cover.countedDays - cover.recordedDays}일
												</span>
											</li>
										{/if}
									</ul>
								{/if}
								{#if showsCalendar && calendarBase}
									<!-- 다른 답변의 근거로 바뀌면 고른 달·날을 처음부터 다시 잡는다 -->
									{#key `${selected.metric}:${selected.current.id}`}
										<DayCalendar
											{snapshot}
											metric={selected.metric}
											average={calendarBase.value}
											basis={calendarBase.basis}
										/>
									{/key}
								{:else}
									<details class="group">
										<summary
											class="cursor-pointer rounded-lg py-1 text-sm font-bold text-text-secondary hover:text-text-primary"
										>
											날짜별 전체 보기 ({cover.periodDays}일)
										</summary>
										<div class="mt-3">{@render dayList(items, selected.metric)}</div>
									</details>
								{/if}
							{/if}
						</section>
					{/if}

					<!--
						아래 안내는 숫자를 읽는 데 필요한 것만 둔다. "선택한 기간의 ○○ 기록이에요"는 위 머리말·탭과 같은 말이라 뺐다(S15P31A403-438 피드백)
					-->
					<div class="flex flex-col gap-1 text-[13px] text-text-secondary">
						{#if merged}
							<p>전체 평균은 따로 계산하지 않고, 조회한 기간별 평균을 그대로 보여 줘요.</p>
						{/if}
						<!-- 기록된 날 수는 머리말·날짜 목록에 있으니 여기서는 평균 계산 방식만 알린다 -->
						{#if cover.recordedDays > 1 && cover.recordedDays < cover.countedDays}
							<p>기록 없는 날은 0으로 치지 않고 평균에서 뺐어요.</p>
						{/if}
						<!-- 오늘 하루 기간은 위 카드에 "수집 중"이 있고 평균도 없어 쓰지 않는다 -->
						{#if inProgress && cover.periodDays > 1}
							<p>오늘 기록은 수집 중이며 기간 평균에서 제외해요.</p>
						{/if}
						{#if linkedOut > 0 && !allBeforeLink}
							<p>{linkedOut}일은 앱 연동 전이라 기간에서 뺐어요.</p>
						{/if}
						{#if bars.unit === 'week' && showsChart(snapshot)}
							<p>
								{BODY_METRICS.includes(selected.metric)
									? '기간이 길어 차트는 7일씩 묶어 그 주 마지막 측정값으로 보여 줘요.'
									: '기간이 길어 차트는 7일씩 묶은 하루 평균으로 보여 줘요.'}
							</p>
						{/if}
					</div>
				</div>
			{:else if selectedKind}
				<div
					id={`${tabId}-panel`}
					class="flex flex-col gap-6"
					role={tabs.length > 1 ? 'tabpanel' : undefined}
				>
					<KindEvidencePanel evidence={selectedKind} />
				</div>
			{/if}

			<div class="flex justify-end">
				<Button variant="outline" onclick={() => (open = false)}>닫기</Button>
			</div>
		</div>
	{/if}
</dialog>
