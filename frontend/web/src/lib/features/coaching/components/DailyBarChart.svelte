<script lang="ts">
	import { untrack } from 'svelte';
	import { barTone, chartScale, formatAxisValue, scaleFraction, type BarTone } from '../chart';
	import type { ChartBar } from '../evidence-view';
	import { formatMetricValue } from '../format';
	import type { HealthMetric } from '../types';
	import { VISIBLE_DAYS, WeekScroll, type RangeView } from '../week-scroll.svelte';
	import ChartRangeControls from './ChartRangeControls.svelte';
	import ChartScrollBar from './ChartScrollBar.svelte';

	/** 꼬리표를 달 막대 하나. key 는 막대의 key(날짜)다 */
	type Extreme = { key: string; value: number };

	type Props = {
		metric: HealthMetric;
		bars: ChartBar[];
		/** 평균선. 기록이 하루뿐이면 막대와 같은 값이라 넘기지 않는다 */
		average: number | null;
		/**
		 * 가장 높은 날과 낮은 날(S15P31A403-404). 색으로 따로 칠하지 않고 막대 위 꼬리표로 알린다.
		 * 두 값이 같거나 기록이 적으면 넘기지 않는다. min 이 null 이면 최고만 단다(최저가 0인 날 등)
		 */
		extremes?: { max: Extreme; min: Extreme | null } | null;
		/**
		 * 비교 기간의 평균(S15P31A403-404). 회색 실선으로 겹쳐 그리고 두 평균선 끝에 이름과 값을 붙인다.
		 * delta 는 화면에서 이미 쓰는 "이전 기간 대비" 표기, reference 는 기록이 적어 참고용인 비교다
		 */
		previous?: { average: number; delta: string; reference: boolean } | null;
		/**
		 * 막대 하나가 하루인지. 하루 단위면 일별 차트 공통 규칙을 따른다(S15P31A403-418):
		 * 처음에는 최근 일주일만 보이고, 기간이 일주일을 넘으면 가로로 넘겨 본다. 주 단위로 묶은 막대는 넘기지 않는다
		 */
		daily?: boolean;
		/**
		 * 지금 보기(일주일·기간 전체). 근거 창이 탭마다 기억해 두었다가 그 탭으로 돌아오면 같은 보기로 시작하게 한다(S15P31A403-439).
		 * 넘기지 않으면 일주일로 시작한다
		 */
		view?: RangeView;
		/** 차트 설명. 화면에는 보이지 않고 보조기기에 읽힌다 */
		label: string;
	};

	let {
		metric,
		bars,
		average,
		extremes = null,
		previous = null,
		daily = true,
		view = $bindable('week'),
		label
	}: Props = $props();

	const TONE_CLASS: Record<BarTone, string> = {
		high: 'bg-brand',
		normal: 'bg-coral-sub'
	};
	/** 평균선이 없으면 견줄 기준이 없어 모든 막대를 한 색으로 둔다 */
	const PLAIN_CLASS = 'bg-coral-main';

	/** 차트 높이(h-44). 평균선 끝 이름표의 위치를 px 로 잡는 데 쓴다 */
	const PLOT_HEIGHT = 176;
	/*
	 * 꼬리표는 바깥 타원 안에 이름("최고")을 담은 작은 타원이 있다. 바깥 여백을 위·아래·왼쪽 모두 같게(p-0.5) 두어야
	 * 두 타원의 곡선이 같은 중심으로 나란해 보인다(S15P31A403-427). 처음에는 위아래 2px · 왼쪽 4px(또는 0)라 곡선이 어긋났다.
	 * 평균선 끝 이름표도 같은 모양이라 함께 맞췄다
	 */
	/** 막대 위 꼬리표 하나의 높이(px)와, 막대 꼭대기에서 띄우는 간격 */
	const CHIP_HEIGHT = 26;
	const CHIP_GAP = 8;
	/**
	 * 두 꼬리표가 닿는지 볼 때 값까지 적은 꼬리표에 가로로 더 잡는 여유(px, S15P31A403-439).
	 * 꼬리표 폭은 자리를 잡기 전에 글자 수로 어림하는데, 값까지 적은 꼬리표는 실제보다 6~10px 작게 잡힌다.
	 * 그래서 최고와 최저가 기간 양 끝 날에 있으면 휴대폰 폭(320~400px)에서 계산으로는 떨어져 있는데 화면에서는 1~11px 겹쳤다.
	 * 폭 어림 자체를 키우면 꼬리표 하나의 자리(값을 적을지, 어느 막대 위로 올릴지)까지 달라져, 닿는지 볼 때만 여유를 둔다.
	 * 이름만 적은 꼬리표는 어림이 실제와 1px 차이라 여유를 두지 않는다. 두면 붙어 있을 뿐인 두 꼬리표를 닿았다고 보고
	 * 최저를 최고 위로 올려 막대에서 멀어졌다
	 */
	const CHIP_SLACK_FULL = 10;
	/** 이름표 두 개가 겹치지 않는 최소 간격 */
	const END_LABEL_GAP = 28;

	const comparison = $derived(average !== null ? previous : null);
	const scale = $derived(
		chartScale(
			metric,
			bars.map((bar) => bar.value),
			average,
			comparison ? [comparison.average] : []
		)
	);
	const toPercent = (value: number) => `${scaleFraction(scale, value) * 100}%`;
	// 막대가 많으면 휴대폰 폭에 들어가도록 사이 간격을 줄인다
	const gapClass = $derived(bars.length > 14 ? 'gap-px sm:gap-1' : 'gap-2');

	const range = new WeekScroll(
		() => (daily ? bars.length : 0),
		undefined,
		() => bars.findLastIndex((bar) => !isEmpty(bar))
	);
	// 창이 기억해 둔 보기로 시작하고, 사용자가 바꾸면 창에 알린다
	range.view = untrack(() => view);
	$effect(() => {
		view = range.view;
	});
	$effect(() => range.reset());
	$effect(() => range.observe());
	/** 한 화면에 보이는 칸 수. 꼬리표와 팝업이 가장자리에서 밖으로 나가지 않게 하는 데 쓴다 */
	const span = $derived(range.scrolls ? VISIBLE_DAYS : bars.length);

	type Chip = {
		kind: 'max' | 'min';
		value: number;
		index: number;
		/** 값까지 적는지. 아니면 "최고"·"최저"만 적는다 */
		full: boolean;
		align: 'start' | 'center' | 'end';
		/** 막대 칸 바닥에서 꼬리표 아래 끝까지의 높이(px) */
		bottom: number;
		/** 꼬리표를 자기 막대 바로 위에 둘 때의 높이(px). 이보다 많이 올라가면 점선으로 막대와 잇는다 */
		own: number;
	};
	/** 차트가 그려지는 폭과 한 칸의 폭(px). 꼬리표 자리를 잡는 데 쓴다 */
	let plotWidth = $state(0);
	const column = $derived(span > 0 ? plotWidth / span : 0);
	/** 이보다 좁은 칸(휴대폰 폭의 일주일)은 이름만 적은 꼬리표도 글자를 줄여야 옆 막대에 닿지 않는다 */
	const NARROW_COLUMN = 48;
	/** 칸이 넓은 보기(일주일씩 넘겨 보기, 일주일 이하). 이때만 칸 폭을 보고 값을 뺄지 정한다 */
	const wide = $derived(range.scrolls || bars.length <= VISIBLE_DAYS);
	/** 막대 칸 하나에서 위로 가장 높이 올라온 곳(px). 빈 날은 점선 칸, 수집 중인 오늘은 위의 "오늘" 글자까지 친다 */
	function topOf(bar: ChartBar): number {
		const top = isEmpty(bar) ? 12 : scaleFraction(scale, bar.value ?? 0) * PLOT_HEIGHT;
		return bar.inProgress && !isEmpty(bar) ? top + 20 : top;
	}
	/**
	 * 최고·최저 꼬리표(S15P31A403-428). 값은 늘 기간 전체의 가장 높은 날과 낮은 날(extremes)이다.
	 * 427에서는 일주일씩 볼 때 보이는 일주일 안에서 골랐는데, 넘길 때마다 꼬리표가 다른 막대로 옮겨 가 기준이 흔들렸다.
	 * 이제 꼬리표는 그 날의 막대에 고정되어, 그 날이 화면에 있을 때 보이고 넘기면 함께 지나간다.
	 *
	 * 자리는 막대 바로 위다. 꼬리표가 칸보다 넓으면 덮는 칸들 가운데 가장 높은 막대 위로 올려 어떤 막대와도 겹치지 않게 하고,
	 * 차트 밖으로 나가면 막대의 왼쪽·오른쪽 끝에 맞춘다. 두 꼬리표가 서로 닿으면 최저를 최고 위로 올린다.
	 * 처음에는 기간 전체 보기에서 꼬리표를 늘 차트 맨 위 줄에 두고 점선으로 이어, 낮은 막대일수록 꼬리표가 멀었다.
	 * 글자 폭은 12px 굵은 글자의 대략값으로 어림한다
	 */
	const chips = $derived.by(() => {
		const found: Record<string, Chip> = {};
		if (!extremes || column <= 0) return found;
		const barHalf = Math.min(column, 48) / 2;
		type Placed = Chip & { left: number; right: number };
		/** [named]면 이름만 적은 꼬리표로 자리를 잡는다 */
		const place = (
			kind: Chip['kind'],
			key: string,
			value: number,
			named = false
		): Placed | null => {
			const index = bars.findIndex((bar) => bar.key === key);
			if (index < 0) return null;
			const position = range.position(index);
			const center = (position + 0.5) * column;
			const name = column < NARROW_COLUMN && wide ? 34 : 38;
			const width = 52 + formatMetricValue(metric, value).length * 8;
			const atEdge = position <= 0 || position >= span - 1;
			// 넓은 보기에서는 값까지 적으면 옆 막대에 닿는 칸이면 이름만 적는다
			const full = !named && (!wide || (atEdge ? width <= column : width / 2 <= column - 28));
			const size = full ? width : name;
			let align: Chip['align'] = 'center';
			let left = center - size / 2;
			if (left < 0) [align, left] = ['start', center - barHalf];
			else if (left + size > plotWidth) [align, left] = ['end', center + barHalf - size];
			const right = left + size;
			// 꼬리표가 가로로 덮는 칸들 가운데 가장 높은 막대 위
			let top = 0;
			bars.forEach((bar, other) => {
				const middle = (range.position(other) + 0.5) * column;
				const halfBar = other === index ? 0 : barHalf;
				if (middle + halfBar > left - 2 && middle - halfBar < right + 2)
					top = Math.max(top, topOf(bar));
			});
			const own = topOf(bars[index]) + CHIP_GAP;
			return { kind, value, index, full, align, bottom: top + CHIP_GAP, left, right, own };
		};
		/**
		 * 값까지 적은 꼬리표가 넓어 옆의 높은 막대 위로 많이 떠오르면, 이름만 적은 짧은 꼬리표로 바꿔 자기 막대에 가깝게 둔다.
		 * 기간 전체 보기처럼 칸이 좁을 때다. 값은 막대의 팝업과 아래 "날짜별 기록"에 있다
		 */
		const closest = (kind: Chip['kind'], key: string, value: number): Placed | null => {
			const full = place(kind, key, value);
			if (!full || !full.full || full.bottom - full.own <= CHIP_HEIGHT / 2) return full;
			const named = place(kind, key, value, true);
			return named && named.bottom < full.bottom ? named : full;
		};
		const slack = (chip: Placed) => (chip.full ? CHIP_SLACK_FULL : 0);
		const touches = (a: Placed, b: Placed) => {
			const room = slack(a) + slack(b);
			return (
				a.left < b.right + room &&
				b.left < a.right + room &&
				a.bottom < b.bottom + CHIP_HEIGHT &&
				b.bottom < a.bottom + CHIP_HEIGHT
			);
		};
		let max = closest('max', extremes.max.key, extremes.max.value);
		let min = extremes.min ? closest('min', extremes.min.key, extremes.min.value) : null;
		if (max && min && max.index === min.index) return found;
		if (max && min && extremes.min) {
			// 두 꼬리표가 닿으면 최저부터, 그래도 닿으면 최고까지 이름만 적은 짧은 꼬리표로 줄여 본다. 그래도 닿으면 최저를 최고 위로 올린다
			if (touches(min, max) && min.full) {
				const named = place('min', extremes.min.key, extremes.min.value, true);
				if (named) min = named;
			}
			if (touches(min, max) && max.full) {
				const named = place('max', extremes.max.key, extremes.max.value, true);
				if (named) max = named;
			}
			if (touches(min, max)) min.bottom = max.bottom + CHIP_HEIGHT + 4;
		}
		if (max) found[extremes.max.key] = max;
		if (min && extremes.min) found[extremes.min.key] = min;
		return found;
	});
	/**
	 * 점선 테두리 칸으로 그릴 날: 기록이 없는 날과 값이 0인 날(운동을 안 한 날).
	 * 오늘도 같은 규칙이다. 아직 값이 없거나 0이면 점선 칸, 값이 생기면 평균과 견준 색의 채운 막대가 된다
	 */
	const isEmpty = (bar: ChartBar) => bar.value === null || bar.value === 0;
	const hasEmpty = $derived(bars.some(isEmpty));
	const hasChips = $derived(Object.keys(chips).length > 0);
	/**
	 * 차트 위로 꼬리표가 올라가는 만큼 위쪽 줄과 차트 사이를 띄운다. 일주일씩 넘겨 볼 때는 넘길 때마다 높이가 흔들리지 않게
	 * 기간 전체의 꼬리표를 모두 기준으로 잡는다(화면 밖의 꼬리표도 센다)
	 */
	const chipSpace = $derived(
		Math.max(12, ...Object.values(chips).map((chip) => chip.bottom + CHIP_HEIGHT - PLOT_HEIGHT + 4))
	);

	/**
	 * 마우스를 올린 막대(S15P31A403-418). 날짜(주 막대는 기간)와 값을 막대 옆에 띄운다. 날짜별 수면 단계의 팝업과 같은 모양이다.
	 * 같은 내용이 보조기기 글에 있어 키보드 포커스는 두지 않는다
	 */
	let hovered = $state<string | null>(null);
	/** 팝업 높이(rem). 막대 꼭대기에 맞추되 차트 위아래로 나가지 않게 가두는 데 쓴다 */
	const POPUP_HEIGHT = 3.5;
	/**
	 * 팝업을 막대 왼쪽에 띄울지. 처음에는 보이는 칸의 오른쪽 25%만 왼쪽에 띄웠는데, 휴대폰 폭에서는 칸이 약 31px 이고
	 * 팝업이 약 100px 이라 가운데 오른쪽 막대의 팝업도 넘겨 보기 영역 밖으로 나가 잘렸다(S15P31A403-438 점검).
	 * 마우스를 올릴 때 막대 오른쪽에 남은 자리를 재서, 팝업이 들어가지 않고 왼쪽이 더 넓으면 왼쪽에 띄운다
	 */
	const POPUP_ROOM = 120;
	let popupFlips = $state(false);

	function measurePopupSide(cell: HTMLElement) {
		const box = (range.element ?? cell).getBoundingClientRect();
		const rect = cell.getBoundingClientRect();
		const right = box.right - rect.right;
		const left = rect.left - box.left;
		popupFlips = right < POPUP_ROOM && left > right;
	}
	const barText = (bar: ChartBar) =>
		bar.value === null ? '기록 없음' : formatMetricValue(metric, bar.value);

	function handleOutside(event: PointerEvent) {
		if (hovered === null) return;
		const target = event.target instanceof Element ? event.target : null;
		if (!target?.closest('[data-bar]')) hovered = null;
	}
	// 끌어서 넘기기 시작하면 올려 둔 팝업을 닫는다
	$effect(() => {
		if (range.free) hovered = null;
	});

	/** 평균선 끝 이름표의 높이(px, 아래에서부터). 두 선이 가까우면 가운데를 기준으로 위아래로 벌린다 */
	const ends = $derived.by(() => {
		if (!comparison || average === null) return null;
		let current = scaleFraction(scale, average) * PLOT_HEIGHT;
		let before = scaleFraction(scale, comparison.average) * PLOT_HEIGHT;
		if (Math.abs(current - before) < END_LABEL_GAP) {
			const middle = (current + before) / 2;
			const upper = middle + END_LABEL_GAP / 2;
			const lower = middle - END_LABEL_GAP / 2;
			[current, before] = current >= before ? [upper, lower] : [lower, upper];
		}
		const lowest = Math.min(current, before);
		// 차이 글은 아래 이름표 밑에 두고, 자리가 없으면 위 이름표 위로 올린다
		const delta = lowest >= 40 ? lowest - 34 : Math.max(current, before) + 22;
		return { current, before, delta };
	});
</script>

{#snippet lineLegend()}
	<!-- 넓은 화면에서 비교 기간이 있으면 선 끝의 이름표가 범례를 대신한다 -->
	<p
		class={['flex flex-wrap items-center gap-x-4 gap-y-1', comparison && 'sm:hidden']}
		data-average-legend
	>
		<span class="flex items-center gap-2">
			<span class="w-6 border-t border-dashed border-brand" aria-hidden="true"></span>기간 평균
		</span>
		{#if comparison}
			<span class="flex items-center gap-2">
				<span class="w-6 border-t-[1.5px] border-muted-foreground" aria-hidden="true"></span>
				이전 기간 평균
			</span>
		{/if}
	</p>
{/snippet}

<svelte:document onpointerdown={handleOutside} />

<!-- 꼬리표가 있으면 그 줄이 들어갈 만큼 위쪽 줄과 차트 사이를 띄운다. 넘어가는 칸도 꼬리표가 잘리지 않게 그만큼 위로 넓힌다 -->
<figure
	class="isolate rounded-2xl bg-surface px-5 pt-5 pb-4"
	style:--chip-space={`${chipSpace}px`}
	aria-label={label}
>
	{#if average !== null || hasEmpty || hasChips || range.available}
		<!--
			색의 뜻은 왼쪽 위, 선의 뜻은 오른쪽 위에 둔다(S15P31A403-404). 평균 수치는 바로 위 요약 상자에 있어 쓰지 않는다.
			기간이 일주일을 넘으면 오른쪽 위는 넘기는 버튼과 보기 전환이 쓰고, 선의 뜻은 색의 뜻 옆으로 옮긴다(S15P31A403-418)
		-->
		<div
			class="mb-(--chip-space) flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs text-text-primary"
		>
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1">
				<p class="flex flex-wrap items-center gap-x-4 gap-y-1" data-tone-legend>
					{#if average !== null}
						<span class="flex items-center gap-1.5">
							<span class="size-2.5 rounded-[3px] bg-brand" aria-hidden="true"></span>평균 이상
						</span>
						<span class="flex items-center gap-1.5">
							<span class="size-2.5 rounded-[3px] bg-coral-sub" aria-hidden="true"></span>평균 미만
						</span>
					{/if}
					{#if hasEmpty}
						<span class="flex items-center gap-1.5">
							<span
								class="size-2.5 rounded-[3px] border border-dashed border-muted-foreground/60"
								aria-hidden="true"
							></span>기록 없음
						</span>
					{/if}
				</p>
				{#if average !== null && range.available}{@render lineLegend()}{/if}
			</div>
			{#if range.available}
				<ChartRangeControls {range} />
			{:else if average !== null}
				{@render lineLegend()}
			{/if}
		</div>
	{/if}
	<div class="flex gap-3">
		<div class="relative h-44 w-8 shrink-0 text-right text-xs text-text-primary" aria-hidden="true">
			{#each scale.ticks as tick (tick)}
				<span class="absolute right-0 translate-y-1/2" style:bottom={toPercent(tick)}>
					{formatAxisValue(metric, tick)}
				</span>
			{/each}
		</div>

		<div class="relative min-w-0 flex-1">
			<!-- 눈금선과 평균선은 넘어가는 칸 바깥에 두어 넘겨도 제자리에 있다 -->
			<div class="pointer-events-none absolute inset-x-0 top-0 h-44" aria-hidden="true">
				{#each scale.ticks as tick (tick)}
					<div
						class="absolute inset-x-0 border-t border-border"
						style:bottom={toPercent(tick)}
					></div>
				{/each}

				{#if comparison && average !== null}
					<!-- 두 평균선 사이를 옅게 칠해 차이를 보인다. 이전 기간은 보조 글자색 실선이다 -->
					<div
						class="absolute inset-x-0 bg-primary-soft"
						style:bottom={toPercent(Math.min(average, comparison.average))}
						style:height={`${Math.abs(scaleFraction(scale, average) - scaleFraction(scale, comparison.average)) * 100}%`}
					></div>
					<div
						class="absolute inset-x-0 z-10 border-t-[1.5px] border-muted-foreground"
						style:bottom={toPercent(comparison.average)}
						data-previous-average
					></div>
				{/if}

				{#if average !== null}
					<div
						class="absolute inset-x-0 z-10 border-t border-dashed border-brand"
						style:bottom={toPercent(average)}
					></div>
				{/if}
			</div>

			<!-- 넘어가는 칸이라 키보드로도 넘길 수 있게 포커스를 받는다(넘길 내용이 없으면 받지 않는다) -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
			<ol
				bind:this={range.element}
				bind:clientWidth={plotWidth}
				aria-label={range.scrolls ? `${label} 날짜별 값, 가로로 넘겨 보기` : undefined}
				tabindex={range.scrolls ? 0 : undefined}
				class={[
					'relative flex',
					range.scrolls
						? [range.scrollerClass, '-mt-(--chip-space) pt-(--chip-space)']
						: ['justify-around', gapClass]
				]}
				onscroll={range.onscroll}
				onpointerdown={range.onpointerdown}
				onpointermove={range.onpointermove}
				onpointerup={range.onpointerup}
				onpointercancel={range.onpointerup}
				data-range-scroller={range.scrolls ? '' : undefined}
			>
				{#each bars as bar, barIndex (bar.key)}
					{@const chip = chips[bar.key]}
					<li
						class={[
							'flex min-w-0 flex-col items-center',
							range.scrolls ? 'shrink-0 grow-0 basis-[calc(100%/7)]' : 'w-full max-w-12'
						]}
						data-day={bar.key}
					>
						<!-- 터치는 손을 떼는 순간 벗어난 것으로 오므로 그대로 두고, 바깥을 누를 때 닫는다 -->
						<div
							class={[
								'relative flex h-44 w-full max-w-12 flex-col items-center justify-end',
								// 넘겨 볼 때는 칸 사이 간격이 없어, 휴대폰 폭처럼 칸이 좁으면 막대끼리 붙어 한 덩어리로 보였다(S15P31A403-427).
								// 칸 폭의 12%씩 좌우를 비워 막대를 얇게 하고 사이를 띄운다. 넓은 화면은 막대 폭 상한(max-w-12)으로 이미 떨어져 있다
								range.scrolls && 'max-sm:px-[12%]',
								hovered === bar.key && 'z-30'
							]}
							role="presentation"
							data-bar={bar.key}
							onpointerenter={(event) => {
								if (range.dragging) return;
								measurePopupSide(event.currentTarget);
								hovered = bar.key;
							}}
							onpointerleave={(event) => {
								if (event.pointerType !== 'touch' && hovered === bar.key) hovered = null;
							}}
						>
							{#if chip && bar.value !== null}
								{#if chip.bottom - chip.own > CHIP_HEIGHT / 2}
									<!-- 옆의 높은 막대 위로 올라간 꼬리표는 어느 막대의 것인지 가는 점선으로 잇는다 -->
									<span
										class="pointer-events-none absolute left-1/2 z-10 border-l border-dashed border-muted-foreground/60"
										style:bottom={`${chip.own - CHIP_GAP}px`}
										style:height={`${chip.bottom - chip.own + CHIP_GAP}px`}
										aria-hidden="true"
										data-extreme-lead
									></span>
								{/if}
								<!-- 막대 바로 위의 꼬리표. 값까지 적으면 옆 막대에 닿는 칸에서는 이름만 적는다(값은 막대의 팝업에 있다) -->
								{@const narrow = !chip.full && wide && column < NARROW_COLUMN}
								<span
									class={[
										'pointer-events-none absolute z-20 flex items-center gap-1.5 rounded-full p-0.5 text-xs font-bold whitespace-nowrap',
										chip.full && 'pr-3',
										narrow && 'text-[10px]',
										chip.align === 'start' && 'left-0',
										chip.align === 'end' && 'right-0',
										chip.align === 'center' && 'left-1/2 -translate-x-1/2',
										chip.kind === 'max'
											? 'bg-primary text-primary-foreground'
											: 'border border-border bg-card text-text-primary'
									]}
									style:bottom={`${chip.bottom}px`}
									data-extreme={chip.kind}
								>
									<span
										class={[
											'rounded-full py-px font-normal',
											chip.full ? 'px-2' : narrow ? 'px-1' : 'px-1.5',
											chip.kind === 'max' ? 'bg-primary-foreground text-primary' : 'bg-surface'
										]}
									>
										{chip.kind === 'max' ? '최고' : '최저'}
									</span>
									{#if chip.full}{formatMetricValue(metric, chip.value)}{/if}
								</span>
							{/if}
							{#if isEmpty(bar)}
								<!--
									운동을 안 한 0분인 날과 기록이 없는 날은 점선 테두리 칸으로 그린다(S15P31A403-404).
									비워 두면 평균 미만인 날이 화면에서 사라져 보이는 막대가 모두 평균 위에 있는 것처럼 읽히고,
									낮은 막대로 채우면 조금 한 날과 구분되지 않는다. 정확한 값(0분·기록 없음)은 팝업과 보조기기 글에 둔다
								-->
								<span
									class="h-3 w-full rounded-md border border-dashed border-muted-foreground/60"
									data-bar-title={`${bar.title} ${barText(bar)}`}
									data-empty-day
								></span>
							{:else if bar.value !== null}
								{#if bar.inProgress}
									<!-- 수집 중인 오늘도 다른 날과 같은 채운 막대로 그리고, 막대 위에 "오늘"이라고만 적는다 -->
									<span class="mb-1 text-xs whitespace-nowrap text-text-primary" data-in-progress
										>오늘</span
									>
								{/if}
								<span
									class={[
										'min-h-0.5 w-full rounded-md',
										average === null ? PLAIN_CLASS : TONE_CLASS[barTone(bar.value, average)]
									]}
									style:height={toPercent(bar.value)}
									data-bar-title={`${bar.title} ${barText(bar)}${bar.inProgress ? ' (수집 중)' : ''}`}
								></span>
							{/if}
							{#if hovered === bar.key}
								<div
									class={[
										'pointer-events-none absolute z-30 w-max rounded-xl border border-border bg-card px-3 py-2 text-left shadow-md',
										popupFlips ? 'right-full mr-2' : 'left-full ml-2'
									]}
									style:bottom={`clamp(0rem, calc(${scaleFraction(scale, bar.value ?? 0) * 100}% - ${POPUP_HEIGHT / 2}rem), calc(100% - ${POPUP_HEIGHT}rem))`}
									aria-hidden="true"
									data-bar-popup
								>
									<p class="text-card-title whitespace-nowrap">{barText(bar)}</p>
									<p class="mt-0.5 text-xs whitespace-nowrap text-text-secondary">
										{bar.title}{bar.inProgress ? ' · 수집 중' : ''}
									</p>
								</div>
							{/if}
						</div>
						<!-- 글자는 칸보다 넓을 수 있어 칸 폭을 늘리지 않고 가운데에 겹쳐 둔다. 넘겨 볼 때는 7칸뿐이라 모든 날짜를 적고, 휴대폰 폭에서만 하루 걸러 적는다 -->
						<span
							class="relative mt-2 block h-4 w-full text-xs text-text-primary"
							aria-hidden="true"
							data-axis-label
						>
							<span
								class={[
									'absolute left-1/2 -translate-x-1/2 whitespace-nowrap',
									!range.scrolls && !bar.labelCompact && 'max-sm:hidden',
									range.scrolls && !range.labelsOnNarrow(barIndex) && 'max-sm:hidden',
									!range.scrolls && !bar.labelWide && 'sm:hidden'
								]}
							>
								{bar.axisLabel}
							</span>
						</span>
						<span class="sr-only">
							{bar.title}
							{bar.value !== null ? formatMetricValue(metric, bar.value) : '기록 없음'}
							{bar.inProgress ? '수집 중' : ''}
						</span>
					</li>
				{/each}
			</ol>
			<ChartScrollBar {range} />
			{#if range.scrolls}
				<!-- 더 넘길 날이 있는 쪽 가장자리를 옅게 흐려 이어진다는 것을 알린다 -->
				<span
					class={[
						'pointer-events-none absolute top-0 bottom-6 left-0 z-20 w-4 bg-linear-to-r from-surface to-transparent transition-opacity',
						range.first === 0 && 'opacity-0'
					]}
					aria-hidden="true"
				></span>
				<span
					class={[
						'pointer-events-none absolute top-0 right-0 bottom-6 z-20 w-4 bg-linear-to-l from-surface to-transparent transition-opacity',
						range.first >= range.lastStart && 'opacity-0'
					]}
					aria-hidden="true"
				></span>
			{/if}
		</div>

		{#if comparison && ends}
			<!-- 평균선 끝의 이름표. 좁은 화면에서는 자리가 없어 위쪽 범례로 대신한다 -->
			<div class="relative hidden h-44 w-40 shrink-0 text-xs sm:block" data-average-ends>
				<span
					class="absolute left-0 flex translate-y-1/2 items-center gap-1.5 rounded-full border border-border bg-card p-0.5 pr-3 font-bold whitespace-nowrap"
					style:bottom={`${ends.before}px`}
				>
					<span class="rounded-full bg-surface px-2 py-px font-normal">이전 기간</span>
					{formatMetricValue(metric, comparison.average)}
				</span>
				<span
					class="absolute left-0 flex translate-y-1/2 items-center gap-1.5 rounded-full bg-primary-soft p-0.5 pr-3 font-bold whitespace-nowrap text-primary"
					style:bottom={`${ends.current}px`}
				>
					<span class="rounded-full bg-card px-2 py-px font-normal">이번 기간</span>
					{formatMetricValue(metric, average ?? 0)}
				</span>
				<span
					class="absolute left-1.5 whitespace-nowrap text-text-primary"
					style:bottom={`${ends.delta}px`}
				>
					이전 기간 대비
					<b class={comparison.reference ? undefined : 'text-primary'}>{comparison.delta}</b>
				</span>
			</div>
		{/if}
	</div>
</figure>
