<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { chartScale, formatAxisValue, scaleFraction } from '../chart';
	import type { ChartBar } from '../evidence-view';
	import { formatMetricValue } from '../format';
	import type { SleepNight } from '../types';
	import { WeekScroll, type RangeView } from '../week-scroll.svelte';
	import ChartRangeControls from './ChartRangeControls.svelte';
	import ChartScrollBar from './ChartScrollBar.svelte';

	/**
	 * 날짜별 수면 단계를 쌓은 막대(S15P31A403-404). 단계 평균은 숫자 네 개로만 보여서 날마다 어땠는지 알 수 없었다.
	 * - 아래부터 깊은 잠, 렘수면, 얕은 잠 순으로 쌓는다. 깬 시간은 잠이 아니라 넣지 않는다
	 * - 단계 기록이 없는 날(일부 워치)은 막대를 비운다. 없는 값을 0으로 그리지 않는다
	 * - 축 날짜는 일별 막대 차트와 같은 칸을 쓴다(bars 는 일별 차트의 막대, nights 는 깬 날 기준)
	 * - 구간에 마우스를 올리면 그 단계의 시간이 옆에 뜬다(S15P31A403-418). 412 에서는 눌러야 떴는데,
	 *   건강 화면 차트처럼 올리기만 해도 뜨는 쪽이 맞다는 팀 의견으로 바꿨다. 키보드는 Tab 으로 차트에
	 *   한 번만 들어오고 방향키로 구간을 옮기며, 포커스가 있는 구간의 시간이 뜬다
	 * - 기간이 일주일을 넘으면 최근 7일만 먼저 보이고 나머지는 가로로 넘겨 본다(일별 차트 공통 규칙, week-scroll).
	 *   세로축은 기간 전체 기준이라 넘겨도 눈금이 바뀌지 않는다
	 * 라이브러리 없이 HTML 로 그린다(막대 차트와 같은 방침)
	 */
	type Props = {
		bars: ChartBar[];
		nights: SleepNight[];
		/**
		 * 지금 보기(일주일·기간 전체). 근거 창이 탭마다 기억해 두었다가 그 탭으로 돌아오면 같은 보기로 시작하게 한다(S15P31A403-439).
		 * 넘기지 않으면 일주일로 시작한다
		 */
		view?: RangeView;
		/** 차트 설명. 화면에는 보이지 않고 보조기기에 읽힌다 */
		label: string;
	};

	let { bars, nights, view = $bindable('week'), label }: Props = $props();

	const STAGES = [
		{ key: 'light_minutes', name: '얕은 잠', tone: 'bg-coral-sub' },
		{ key: 'rem_minutes', name: '렘수면', tone: 'bg-coral-main' },
		{ key: 'deep_minutes', name: '깊은 잠', tone: 'bg-primary' }
	] as const;
	type StageKey = (typeof STAGES)[number]['key'];

	/** 팝업 높이(rem). 구간 가운데에 맞추되 차트 위아래로 나가지 않게 가두는 데 쓴다 */
	const POPUP_HEIGHT = 4.75;

	type Part = {
		id: string;
		stage: StageKey;
		name: string;
		tone: string;
		minutes: number;
	};
	type Column = { bar: ChartBar; parts: Part[]; total: number };

	const columns = $derived(
		bars.map((bar): Column => {
			const night = nights.find((item) => item.date === bar.key);
			// 화면에는 위에서 아래로(얕은 잠 → 깊은 잠) 놓인다
			const parts = night
				? STAGES.flatMap((stage): Part[] => {
						const minutes = night[stage.key];
						return minutes === null
							? []
							: [
									{
										id: `${bar.key}|${stage.key}`,
										stage: stage.key,
										name: stage.name,
										tone: stage.tone,
										minutes
									}
								];
					})
				: [];
			return { bar, parts, total: parts.reduce((sum, part) => sum + part.minutes, 0) };
		})
	);
	const scale = $derived(
		chartScale(
			'sleeps',
			columns.map((column) => (column.total > 0 ? column.total : null)),
			null
		)
	);
	const toPercent = (value: number) => `${scaleFraction(scale, value) * 100}%`;
	const gapClass = $derived(bars.length > 14 ? 'gap-px sm:gap-1' : 'gap-2');
	const describe = (column: Column) =>
		column.total > 0
			? [...column.parts]
					.reverse()
					.map((part) => `${part.name} ${formatMetricValue('sleeps', part.minutes)}`)
					.join(', ')
			: '단계 기록 없음';

	let figure: HTMLElement | undefined = $state();
	/** 눈금선과 팝업이 놓이는 칸(막대 영역). 팝업 위치를 이 칸 기준으로 잡는다 */
	let plot: HTMLElement | undefined = $state();

	const range = new WeekScroll(
		() => bars.length,
		() => place(),
		() => columns.findLastIndex((column) => column.total > 0)
	);
	// 창이 기억해 둔 보기로 시작하고, 사용자가 바꾸면 창에 알린다
	range.view = untrack(() => view);
	$effect(() => {
		view = range.view;
	});
	$effect(() => range.reset());
	$effect(() => range.observe());

	/* ── 구간의 시간 팝업 ── */

	/** 마우스가 올라가 있는 구간 */
	let hovered = $state<string | null>(null);
	/** 키보드 포커스로 팝업이 열린 구간 */
	let keyed = $state<string | null>(null);
	/** Tab 으로 들어올 때 포커스를 받는 구간. 방향키로 옮기면 따라간다 */
	let active = $state<string | null>(null);
	const allIds = $derived(columns.flatMap((column) => column.parts.map((part) => part.id)));
	// 근거가 바뀌어 없어진 구간을 가리키지 않게 한다
	const openId = $derived.by(() => {
		const id = hovered ?? keyed;
		return id !== null && allIds.includes(id) ? id : null;
	});
	// 정해 둔 구간이 없으면 지금 화면에서 가장 앞선 날의 맨 아래 구간으로 들어온다
	const focusId = $derived(
		active !== null && allIds.includes(active)
			? active
			: (columns
					.slice(range.scrolls ? range.first : 0)
					.find((column) => column.parts.length > 0)
					?.parts.at(-1)?.id ?? null)
	);

	/** 팝업 자리. x 는 막대 영역의 왼쪽(뒤집히면 오른쪽) 끝에서, center 는 위에서 잰 px 이다 */
	let popup = $state<{ id: string; flip: boolean; x: number; center: number } | null>(null);
	const shown = $derived.by(() => {
		if (!popup) return null;
		for (const column of columns) {
			const part = column.parts.find((item) => item.id === popup?.id);
			if (part) return { ...popup, part, title: column.bar.title };
		}
		return null;
	});

	/**
	 * 팝업을 구간 옆에 놓는다. 막대가 가로로 넘어가는 칸 안에 있어서, 팝업은 그 바깥(막대 영역)에 두고
	 * 구간의 화면 위치를 재서 맞춘다. 오른쪽 끝에 가까운 구간은 왼쪽에 띄워 차트 밖으로 나가지 않게 한다
	 */
	function place() {
		const segment =
			openId === null
				? null
				: figure?.querySelector<HTMLElement>(`[data-stage-segment="${openId}"]`);
		if (!segment || !plot || openId === null) {
			popup = null;
			return;
		}
		const frame = plot.getBoundingClientRect();
		const box = segment.getBoundingClientRect();
		// 넘기는 중에 화면 밖으로 나간 구간에는 띄우지 않는다
		if (box.right <= frame.left + 1 || box.left >= frame.right - 1) {
			popup = null;
			return;
		}
		const flip = box.right - frame.left > frame.width * 0.75;
		popup = {
			id: openId,
			flip,
			x: flip ? frame.right - box.left + 8 : box.right - frame.left + 8,
			center: box.top + box.height / 2 - frame.top
		};
	}

	$effect(() => {
		void openId;
		untrack(place);
	});
	// 끌어서 넘기기 시작하면 올려 둔 팝업을 닫는다
	$effect(() => {
		if (range.free) hovered = null;
	});

	/**
	 * 방향키로 옮기는 동안에는 마우스를 무시한다. 차트가 넘어가면 가만히 있는 마우스 밑으로 다른 구간이 들어와,
	 * 키보드로 고른 구간이 아니라 그 구간의 팝업이 뜨기 때문이다. 마우스를 실제로 움직이면 다시 따른다
	 */
	let keyboardOnly = false;

	function handleHover(event: PointerEvent, id: string) {
		if (range.dragging) return;
		if (keyboardOnly) {
			if (event.movementX === 0 && event.movementY === 0) return;
			keyboardOnly = false;
		}
		hovered = id;
	}

	function handleLeave(event: PointerEvent, id: string) {
		// 터치는 손을 떼는 순간 벗어난 것으로 오므로 그대로 두고, 바깥을 누를 때 닫는다
		if (event.pointerType === 'touch') return;
		if (hovered === id) hovered = null;
	}

	function handleFocus(event: FocusEvent, id: string) {
		active = id;
		// 마우스로 눌러 생긴 포커스로는 띄우지 않는다. 마우스가 떠난 뒤에도 팝업이 남기 때문이다
		if (event.currentTarget instanceof HTMLElement && event.currentTarget.matches(':focus-visible'))
			keyed = id;
	}

	async function focusPart(id: string) {
		active = id;
		keyed = id;
		keyboardOnly = true;
		hovered = null;
		await tick();
		figure?.querySelector<HTMLElement>(`[data-stage-segment="${id}"]`)?.focus();
	}

	function handleKeydown(event: KeyboardEvent, columnIndex: number, partIndex: number) {
		if (event.key === 'Escape') {
			if (openId === null) return;
			// 근거 창까지 닫히지 않게 팝업만 닫는다
			event.preventDefault();
			event.stopPropagation();
			hovered = null;
			keyed = null;
			return;
		}
		const column = columns[columnIndex];
		let target: Part | undefined;
		if (event.key === 'ArrowUp') target = column.parts[partIndex - 1];
		else if (event.key === 'ArrowDown') target = column.parts[partIndex + 1];
		else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
			const step = event.key === 'ArrowRight' ? 1 : -1;
			for (let index = columnIndex + step; index >= 0 && index < columns.length; index += step) {
				const parts = columns[index].parts;
				if (parts.length === 0) continue;
				// 같은 단계가 있으면 그 단계로, 없으면 맨 아래 구간으로
				target = parts.find((part) => part.stage === column.parts[partIndex].stage) ?? parts.at(-1);
				break;
			}
		} else return;
		event.preventDefault();
		if (target) void focusPart(target.id);
	}

	function handleOutside(event: PointerEvent) {
		if (hovered === null) return;
		const target = event.target instanceof Element ? event.target : null;
		if (!target?.closest('[data-stage-segment]')) hovered = null;
	}
</script>

<svelte:document onpointerdown={handleOutside} />

<figure
	bind:this={figure}
	class="isolate rounded-2xl bg-surface px-5 pt-5 pb-4"
	aria-label={label}
	data-sleep-stage-bars
>
	<div class="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
		<!-- 글자를 키우면 한 줄에 다 들어가지 않아 줄을 바꾼다(S15P31A403-438 점검) -->
		<p class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-primary">
			{#each [...STAGES].reverse() as stage (stage.key)}
				<span class="flex items-center gap-1.5">
					<span class={['size-2.5 rounded-[3px]', stage.tone]} aria-hidden="true"
					></span>{stage.name}
				</span>
			{/each}
		</p>
		<ChartRangeControls {range} />
	</div>
	<div class="flex gap-3">
		<div class="relative h-44 w-8 shrink-0 text-right text-xs text-text-primary" aria-hidden="true">
			{#each scale.ticks as tick (tick)}
				<span class="absolute right-0 translate-y-1/2" style:bottom={toPercent(tick)}>
					{formatAxisValue('sleeps', tick)}
				</span>
			{/each}
		</div>
		<div class="relative min-w-0 flex-1">
			<div class="pointer-events-none absolute inset-x-0 top-0 h-44" aria-hidden="true">
				{#each scale.ticks as tick (tick)}
					<div
						class="absolute inset-x-0 border-t border-border"
						style:bottom={toPercent(tick)}
					></div>
				{/each}
			</div>
			<!-- 끌어서 넘기는 것은 마우스용 수단이고, 키보드는 구간에서 방향키를 누르면 차트가 따라 넘어간다 -->
			<ol
				bind:this={range.element}
				class={[
					'relative flex',
					range.scrolls ? range.scrollerClass : ['justify-around', gapClass]
				]}
				onscroll={range.onscroll}
				onpointerdown={range.onpointerdown}
				onpointermove={range.onpointermove}
				onpointerup={range.onpointerup}
				onpointercancel={range.onpointerup}
				data-range-scroller={range.scrolls ? '' : undefined}
			>
				{#each columns as column, columnIndex (column.bar.key)}
					<li
						class={[
							'flex min-w-0 flex-col items-center',
							range.scrolls ? 'shrink-0 grow-0 basis-[calc(100%/7)]' : 'w-full max-w-12'
						]}
						data-stage-day={column.bar.key}
					>
						<div class="flex h-44 w-full max-w-12 flex-col justify-end">
							{#if column.total > 0}
								<span
									class="flex w-full flex-col overflow-hidden rounded-md"
									style:height={toPercent(column.total)}
								>
									{#each column.parts as part, partIndex (part.id)}
										<!-- 구간 하나가 버튼이다(키보드 포커스를 받는다). 테두리는 칸 밖으로 잘리지 않게 안쪽에 그린다 -->
										<button
											type="button"
											class={[
												'w-full cursor-[inherit] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-text-primary',
												part.tone,
												part.id === openId && 'outline-2 -outline-offset-2 outline-text-primary'
											]}
											style:height={`${(part.minutes / column.total) * 100}%`}
											aria-label={`${column.bar.title} ${part.name} ${formatMetricValue('sleeps', part.minutes)}`}
											tabindex={part.id === focusId ? 0 : -1}
											data-stage-segment={part.id}
											onpointerenter={(event) => handleHover(event, part.id)}
											onpointermove={(event) => handleHover(event, part.id)}
											onpointerleave={(event) => handleLeave(event, part.id)}
											onfocus={(event) => handleFocus(event, part.id)}
											onblur={() => {
												if (keyed === part.id) keyed = null;
											}}
											onkeydown={(event) => handleKeydown(event, columnIndex, partIndex)}
										></button>
									{/each}
								</span>
							{/if}
						</div>
						<!-- 글자는 칸보다 넓을 수 있어 칸 폭을 늘리지 않고 가운데에 겹쳐 둔다. 넘겨 볼 때는 7칸뿐이라 모든 날짜를 적고, 휴대폰 폭에서만 하루 걸러 적는다 -->
						<span
							class="relative mt-2 block h-4 w-full text-xs text-text-primary"
							aria-hidden="true"
						>
							<span
								class={[
									'absolute left-1/2 -translate-x-1/2 whitespace-nowrap',
									!range.scrolls && !column.bar.labelCompact && 'max-sm:hidden',
									range.scrolls && !range.labelsOnNarrow(columnIndex) && 'max-sm:hidden',
									!range.scrolls && !column.bar.labelWide && 'sm:hidden'
								]}
							>
								{column.bar.axisLabel}
							</span>
						</span>
						<span class="sr-only">{column.bar.title} {describe(column)}</span>
					</li>
				{/each}
			</ol>
			<ChartScrollBar {range} />
			{#if range.scrolls}
				<!-- 더 넘길 날이 있는 쪽 가장자리를 옅게 흐려 이어진다는 것을 알린다 -->
				<span
					class={[
						'pointer-events-none absolute top-0 bottom-6 left-0 w-4 bg-linear-to-r from-surface to-transparent transition-opacity',
						range.first === 0 && 'opacity-0'
					]}
					aria-hidden="true"
				></span>
				<span
					class={[
						'pointer-events-none absolute top-0 right-0 bottom-6 w-4 bg-linear-to-l from-surface to-transparent transition-opacity',
						range.first >= range.lastStart && 'opacity-0'
					]}
					aria-hidden="true"
				></span>
			{/if}
			<div bind:this={plot} class="pointer-events-none absolute inset-x-0 top-0 z-30 h-44">
				{#if shown}
					<!--
						올린 구간 옆에 뜨는 팝업. 구간 가운데 높이에 맞추고 차트 위아래로 나가지 않게 가둔다.
						같은 내용이 구간 버튼의 이름에 있어 보조기기에는 읽히지 않게 한다
					-->
					<div
						class="absolute w-max rounded-xl border border-border bg-card px-3 py-2 text-left shadow-md"
						style:left={shown.flip ? undefined : `${shown.x}px`}
						style:right={shown.flip ? `${shown.x}px` : undefined}
						style:top={`clamp(0rem, calc(${shown.center}px - ${POPUP_HEIGHT / 2}rem), calc(100% - ${POPUP_HEIGHT}rem))`}
						aria-hidden="true"
						data-stage-popup
					>
						<p class="flex items-center gap-1.5 text-xs text-text-primary">
							<span class={['size-2 rounded-full', shown.part.tone]} aria-hidden="true"></span>
							{shown.part.name}
						</p>
						<p class="mt-0.5 text-card-title whitespace-nowrap">
							{formatMetricValue('sleeps', shown.part.minutes)}
						</p>
						<p class="mt-0.5 text-xs whitespace-nowrap text-text-secondary">
							{shown.title}
						</p>
					</div>
				{/if}
			</div>
		</div>
	</div>
</figure>
