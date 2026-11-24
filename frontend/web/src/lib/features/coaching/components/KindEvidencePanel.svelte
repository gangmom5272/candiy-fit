<script lang="ts">
	import {
		formatEvidencePeriod,
		hourlyColumns,
		sleepSessionsView,
		spo2LowState
	} from '../evidence-view';
	import {
		formatClockTime,
		formatDayWithWeekday,
		formatMetricValue,
		formatSleepLabel
	} from '../format';
	import type { KindEvidence } from '../types';
	import Spo2LowTimes from './Spo2LowTimes.svelte';

	/**
	 * 모양이 다른 근거(snapshot.kind) 한 건을 근거 상세 창 안에 그린다(S15P31A403-312).
	 * - sleep_sessions: 낮잠·다시 잠든 수면까지 모든 세션. 주 수면 평균은 is_main 만으로 낸다
	 * - hourly_steps: 날짜 × 시간대 걸음 표
	 * 제목·기간·출처 줄은 날짜별 지표 패널과 같은 자리·글자를 쓴다
	 */
	let { evidence }: { evidence: KindEvidence } = $props();

	const snapshot = $derived(evidence.snapshot);
</script>

<div class="flex flex-wrap items-end justify-between gap-4">
	<div>
		<p class="text-[13px] font-bold text-text-secondary">
			{snapshot.kind === 'sleep_sessions' ? '수면 기록' : '시간대별 걸음'}
		</p>
		<p class="mt-1 text-sm font-bold">
			{formatEvidencePeriod(evidence.periodStart, evidence.periodEnd)}
		</p>
	</div>
	<p class="text-[13px] text-text-secondary sm:text-right">출처 · 내 건강 기록 (CANDiY API)</p>
</div>

{#if snapshot.kind === 'sleep_sessions'}
	{@const view = sleepSessionsView(snapshot)}
	{#if view.sessions.length === 0}
		<p
			class="rounded-2xl border border-border bg-surface px-6 py-8 text-center text-sm text-text-primary"
		>
			이 기간에는 수면 기록이 없어요.
		</p>
	{:else}
		<dl
			class="grid grid-cols-1 gap-4 rounded-2xl border border-border bg-surface px-6 py-5 sm:grid-cols-2"
		>
			<div>
				<dt class="text-[13px] font-bold text-text-primary">주 수면 평균</dt>
				<dd class="mt-2 text-metric">{formatMetricValue('sleeps', view.mainAverage)}</dd>
			</div>
			<div>
				<dt class="text-[13px] font-bold text-text-primary">수면 기록</dt>
				<dd class="mt-2 text-metric">{view.sessions.length}회</dd>
				{#if view.extraCount > 0}
					<dd class="mt-1 text-xs text-text-primary">
						주 수면 {view.mainCount}회 · 추가 수면 {view.extraCount}회
					</dd>
				{/if}
			</div>
		</dl>

		<section class="flex flex-col gap-3">
			<h3 class="text-card-title">수면 기록</h3>
			<ol
				class="flex flex-col divide-y divide-border rounded-2xl border border-border bg-surface px-5"
			>
				{#each view.sessions as session (session.session_id)}
					{@const spo2 = spo2LowState(session.spo2_low_count, session.spo2_low_times)}
					<li class="flex flex-col gap-2 py-4">
						<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
							<p class="text-sm font-bold">
								{formatSleepLabel(session.start_at)}
								{#if !session.is_main}
									<span class="font-semibold text-text-primary"> · 추가 수면</span>
								{/if}
							</p>
							<p class="text-sm font-bold whitespace-nowrap">
								{formatMetricValue('sleeps', session.sleep_minutes)}
							</p>
						</div>
						<p class="text-[13px] text-text-primary">
							{formatClockTime(session.start_at)} ~ {formatClockTime(session.end_at)}
						</p>
						{#if spo2}
							<Spo2LowTimes state={spo2} />
						{/if}
					</li>
				{/each}
			</ol>
		</section>
	{/if}

	<div class="flex flex-col gap-1 text-[13px] text-text-secondary">
		<p>낮잠과 자다 깨서 다시 잔 수면까지 모두 보여 줘요.</p>
		<p>밤·아침·낮은 잠든 시각 기준이에요. 주 수면 평균에는 추가 수면을 넣지 않아요.</p>
	</div>
{:else}
	{@const columns = hourlyColumns(snapshot)}
	<dl class="rounded-2xl border border-border bg-surface px-6 py-5">
		<dt class="text-[13px] font-bold text-text-primary">
			{columns.length > 0 ? `${columns[0]}시~${snapshot.end_hour}시 걸음 합계` : '걸음 합계'}
		</dt>
		<dd class="mt-2 text-metric">{formatMetricValue('steps', snapshot.total)}</dd>
	</dl>

	<section class="flex flex-col gap-3">
		<h3 class="text-card-title">날짜별 시간대 걸음</h3>
		<!-- 시간대가 많으면 표만 가로로 넘긴다(창 전체는 넘치지 않는다). 스크롤 영역이라 키보드로도 밀 수 있게 포커스를 받는다 -->
		<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
		<div
			class="overflow-x-auto rounded-2xl border border-border bg-surface"
			role="region"
			aria-label="시간대 걸음 표"
			tabindex="0"
		>
			<table class="w-full min-w-max text-sm">
				<caption class="sr-only">날짜별 시간대 걸음 수</caption>
				<thead>
					<tr class="border-b border-border text-[13px] text-text-primary">
						<th scope="col" class="px-4 py-3 text-left font-bold">날짜</th>
						{#each columns as hour (hour)}
							<th scope="col" class="px-3 py-3 text-right font-bold whitespace-nowrap">{hour}시</th>
						{/each}
						<th scope="col" class="px-4 py-3 text-right font-bold">합계</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-border">
					{#each snapshot.days as day (day.date)}
						<tr>
							<th scope="row" class="px-4 py-3 text-left font-semibold whitespace-nowrap">
								{formatDayWithWeekday(day.date)}
								{#if day.is_in_progress}
									<span class="ml-1 text-xs font-normal text-primary">수집 중</span>
								{/if}
							</th>
							{#if day.values}
								{#each columns as hour, index (hour)}
									<td class="px-3 py-3 text-right whitespace-nowrap tabular-nums">
										{day.values[index] == null ? '-' : day.values[index].toLocaleString('ko-KR')}
									</td>
								{/each}
							{:else}
								<td colspan={columns.length} class="px-3 py-3 text-center text-text-primary">
									기록 없음
								</td>
							{/if}
							<td class="px-4 py-3 text-right font-bold whitespace-nowrap tabular-nums">
								{day.total === null ? '-' : formatMetricValue('steps', day.total)}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>

	<!--
		기간 합계는 백엔드가 낸다. 오늘(진행 중) 걸음도 들어가고, 하루라도 기록이 없으면 null 이다(날짜별 합계는 있어도).
		웹에서 다시 더하지 않고 왜 비었는지만 알린다(S15P31A403-312 백엔드 답변)
	-->
	<div class="flex flex-col gap-1 text-[13px] text-text-secondary">
		{#if snapshot.total === null && snapshot.days.some((day) => day.total !== null)}
			<p>기록 없는 날이 있어 합계를 내지 않았어요.</p>
		{/if}
		{#if snapshot.days.some((day) => day.is_in_progress)}
			<p>
				{snapshot.total === null
					? '오늘 기록은 수집 중이에요.'
					: '오늘 기록은 수집 중이며, 지금까지 걸음도 합계에 들어가요.'}
			</p>
		{/if}
	</div>
{/if}
