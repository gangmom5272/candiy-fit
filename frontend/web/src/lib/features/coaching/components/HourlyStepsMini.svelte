<script lang="ts">
	import { formatMetricValue } from '../format';
	import {
		hourlyRange,
		type DateRange,
		type HourlySteps,
		type HourlyStepsLoader
	} from '../hourly-steps';

	/**
	 * 활동 달력에서 고른 날의 시간대별 걸음(S15P31A403-438). 날짜별 걸음 근거에는 시간대 값이 실려 오지 않아,
	 * 건강 화면과 같은 시간대 걸음 조회(/health/timeline)로 불러온다. AI 가 받은 값이 아니라 지금 저장된 기록이라
	 * 아래에 그렇게 적는다.
	 * 그날 하루가 아니라 그날이 든 달을 한 번에 불러와(hourly-steps.ts), 같은 달 안에서는 날짜를 바꿔도 다시 부르지 않는다.
	 * 불러오는 중·기록 없음·실패·값 모두 같은 세 줄(문장·막대·시각)로 그려, 날짜를 빠르게 바꿔도 상자 높이가 그대로다(S15P31A403-439)
	 */
	let { date, period, loader }: { date: string; period: DateRange; loader: HourlyStepsLoader } =
		$props();

	type State =
		{ status: 'loading' } | { status: 'error' } | { status: 'ready'; hours: HourlySteps };

	const HOURS = Array.from({ length: 24 }, (_, hour) => hour);

	let view = $state<State>({ status: 'loading' });
	let retry = $state(0);

	$effect(() => {
		const day = date;
		void retry;
		const hit = loader.peek(day);
		if (hit !== undefined) {
			view = { status: 'ready', hours: hit };
			return;
		}
		// 응답이 도착했을 때 다른 날을 보고 있으면 화면을 덮지 않는다. 요청은 끊지 않아 그 달 값은 쌓인다
		let current = true;
		view = { status: 'loading' };
		loader.load(hourlyRange(day, period)).then(
			() => {
				if (current) view = { status: 'ready', hours: loader.peek(day) ?? null };
			},
			() => {
				if (current) view = { status: 'error' };
			}
		);
		return () => {
			current = false;
		};
	});

	const hours = $derived(view.status === 'ready' ? view.hours : null);
	const peak = $derived.by(() => {
		if (!hours) return null;
		const value = Math.max(...hours);
		return value > 0 ? { hour: hours.indexOf(value), value } : null;
	});
	/** 막대 높이(px). 0걸음인 시간은 바닥 선, 값이 있으면 가장 많이 걸은 시간을 64px 로 두고 견준다 */
	const barHeight = (value: number, top: number) =>
		value === 0 ? 2 : Math.max(4, (value / top) * 64);

	/**
	 * 마우스를 올리거나 누른 시간(S15P31A403-439). 일별 막대 차트의 팝업과 같은 모양으로 그 시간의 걸음을 띄운다.
	 * - 막대가 가늘고 0걸음인 시간은 바닥 선뿐이라, 막대가 아니라 그 시간의 칸 전체(높이 64px)가 반응한다
	 * - 터치는 손을 떼는 순간 벗어난 것으로 오므로 그대로 두고, 바깥을 누를 때 닫는다
	 * - 어느 날의 어느 시간인지 함께 적어 두어, 날짜를 바꾸면 따로 지우지 않아도 닫힌다
	 * 같은 내용이 아래 목록(보조기기)에 있어 키보드 포커스는 두지 않는다
	 */
	let picked = $state<{ day: string; hour: number } | null>(null);
	const shown = $derived(picked?.day === date ? picked.hour : null);

	function handleOutside(event: PointerEvent) {
		if (picked === null) return;
		const target = event.target instanceof Element ? event.target : null;
		if (!target?.closest('[data-hour]')) picked = null;
	}
</script>

<svelte:document onpointerdown={handleOutside} />

<section
	class="isolate flex flex-col gap-2 rounded-xl border border-border bg-card px-4 py-3"
	data-hourly-steps
	data-hourly-status={view.status}
>
	<h4 class="text-xs text-text-secondary">시간대별 걸음</h4>
	<!-- 첫 줄: 가장 많이 걸은 시간. 값이 없을 때는 그 자리에 안내를 같은 줄 높이로 둔다 -->
	{#if view.status === 'loading'}
		<p class="text-sm" aria-hidden="true">&nbsp;</p>
	{:else if view.status === 'error'}
		<p class="flex items-center justify-between gap-3 text-[13px] leading-5 text-text-secondary">
			시간대별 걸음을 불러오지 못했어요.
			<button
				type="button"
				class="-my-1 shrink-0 rounded-lg px-2 py-1 font-bold text-text-primary hover:bg-surface"
				onclick={() => (retry += 1)}
			>
				다시 불러오기
			</button>
		</p>
	{:else if !hours || !peak}
		<p class="text-[13px] leading-5 text-text-secondary">이날은 시간대별 걸음 기록이 없어요.</p>
	{:else}
		<p class="text-sm">
			가장 많이 걸은 시간 <b class="font-bold">{peak.hour}시</b>
			· {formatMetricValue('steps', peak.value)}
		</p>
	{/if}
	<!-- 둘째 줄: 막대 24개(0시~23시). 가장 많이 걸은 시간만 진하게 둔다. 값은 팝업과 아래 목록(보조기기)에 있다 -->
	{#if view.status === 'loading'}
		<div
			class="h-16 animate-pulse rounded-lg bg-surface"
			aria-label="시간대별 걸음 불러오는 중"
		></div>
	{:else if hours && peak}
		<div class="relative grid h-16 grid-cols-24 gap-px" aria-hidden="true">
			{#each hours as value, hour (hour)}
				<div
					class="flex h-full items-end"
					role="presentation"
					data-hour={hour}
					data-hourly-active={shown === hour ? '' : undefined}
					onpointerenter={() => (picked = { day: date, hour })}
					onpointerleave={(event) => {
						if (event.pointerType !== 'touch' && shown === hour) picked = null;
					}}
				>
					<span
						class={[
							'w-full rounded-t-[2px]',
							value === 0 ? 'bg-border' : hour === peak.hour ? 'bg-primary' : 'bg-coral-main',
							// 올린 시간의 막대는 조금 진하게 해 어느 막대의 팝업인지 보인다
							shown === hour && 'brightness-75'
						]}
						style:height={`${barHeight(value, peak.value)}px`}
					></span>
				</div>
			{/each}
			{#if shown !== null}
				<!--
					팝업은 막대 바로 위에 띄운다. 가로 자리는 차트 폭에서 그 시간이 놓인 비율만큼 팝업 자신도 왼쪽으로 당겨,
					0시는 왼쪽 끝·23시는 오른쪽 끝에 맞고 그 사이는 차츰 옮겨 간다. 폭을 재지 않아도 차트 밖으로 나가지 않는다
				-->
				{@const share = ((shown + 0.5) / 24) * 100}
				<div
					class="pointer-events-none absolute z-10 w-max rounded-xl border border-border bg-card px-3 py-2 text-left shadow-md"
					style:left={`${share}%`}
					style:transform={`translateX(-${share}%)`}
					style:bottom={`${barHeight(hours[shown], peak.value) + 6}px`}
					data-hourly-popup
				>
					<p class="text-card-title whitespace-nowrap">
						{formatMetricValue('steps', hours[shown])}
					</p>
					<p class="mt-0.5 text-xs whitespace-nowrap text-text-secondary">{shown}시</p>
				</div>
			{/if}
		</div>
	{:else}
		<!-- 기록이 없거나 불러오지 못한 날은 바닥 선만 둔다(값이 0인 시간과 같은 모양) -->
		<div class="grid h-16 grid-cols-24 items-end gap-px" aria-hidden="true" data-hourly-empty>
			{#each HOURS as hour (hour)}
				<span class="h-[2px] rounded-t-[2px] bg-border"></span>
			{/each}
		</div>
	{/if}
	<div class="grid grid-cols-4 text-[11px] text-text-secondary" aria-hidden="true">
		{#each [0, 6, 12, 18] as hour (hour)}<span>{hour}시</span>{/each}
	</div>
	{#if hours && peak}
		<ol class="sr-only">
			{#each hours as value, hour (hour)}
				<li>{hour}시 {formatMetricValue('steps', value)}</li>
			{/each}
		</ol>
	{/if}
	<p class="text-[11px] text-text-secondary">지금 저장된 기록 기준이에요.</p>
</section>
