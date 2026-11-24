<script lang="ts">
	import { untrack } from 'svelte';
	import { sleepClock, formatSleepMinutes } from '../sleep';
	import ActivityDataTooltip from './ActivityDataTooltip.svelte';
	import {
		sleepStageStyles,
		sleepStageOrder,
		sleepTimelinePosition,
		sleepVisibleSegment,
		sleepTimelineTicks,
		sleepMidnights,
		sleepKstDate,
		type SleepTimelineDay
	} from '../sleep-detail';
	let { timeline }: { timeline: SleepTimelineDay | undefined } = $props();
	const mainSleep = $derived(timeline?.mainSleep);
	const segments = $derived(timeline?.sleepSegments ?? []);
	const ticks = $derived(mainSleep ? sleepTimelineTicks(mainSleep) : []);
	let axis = $state<HTMLDivElement>();
	let labelMeasure = $state<HTMLDivElement>();
	let labelTicks = $state<string[]>([]);
	$effect(() => {
		const sleep = mainSleep;
		const candidates = ticks;
		const currentAxis = axis;
		const measure = labelMeasure;
		if (!sleep || !currentAxis || !measure) return;
		let active = true;
		function selectLabels() {
			if (!active || !measure || !currentAxis || !sleep) return;
			const widths = Array.from(measure.children, (node) => node.getBoundingClientRect().width);
			const width = currentAxis.clientWidth;
			const gap = 8;
			let previousRight = widths[0];
			const lastLeft = width - widths[1];
			const selected = candidates.filter((tick, index) => {
				const center = (sleepTimelinePosition(tick, sleep) / 100) * width;
				const half = widths[index + 2] / 2;
				if (center - half < previousRight + gap || center + half > lastLeft - gap) return false;
				previousRight = center + half;
				return true;
			});
			if (selected.join() !== labelTicks.join()) labelTicks = selected;
		}
		let frame = 0;
		const observer = new ResizeObserver(() => {
			cancelAnimationFrame(frame);
			frame = requestAnimationFrame(selectLabels);
		});
		observer.observe(currentAxis);
		observer.observe(measure);
		untrack(selectLabels);
		void document.fonts.ready.then(selectLabels);
		document.fonts.addEventListener('loadingdone', selectLabels);
		return () => {
			active = false;
			cancelAnimationFrame(frame);
			observer.disconnect();
			document.fonts.removeEventListener('loadingdone', selectLabels);
		};
	});
	const midnights = $derived(mainSleep ? sleepMidnights(mainSleep) : []);
	const hasUnclassified = $derived(
		segments.some((segment) => segment.stage === 'unknown' || segment.stage === 'unspecified')
	);
	const rows = $derived([
		...sleepStageOrder,
		...(hasUnclassified ? ['unclassified' as const] : [])
	]);
	const isUnclassified = (stage: string) => stage === 'unknown' || stage === 'unspecified';
	let chart = $state<HTMLElement>();
	let tooltip = $state<{ dismiss: () => void }>();
	const stageRow = (stage: string) => (isUnclassified(stage) ? 'unclassified' : stage);
	const visibleSegments = $derived(
		segments.map((segment) => (mainSleep ? sleepVisibleSegment(segment, mainSleep) : null))
	);
	const selectionLimits = $derived.by(() => {
		const limits: ({ left: number; right: number } | undefined)[] = [];
		for (const row of rows) {
			const peers = visibleSegments
				.flatMap((visible, index) =>
					visible && stageRow(segments[index].stage) === row ? [{ index, ...visible }] : []
				)
				.sort((a, b) => a.left + a.width / 2 - (b.left + b.width / 2));
			const boundary = (a: (typeof peers)[number], b: (typeof peers)[number]) =>
				a.left + a.width <= b.left
					? (a.left + a.width + b.left) / 2
					: (a.left + a.width / 2 + b.left + b.width / 2) / 2;
			for (const [index, peer] of peers.entries()) {
				limits[peer.index] = {
					left: index === 0 ? 0 : boundary(peers[index - 1], peer),
					right: index === peers.length - 1 ? 100 : boundary(peer, peers[index + 1])
				};
			}
		}
		return limits;
	});
	function guardAmbiguousPointer(event: MouseEvent) {
		if (event.type === 'click' && event.detail === 0) return;
		const target = event.target instanceof Element ? event.target : null;
		const row = target?.closest<HTMLElement>('[data-stage-row]');
		if (!row) return;
		const bounds = row.getBoundingClientRect();
		// 좌표가 반올림되어 다른 구간을 선택하지 않도록 좌우 1px 범위를 확인한다.
		const candidates = visibleSegments.flatMap((visible, index) => {
			if (!visible || stageRow(segments[index].stage) !== row.dataset.stageRow) return [];
			const left = bounds.left + (visible.left / 100) * bounds.width;
			const right = left + (visible.width / 100) * bounds.width;
			return right > event.clientX - 1 && left < event.clientX + 1 ? [index] : [];
		});
		const point = target?.closest<HTMLElement>('[data-chart-point]');
		if (
			candidates.length > 1 ||
			(point && candidates.length === 1 && candidates[0] !== Number(point.dataset.chartPoint))
		) {
			event.preventDefault();
			event.stopPropagation();
			tooltip?.dismiss();
		}
	}
	const segmentLabels = $derived(
		segments.map((segment) =>
			segment.stage === 'unknown' || segment.stage === 'unspecified'
				? '분류되지 않음'
				: sleepStageStyles[segment.stage].label
		)
	);
	const segmentMinutes = $derived(
		segments.map((segment) => (Date.parse(segment.endAt) - Date.parse(segment.startAt)) / 60000)
	);
	const segmentTime = (timestamp: string, short: boolean) =>
		`${sleepClock(timestamp)}${short ? `:${String(new Date(timestamp).getUTCSeconds()).padStart(2, '0')}` : ''}`;
	const segmentNotes = $derived(
		segments.map(
			(segment, index) =>
				`${segmentTime(segment.startAt, segmentMinutes[index] < 1)}~${segmentTime(segment.endAt, segmentMinutes[index] < 1)} · ${segmentMinutes[index] < 1 ? '1분 미만' : `${Math.round(segmentMinutes[index])}분`}`
		)
	);
</script>

<section
	aria-labelledby="sleep-stage-title"
	class="min-w-0 rounded-3xl border border-border bg-card p-4 sm:p-8"
>
	<div class="flex flex-wrap items-start justify-between gap-5">
		<div>
			<h3 id="sleep-stage-title" class="text-card-title-lg">수면 단계</h3>
		</div>
		<ul
			aria-label="수면 단계 범례"
			class="flex flex-wrap gap-x-5 gap-y-3 text-xs text-text-secondary sm:gap-x-8"
		>
			{#each sleepStageOrder as stage (stage)}
				<li class="flex items-center gap-2">
					<span
						class={['size-3 rounded-sm', stage === 'awake' ? 'border border-border' : '']}
						style:background={sleepStageStyles[stage].color}
						aria-hidden="true"
					></span>{sleepStageStyles[stage].label}
				</li>
			{/each}
		</ul>
	</div>
	{#if !mainSleep || segments.length === 0}
		<p class="grid min-h-56 place-items-center text-center text-sm text-text-secondary">
			해당 날짜의 수면 단계 기록이 없어요.
		</p>
	{:else}
		<figure
			bind:this={chart}
			aria-label="시간대별 수면 단계"
			class="mt-8 min-w-0"
			onpointerovercapture={guardAmbiguousPointer}
			onpointermovecapture={guardAmbiguousPointer}
			onpointerdowncapture={guardAmbiguousPointer}
			onclickcapture={guardAmbiguousPointer}
		>
			<div
				class="grid grid-cols-[64px_minmax(0,1fr)] gap-x-3 sm:grid-cols-[120px_minmax(0,1fr)] sm:gap-x-8"
			>
				<div class="pt-6 text-xs font-semibold text-text-secondary sm:text-sm">
					{#each rows as stage (stage)}<div class="flex h-12 items-center">
							{stage === 'unclassified' ? '분류되지 않음' : sleepStageStyles[stage].label}
						</div>{/each}
				</div>
				<div class="min-w-0">
					<div class="relative pt-6">
						{#each ticks as tick (tick)}<span
								aria-hidden="true"
								class="absolute inset-y-6 border-l border-dashed border-border"
								style:left={`${sleepTimelinePosition(tick, mainSleep)}%`}
							></span>{/each}
						{#each rows as stage (stage)}
							<div data-stage-row={stage} class="relative h-12 border-b border-border">
								{#each segments as segment, index (index)}
									{@const visible = visibleSegments[index]}
									{@const limits = selectionLimits[index]}
									{#if visible && limits && (segment.stage === stage || (stage === 'unclassified' && isUnclassified(segment.stage)))}
										<div
											aria-hidden="true"
											data-stage={segment.stage}
											class="absolute top-3 h-6 rounded-md"
											style:left={`${visible.left}%`}
											style:width={`${visible.width}%`}
											style:background={stage === 'unclassified'
												? 'var(--color-text-disabled)'
												: sleepStageStyles[stage].color}
										></div>
										<button
											type="button"
											data-chart-point={index}
											aria-label={`${segmentLabels[index]} ${segmentNotes[index]}`}
											class="absolute top-1.5 h-9 cursor-pointer rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info"
											style:left={`max(${limits.left}%, min(${visible.left}%, clamp(0px, calc(${visible.left + visible.width / 2}% - 12px), calc(100% - 24px))))`}
											style:right={`max(${100 - limits.right}%, min(${100 - visible.left - visible.width}%, clamp(0px, calc(${100 - visible.left - visible.width / 2}% - 12px), calc(100% - 24px))))`}
										></button>
									{/if}
								{/each}
							</div>
						{/each}
						{#each midnights as midnight (midnight)}
							<div
								aria-label={`${sleepKstDate(midnight)} 자정 경계`}
								class="pointer-events-none absolute inset-y-0 border-l border-dashed border-brand"
								style:left={`${sleepTimelinePosition(midnight, mainSleep)}%`}
							>
								<span
									class={[
										'absolute top-0 text-xs font-bold whitespace-nowrap text-primary',
										sleepTimelinePosition(midnight, mainSleep) > 80 ? 'right-1' : 'left-1'
									]}>{sleepKstDate(midnight).slice(5).replace('-', '/')}</span
								>
							</div>
						{/each}
					</div>
					<div
						bind:this={labelMeasure}
						aria-hidden="true"
						class="pointer-events-none invisible absolute h-0 overflow-hidden text-xs whitespace-nowrap text-text-secondary"
					>
						<span class="inline-block">{sleepClock(mainSleep.startAt)}</span>
						<span class="inline-block">{sleepClock(mainSleep.endAt)}</span>
						{#each ticks as tick (tick)}<span class="inline-block">{sleepClock(tick)}</span>{/each}
					</div>
					<div
						bind:this={axis}
						aria-hidden="true"
						data-sleep-time-axis
						class="relative mt-5 h-5 text-xs text-text-secondary"
					>
						<span class="absolute left-0">{sleepClock(mainSleep.startAt)}</span>
						{#each labelTicks as tick (tick)}<span
								class="absolute -translate-x-1/2"
								style:left={`${sleepTimelinePosition(tick, mainSleep)}%`}>{sleepClock(tick)}</span
							>{/each}
						<span class="absolute right-0">{sleepClock(mainSleep.endAt)}</span>
					</div>
				</div>
			</div>
			<ActivityDataTooltip
				bind:this={tooltip}
				root={chart}
				dates={segments.map((segment) => sleepKstDate(segment.startAt))}
				values={segmentMinutes}
				label=""
				unit="분"
				messages={segmentLabels}
				notes={segmentNotes}
			/>
			<figcaption class="sr-only">
				취침 {sleepClock(mainSleep.startAt)}, 기상 {sleepClock(mainSleep.endAt)}.
				{#each segments as segment, index (index)}{segment.stage === 'unknown' ||
					segment.stage === 'unspecified'
						? '분류되지 않음'
						: sleepStageStyles[segment.stage].label}: {sleepClock(segment.startAt)} ~ {sleepClock(
						segment.endAt
					)}, {formatSleepMinutes(
						(Date.parse(segment.endAt) - Date.parse(segment.startAt)) / 60000
					)}.
				{/each}
			</figcaption>
		</figure>
	{/if}
</section>
