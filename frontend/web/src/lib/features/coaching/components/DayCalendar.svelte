<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import {
		calendarExtremes,
		calendarMonths,
		calendarStart,
		calendarWeeks,
		compareToAverage,
		donutDash,
		donutFill,
		dailyRangeOf,
		exerciseActivityView,
		highlightLabels,
		hasLowSpo2,
		MISSING_REASON_LABELS,
		nightSpo2Items,
		sessionItems
	} from '../evidence-view';
	import { formatClockTime, formatMetricValue } from '../format';
	import { createHourlyStepsLoader, hourlyRange } from '../hourly-steps';
	import type { CalendarBasis } from '../evidence-view';
	import type { EvidenceSnapshot, HealthMetric } from '../types';
	import HourlyStepsMini from './HourlyStepsMini.svelte';
	import Spo2LowTimes from './Spo2LowTimes.svelte';

	/**
	 * 날짜별 기록 달력(S15P31A403-430). 긴 기간에서 "날짜별 전체 보기" 목록을 대신한다.
	 * 칸에는 숫자 대신 기간 평균 대비 도넛을 그리고, 날짜를 누르면 옆(휴대폰 폭은 아래)에 그날 값을 보여 준다.
	 * 운동은 그날 운동 기록과 횟수·칼로리·거리, 수면은 그날 산소포화도를 함께 보여 준다.
	 * 원 기준(average)은 AI 가 받은 기간 평균이다. 나눠 조회해 합친 기간과 운동(운동한 날만)은 화면에서 낸 평균이고 basis 로 알린다
	 */
	let {
		snapshot,
		metric,
		average,
		basis = 'summary'
	}: {
		snapshot: EvidenceSnapshot;
		metric: HealthMetric;
		average: number;
		/** 원 기준이 어디서 왔는지(evidence-view.ts calendarAverage) */
		basis?: CalendarBasis;
	} = $props();

	const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'] as const;
	/** activityRow.cells 순서(types.ts EXERCISE_ACTIVITY_FIELDS) */
	const ACTIVITY_NAMES = ['횟수', '칼로리', '거리'] as const;
	/**
	 * 활동 탭의 시간대별 걸음. 달 단위로 불러와 쌓아 두어 같은 달 안에서는 날짜를 바꿔도 다시 부르지 않는다(S15P31A403-439).
	 * 화면에 그리는 값이 아니라 reactive 가 아니어도 된다
	 */
	const hourlyLoader = createHourlyStepsLoader();
	onDestroy(() => hourlyLoader.dispose());
	/**
	 * 달력에 올리는 날. 앱 연동 전 날짜는 기록이 생길 수 없어 뺀다(차트도 뺀다, evidence-view chartDays).
	 * 전에는 연동 전 날도 눌리는 칸이라, 6개월 기간이면 "연동 전"만 나오는 빈 달을 석 달 넘겨야 했다(S15P31A403-439).
	 * 연동한 달의 연동 전 날짜는 기간 밖 날과 같이 흐린 숫자로만 남는다
	 */
	const days = $derived(snapshot.days.filter((day) => day.reason !== 'BEFORE_LINK'));
	/** 시간대별 걸음을 부를 수 있는 범위. 달력에 올린 첫날과 마지막 날이다 */
	const hourlyPeriod = $derived.by(() => {
		const dates = days.map((day) => day.date).sort();
		return { start: dates[0] ?? '', end: dates.at(-1) ?? '' };
	});
	const months = $derived(calendarMonths(days));
	const sessions = $derived(sessionItems(snapshot));
	const activity = $derived(exerciseActivityView(snapshot));
	const spo2Nights = $derived(nightSpo2Items(snapshot));
	const inProgress = $derived(snapshot.days.some((day) => day.is_in_progress));
	/** 두 바퀴째의 끝. AI 가 받은 기간 최고(daily_max) */
	const extremes = $derived(calendarExtremes(snapshot));
	const max = $derived(extremes.max?.value ?? null);
	/** 운동은 쉰 날을 뺀 운동한 날 평균이 기준이다(S15P31A403-435) */
	const baseName = $derived(basis === 'active-days' ? '운동한 날 평균' : '하루 평균');

	/**
	 * 칸·상세에 쓸 그날 값. 값이 없으면 null(점선 원, "기록 없음").
	 * 운동 0분인 날은 기록 없는 날과 구분하지 않는다(S15P31A403-435). 오늘(수집 중)은 아직 모이는 중이라 0분이어도 값으로 둔다
	 */
	function shownValue(day: EvidenceSnapshot['days'][number]): number | null {
		if (day.missing || day.value === null) return null;
		return metric === 'exercises' && day.value === 0 && !day.is_in_progress ? null : day.value;
	}
	/** 안내 팝업의 보기 도넛. 기간 최고가 평균보다 높을 때만 두 바퀴째 보기를 둔다 */
	const samples = $derived<[number | null, string][]>([
		[average * 0.6, '평균 미만'],
		[average, '평균'],
		...(max !== null && max > average
			? ([
					[(average + max) / 2, '평균 초과'],
					[max, '기간 최고']
				] as [number, string][])
			: []),
		[null, '기록 없음']
	]);

	/** 처음에는 마지막으로 기록이 있는 날과 그 달을 보여 준다 */
	const start = $derived(calendarStart(days));
	let chosenMonth: string | null = $state(null);
	let chosenDate: string | null = $state(null);
	/** 고른 달이 이 기간에 없으면(다른 답변의 근거로 바뀐 경우 등) 처음 달로 돌아간다 */
	const monthIndex = $derived.by(() => {
		const chosen = chosenMonth ? months.indexOf(chosenMonth) : -1;
		return chosen >= 0 ? chosen : Math.max(0, months.indexOf(start?.month ?? ''));
	});
	const month = $derived(months[monthIndex]);
	const weeks = $derived(month ? calendarWeeks(month, days) : []);
	/** 보고 있는 달의 시간대별 걸음을 미리 불러 둔다. 날짜를 누를 때는 이미 와 있다(S15P31A403-439). 실패는 날짜를 눌렀을 때 알린다 */
	$effect(() => {
		if (metric !== 'steps' || !month || !hourlyPeriod.start) return;
		hourlyLoader.load(hourlyRange(`${month}-01`, hourlyPeriod)).catch(() => {});
	});
	const selected = $derived(
		days.find((day) => day.date === chosenDate) ??
			days.find((day) => day.date === start?.date) ??
			null
	);

	let help: HTMLDialogElement | undefined = $state();
	const helpId = $props.id();

	let previousButton: HTMLButtonElement | undefined = $state();
	let nextButton: HTMLButtonElement | undefined = $state();

	/**
	 * 달 넘기기. 첫 달·마지막 달에서는 그쪽 버튼이 감춰져, 키보드로 누르던 포커스가 사라졌다.
	 * 끝에 닿으면 반대쪽 버튼으로 포커스를 옮긴다(S15P31A403-438 점검)
	 */
	async function moveMonth(step: number) {
		const hadFocus = document.activeElement === (step < 0 ? previousButton : nextButton);
		chosenMonth = months[monthIndex + step] ?? chosenMonth;
		await tick();
		const atEnd = step < 0 ? monthIndex === 0 : monthIndex === months.length - 1;
		if (hadFocus && atEnd) (step < 0 ? nextButton : previousButton)?.focus();
	}

	function monthTitle(value: string): string {
		const [year, monthNumber] = value.split('-').map(Number);
		// 해가 바뀌는 기간만 연도를 붙인다
		return months[0]?.slice(0, 4) === months.at(-1)?.slice(0, 4)
			? `${monthNumber}월`
			: `${year}년 ${monthNumber}월`;
	}

	function weekdayOf(date: string): number {
		return new Date(`${date}T00:00:00Z`).getUTCDay();
	}

	/**
	 * 기간의 최고·최저 날은 따로 상자에 적지 않고 그날 원 가운데 체크로 표시한다.
	 * 수면에서 시작해(S15P31A403-430) 모든 달력 탭으로 넓혔다(S15P31A403-435).
	 * AI 가 받은 기간 최고·최저 날(daily_max·daily_min)이고, 두 날이 같으면 최고 하나만 둔다.
	 * 최저가 0이면(운동을 안 한 날) 여러 날 가운데 하나만 가리키게 되어 표시하지 않는다. 차트 꼬리표와 같은 규칙이다
	 */
	const extremeDates = $derived({
		max: extremes.max?.date ?? null,
		min: extremes.min?.date ?? null
	});
	const [longestLabel, shortestLabel] = $derived(highlightLabels(metric));

	function markOf(date: string): 'max' | 'min' | null {
		if (extremeDates?.max === date) return 'max';
		return extremeDates?.min === date ? 'min' : null;
	}

	function markLabel(mark: 'max' | 'min' | null): string | null {
		return mark === 'max' ? longestLabel : mark === 'min' ? shortestLabel : null;
	}

	function cellLabel(date: string, value: number | null, missingText: string | null): string {
		const [, monthNumber, day] = date.split('-').map(Number);
		const head = `${monthNumber}월 ${day}일 ${WEEKDAYS[weekdayOf(date)]}요일`;
		const mark = markLabel(markOf(date));
		return `${head}, ${missingText ?? formatMetricValue(metric, value)}${mark ? `, ${mark}` : ''}`;
	}

	function closeOnBackdrop(event: MouseEvent) {
		if (event.target === help) help?.close();
	}
</script>

<!-- 원 가운데 체크와 같은 모양의 작은 체크(상세 꼬리표·안내 팝업) -->
{#snippet check(mark: 'max' | 'min')}
	<svg viewBox="0 0 16 16" class="size-3.5 shrink-0" aria-hidden="true">
		<path
			d="M3.5 8.3l3 3 6-6.4"
			fill="none"
			class={mark === 'max' ? 'stroke-primary' : 'stroke-coral-sub'}
			stroke-width="2.2"
			stroke-linecap="round"
			stroke-linejoin="round"
		/>
	</svg>
{/snippet}

<!-- 그날 상세의 두 칸 값(잠든·깬 시각, 측정 최저·최고). 운동 횟수·칼로리·거리 칸과 같은 모양이다 -->
{#snippet pair(
	leftName: string,
	leftValue: string,
	rightName: string,
	rightValue: string,
	kind: string
)}
	<dl
		class="grid grid-cols-2 divide-x divide-border rounded-xl border border-border bg-card py-3 text-center"
		data-calendar-pair={kind}
	>
		{#each [[leftName, leftValue] as const, [rightName, rightValue] as const] as [name, text] (name)}
			<div class="min-w-0 px-2">
				<dt class="text-xs text-text-secondary">{name}</dt>
				<dd class="mt-1 text-sm font-bold whitespace-nowrap">{text}</dd>
			</div>
		{/each}
	</dl>
{/snippet}

<!-- 도넛 하나. 첫 바퀴는 평균까지(옅은 색), 평균을 넘은 만큼 두 바퀴째(진한 색)를 겹쳐 감는다. null 은 기록 없음(점선) -->
{#snippet donut(
	value: number | null,
	today: boolean,
	size: 'cell' | 'legend',
	mark: 'max' | 'min' | null = null
)}
	<svg
		viewBox="0 0 30 30"
		class={size === 'cell' ? 'size-[30px] shrink-0' : 'size-10'}
		aria-hidden="true"
		data-donut={value === null ? 'missing' : today ? 'today' : 'value'}
	>
		{#if value === null}
			<circle
				cx="15"
				cy="15"
				r="11"
				fill="none"
				class="stroke-text-disabled"
				stroke-width="1.5"
				stroke-dasharray="2 3"
			/>
		{:else}
			{@const fill = donutFill(value, average, max)}
			<circle cx="15" cy="15" r="11" fill="none" class="stroke-border" stroke-width="4" />
			{#each [[fill.first, today ? 'stroke-warning' : 'stroke-coral-main'] as const, [fill.second, 'stroke-primary'] as const] as [amount, color], lap (lap)}
				{#if amount > 0}
					{@const dash = donutDash(amount)}
					<circle
						cx="15"
						cy="15"
						r="11"
						fill="none"
						class={color}
						stroke-width="4"
						pathLength="100"
						stroke-dasharray={dash.dasharray}
						stroke-dashoffset={dash.dashoffset}
						stroke-linecap={dash.linecap}
						data-amount={amount}
						transform="rotate(-90 15 15)"
						data-lap={lap + 1}
					/>
				{/if}
			{/each}
		{/if}
		<!-- 기간 최고·최저 날은 원 가운데 작은 체크로 알린다. 최고는 진한 레드, 최저는 물 빠진 코랄(coral-sub) -->
		{#if mark}
			<path
				d="M10.6 15.3l3 3 5.8-6.2"
				fill="none"
				class={mark === 'max' ? 'stroke-primary' : 'stroke-coral-sub'}
				stroke-width="2.4"
				stroke-linecap="round"
				stroke-linejoin="round"
				data-extreme-mark={mark}
			/>
		{/if}
	</svg>
{/snippet}

<div
	class="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] md:items-start"
	data-day-calendar
>
	<div class="rounded-2xl border border-border bg-surface px-[12px] pt-3 pb-4 sm:px-4">
		<!-- 머리줄 크기는 px 로 둔다. rem 이면 글자를 200%로 키웠을 때 이 줄이 휴대폰 폭보다 넓어져 달력이 옆으로 넘쳤다(S15P31A403-438 점검) -->
		<div class="grid grid-cols-[32px_1fr_32px] items-center">
			<span></span>
			<div class="flex items-center justify-center gap-1">
				<button
					type="button"
					class={[
						'flex size-[32px] shrink-0 items-center justify-center rounded-lg text-text-secondary hover:bg-card',
						monthIndex === 0 && 'invisible'
					]}
					aria-label="이전 달"
					bind:this={previousButton}
					onclick={() => moveMonth(-1)}
				>
					<svg
						class="size-4"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
					>
						<path d="m15 6-6 6 6 6" stroke-linecap="round" stroke-linejoin="round" />
					</svg>
				</button>
				<h4
					class="min-w-0 px-1 text-center text-base font-bold break-keep"
					aria-live="polite"
					data-calendar-month
				>
					{month ? monthTitle(month) : ''}
				</h4>
				<button
					type="button"
					class={[
						'flex size-[32px] shrink-0 items-center justify-center rounded-lg text-text-secondary hover:bg-card',
						monthIndex === months.length - 1 && 'invisible'
					]}
					aria-label="다음 달"
					bind:this={nextButton}
					onclick={() => moveMonth(1)}
				>
					<svg
						class="size-4"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
					>
						<path d="m9 6 6 6-6 6" stroke-linecap="round" stroke-linejoin="round" />
					</svg>
				</button>
			</div>
			<button
				type="button"
				class="flex size-[28px] items-center justify-center justify-self-end rounded-full border border-border bg-card text-[13px] font-bold text-text-secondary hover:text-text-primary"
				aria-label="달력 보는 법"
				aria-haspopup="dialog"
				onclick={() => help?.showModal()}
			>
				?
			</button>
		</div>

		<!--
			요일·날짜 숫자는 px 로 둔다. 휴대폰 폭 일곱 칸(약 35px)에 rem 글자를 200%로 키우면 두 자리 날짜가 옆 칸 숫자와 붙었다(S15P31A403-438 점검).
			아래 상세의 글자는 그대로 커진다
		-->
		<div class="mt-2 grid grid-cols-7 text-center text-[12px]" aria-hidden="true">
			{#each WEEKDAYS as name, index (name)}
				<span
					class={[
						'py-1',
						index === 0 ? 'text-primary' : index === 6 ? 'text-info' : 'text-text-secondary'
					]}
				>
					{name}
				</span>
			{/each}
		</div>
		<ol
			class="grid grid-cols-7 gap-y-1"
			aria-label={month ? `${monthTitle(month)} 날짜별 기록` : undefined}
		>
			{#each weeks.flat() as cell (cell.date)}
				{@const tone =
					cell.weekday === 0
						? 'text-primary'
						: cell.weekday === 6
							? 'text-info'
							: 'text-text-primary'}
				<li class="flex justify-center">
					{#if cell.entry}
						{@const entry = cell.entry}
						{@const value = shownValue(entry)}
						{@const night =
							metric === 'sleeps' ? spo2Nights.find((item) => item.date === cell.date) : undefined}
						<button
							type="button"
							class={[
								'relative flex w-full max-w-12 flex-col items-center gap-0.5 rounded-xl border-2 py-1 transition-colors',
								selected?.date === cell.date
									? 'border-text-disabled bg-card'
									: 'border-transparent hover:bg-card'
							]}
							aria-pressed={selected?.date === cell.date}
							aria-label={cellLabel(
								cell.date,
								value,
								value === null
									? entry.reason
										? MISSING_REASON_LABELS[entry.reason]
										: '기록 없음'
									: entry.is_in_progress
										? `${formatMetricValue(metric, value)}, 수집 중`
										: null
							)}
							onclick={() => (chosenDate = cell.date)}
							data-calendar-day={cell.date}
						>
							<span class={['text-[14px] leading-[20px] font-bold', tone]} data-day-number
								>{cell.day}</span
							>
							{@render donut(value, entry.is_in_progress, 'cell', markOf(cell.date))}
							{#if night && hasLowSpo2(night.state)}
								<span
									class="absolute top-[4px] right-[6px] size-[6px] rounded-full bg-primary"
									data-spo2-low-dot
								></span>
							{/if}
						</button>
					{:else}
						<!-- 앞뒤 달의 날은 흐리게, 같은 달의 기간 밖 날은 회색 숫자만 둔다 -->
						<div
							class={[
								// 날짜 버튼과 같은 높이가 되게 같은 굵기의 투명 테두리를 둔다. 없으면 이 칸만 있는 줄이 4px 낮아졌다
								'flex w-full max-w-12 flex-col items-center gap-0.5 border-2 border-transparent py-1',
								!cell.inMonth && 'opacity-35'
							]}
							aria-hidden="true"
						>
							<span
								class={[
									'text-[14px] leading-[20px]',
									cell.inMonth ? 'text-text-disabled' : ['font-bold', tone]
								]}
							>
								{cell.day}
							</span>
							<span class="size-[30px]"></span>
						</div>
					{/if}
				</li>
			{/each}
		</ol>
	</div>

	<div class="flex flex-col gap-4">
		{#if selected}
			{@const value = shownValue(selected)}
			{@const daySessions = sessions.filter((session) => session.date === selected.date)}
			{@const activityRow = activity?.rows.find((row) => row.date === selected.date)}
			{@const night = spo2Nights.find((item) => item.date === selected.date)}
			{@const nightRecord = snapshot.detail.nights.find((item) => item.date === selected.date)}
			{@const dayRange = dailyRangeOf(snapshot, selected.date)}
			{@const dayNumber = Number(selected.date.slice(8))}
			<div
				class="flex flex-col gap-3 rounded-2xl border border-border bg-surface px-5 py-5"
				aria-live="polite"
				data-calendar-detail
			>
				<!-- 달을 먼저 작게, 날짜와 요일을 크게(S15P31A403-435). "27 화요일 10월" 순서는 읽기 어색했다 -->
				<!-- 두 칸 배치는 768px 부터다. 640px 에서는 오른쪽 칸이 좁아 요일이 상자 밖으로 나갔다(S15P31A403-438 점검) -->
				<p class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
					<span class="text-[13px] text-text-secondary">{Number(selected.date.slice(5, 7))}월</span>
					<span class="text-[28px] leading-none font-bold">{dayNumber}</span>
					<span class="text-sm font-bold">{WEEKDAYS[weekdayOf(selected.date)]}요일</span>
				</p>
				{#if value === null}
					<p class="text-sm font-bold text-text-secondary">
						{selected.reason ? MISSING_REASON_LABELS[selected.reason] : '기록 없음'}
					</p>
				{:else}
					<div>
						<!-- 심박 칸의 값은 하루 평균이다. 아래 측정 최저·최고와 헷갈리지 않게 이름을 붙인다 -->
						{#if metric === 'heart_rates'}
							<p class="mb-0.5 text-xs text-text-secondary">하루 평균</p>
						{/if}
						<p class="flex flex-wrap items-center gap-x-2 gap-y-1">
							<span class="text-metric-sm whitespace-nowrap"
								>{formatMetricValue(metric, value)}</span
							>
							{#if markOf(selected.date)}
								{@const mark = markOf(selected.date)}
								<span
									class={[
										'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold',
										mark === 'max'
											? 'bg-primary-soft text-primary'
											: 'border border-coral-sub bg-card text-text-primary'
									]}
									data-calendar-extreme={mark}
								>
									{@render check(mark ?? 'max')}{markLabel(mark)}
								</span>
							{/if}
						</p>
						<p
							class={[
								'mt-1 text-[13px]',
								selected.is_in_progress ? 'text-warning' : 'text-text-secondary'
							]}
						>
							{selected.is_in_progress ? '수집 중' : compareToAverage(metric, value, average)}
						</p>
					</div>
				{/if}

				<!-- 활동 탭은 그날 시간대별 걸음을 함께 보여 준다(S15P31A403-438) -->
				{#if metric === 'steps'}
					<HourlyStepsMini date={selected.date} period={hourlyPeriod} loader={hourlyLoader} />
				{/if}

				<!-- 그날 실제 측정 최저·최고 심박(detail.daily_ranges). 근거에 실려 오지만 화면이 읽지 않던 값이다 -->
				{#if dayRange}
					{@render pair(
						'측정 최저',
						formatMetricValue(metric, dayRange.min),
						'측정 최고',
						formatMetricValue(metric, dayRange.max),
						'heart-range'
					)}
				{/if}

				<!-- 그 밤에 잠든 시각과 깬 시각. 날짜는 깬 날이라 잠든 시각이 전날 밤일 수 있다 -->
				{#if metric === 'sleeps' && nightRecord}
					{@render pair(
						'잠든 시각',
						formatClockTime(nightRecord.start_at),
						'깬 시각',
						formatClockTime(nightRecord.end_at),
						'sleep-time'
					)}
				{/if}

				{#if metric === 'exercises'}
					<!-- 그날 운동 횟수·칼로리·거리(S15P31A403-375). 따로 있던 날짜별 표를 달력 상세로 옮겼다 -->
					{#if activityRow}
						<dl
							class="grid grid-cols-3 divide-x divide-border rounded-xl border border-border bg-card py-3 text-center"
							data-calendar-activity
						>
							{#each ACTIVITY_NAMES as name, index (name)}
								<div class="min-w-0 px-2">
									<dt class="text-xs text-text-secondary">{name}</dt>
									<dd class="mt-1 text-sm font-bold whitespace-nowrap">
										{activityRow.cells[index]}
									</dd>
								</div>
							{/each}
						</dl>
					{/if}
					{#if daySessions.length > 0}
						<ol
							class="flex flex-col divide-y divide-border rounded-xl border border-border bg-card px-4"
						>
							{#each daySessions as item (item.start_at)}
								<li class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5">
									<span class="flex min-w-0 items-baseline gap-2">
										<span class="text-[13px] text-text-secondary"
											>{formatClockTime(item.start_at)}</span
										>
										<span
											class={[
												'truncate text-sm font-bold',
												!item.counts_as_exercise && 'text-text-secondary'
											]}
										>
											{item.title ?? item.type_name}
										</span>
									</span>
									<span class="flex shrink-0 items-center gap-2">
										{#if item.isLongest}
											<span
												class="rounded-full bg-primary-soft px-2 py-0.5 text-xs font-bold text-primary"
												>가장 길게</span
											>
										{:else if !item.counts_as_exercise}
											<span class="text-xs text-text-secondary">운동 미인정</span>
										{/if}
										<span
											class={[
												'text-sm font-bold whitespace-nowrap',
												!item.counts_as_exercise && 'text-text-secondary'
											]}
										>
											{formatMetricValue('exercises', item.duration_minutes)}
										</span>
									</span>
								</li>
							{/each}
						</ol>
					{/if}
					{#if daySessions.some((session) => !session.counts_as_exercise)}
						<p class="text-[13px] text-text-secondary">
							운동으로 인정되지 않은 기록은 운동 시간에서 뺐어요.
						</p>
					{/if}
				{/if}

				{#if metric === 'sleeps' && night}
					<dl class="divide-y divide-border rounded-xl border border-border bg-card px-4 text-sm">
						<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5">
							<dt>수면 중 평균 산소포화도</dt>
							<dd class="font-bold whitespace-nowrap">
								{night.average !== null
									? formatMetricValue('oxygen_saturations', night.average)
									: '-'}
							</dd>
						</div>
						{#if nightRecord?.spo2_min !== undefined || nightRecord?.spo2_max !== undefined}
							<div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5">
								<dt>최저 · 최고</dt>
								<!-- 글자를 키우면 한 줄에 다 들어가지 않아 가운뎃점에서 줄을 바꾼다 -->
								<dd class="text-right font-bold">
									{formatMetricValue('oxygen_saturations', nightRecord?.spo2_min ?? null)} · {formatMetricValue(
										'oxygen_saturations',
										nightRecord?.spo2_max ?? null
									)}
								</dd>
							</div>
						{/if}
						<div class="flex items-start justify-between gap-3 py-2.5">
							<dt class="shrink-0">90% 미만</dt>
							<dd class="text-right font-bold"><Spo2LowTimes state={night.state} bare /></dd>
						</div>
					</dl>
				{/if}
			</div>
		{/if}

		{#if metric === 'exercises' && activity}
			<!--
			기간의 운동 횟수·칼로리·거리(S15P31A403-375). AI 가 운동 요약에 쓰는 값이라 달력 옆(휴대폰 폭은 아래)에 늘 둔다.
			합계·평균은 서버가 낸 값 그대로이고, 나눠 조회한 기간은 합계가 없어 안내만 둔다
		-->
			<section
				class="flex flex-col gap-3 rounded-2xl border border-border bg-surface px-5 py-5"
				data-evidence-detail="exercise-activity"
			>
				<h4 class="text-sm font-bold">이 기간 합계</h4>
				{#if activity.cards.length > 0}
					<!-- 휴대폰 폭은 한 줄에 하나(왼쪽 이름·보조 글, 오른쪽 값), 넓은 화면은 세 칸 -->
					<dl class="flex flex-col gap-3 sm:grid sm:grid-cols-3">
						{#each activity.cards as card (card.label)}
							<div class="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-3 sm:block">
								<dt class="text-xs text-text-secondary">{card.label}</dt>
								<dd
									class="col-start-2 row-span-2 row-start-1 self-center text-base font-bold whitespace-nowrap sm:mt-1"
								>
									{card.value}
								</dd>
								<!-- 휴대폰 폭은 한 줄로 이어 쓰고, 넓은 화면은 칸이 좁아 한 줄에 하나씩 둔다 -->
								<dd class="mt-0.5 text-xs text-text-secondary">
									{#each card.captions as caption, index (caption)}
										{#if index > 0}<span class="mx-1 sm:hidden">·</span>{/if}<span class="sm:block"
											>{caption}</span
										>
									{/each}
								</dd>
							</div>
						{/each}
					</dl>
				{/if}
				<ul
					class="flex flex-col gap-1 border-t border-border pt-3 text-xs leading-5 text-text-secondary"
				>
					{#each activity.notes as note (note)}
						<li>{note}</li>
					{/each}
				</ul>
			</section>
		{/if}
	</div>
</div>
<!-- 물음표를 누르면 뜨는 안내. 근거 창 위에 한 겹 더 띄우고 뒤를 흐리게 한다 -->
<dialog
	bind:this={help}
	class="m-auto w-[min(22rem,calc(100vw-2rem))] rounded-3xl bg-card p-0 break-keep text-text-primary backdrop:bg-text-primary/40"
	aria-labelledby={`${helpId}-title`}
	onclick={closeOnBackdrop}
	data-calendar-help
>
	<div class="flex flex-col gap-4 px-6 pt-6 pb-5">
		<div>
			<h4 id={`${helpId}-title`} class="text-lg font-bold">한 바퀴가 {baseName}이에요</h4>
			<p class="mt-2 text-sm leading-6 text-text-secondary">
				날마다 기록을 이 기간 {baseName}과 비교해 원을 채워요. 평균을 넘은 날은 진한 색으로 한 번 더
				감기고, 기간에서 가장 높은 날이 두 바퀴를 다 채워요.
			</p>
		</div>
		<p class="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-sm">
			<span class="text-text-secondary">이 기간 {baseName}</span>
			<span class="font-bold">{formatMetricValue(metric, average)}</span>
		</p>
		<ul class="grid auto-cols-fr grid-flow-col gap-1 text-center text-xs text-text-secondary">
			{#each samples as [sample, name] (name)}
				<li class="flex flex-col items-center gap-1">
					{@render donut(sample, false, 'legend')}
					{name}
				</li>
			{/each}
		</ul>
		<div class="flex flex-col gap-1 border-t border-border pt-3 text-[13px] text-text-secondary">
			{#if extremeDates?.max}
				<p class="flex flex-wrap items-center gap-x-3 gap-y-1">
					<span class="inline-flex items-center gap-1">{@render check('max')}{longestLabel}</span>
					{#if extremeDates.min}
						<span class="inline-flex items-center gap-1">{@render check('min')}{shortestLabel}</span
						>
					{/if}
				</p>
			{/if}
			{#if inProgress}
				<p>오늘은 기록이 모이는 중이라 노란색으로 보여요.</p>
			{/if}
			{#if metric === 'sleeps' && spo2Nights.some((night) => hasLowSpo2(night.state))}
				<p>날짜 옆 빨간 점은 수면 중 산소포화도가 90% 미만으로 내려간 밤이에요.</p>
			{/if}
			{#if basis === 'active-days'}
				<p>
					운동 기록이 없는 날은 쉬는 날이라 평균과 최저에서 뺐어요. 위의 평균 운동 시간은 그날까지
					넣은 값이에요.
				</p>
			{:else if basis === 'merged'}
				<p>
					나눠 조회한 기간을 합쳐 이 달력에서만 쓰는 평균이에요. AI 가 받은 평균은 위 기간별
					평균이에요.
				</p>
			{/if}
			<p>날짜를 누르면 그날 기록을 볼 수 있어요.</p>
		</div>
		<button
			type="button"
			class="h-11 rounded-xl bg-primary text-sm font-bold text-primary-foreground hover:bg-primary-hover"
			onclick={() => help?.close()}
		>
			확인
		</button>
	</div>
</dialog>
