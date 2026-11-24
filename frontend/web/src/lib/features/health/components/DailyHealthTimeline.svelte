<script lang="ts">
	import { onDestroy } from 'svelte';
	import HealthChartTooltip from './HealthChartTooltip.svelte';
	import { revealHealthFocus } from '../health-viewport';
	import { barMaximum, barOpacity } from '../health-bar-color';
	import {
		clipDayInterval,
		dayHeartSegments,
		dayHourCenter,
		dayTimeTicks,
		dayTimeX,
		dayTimelineStart,
		type HealthTimelineDay
	} from '../day-detail';
	import { formatSleepMinutes, sleepClock } from '../sleep';
	import { sleepKstDate, sleepStageStyles } from '../sleep-detail';
	let { timeline }: { timeline: HealthTimelineDay } = $props();
	const rangeStart = $derived(dayTimelineStart(timeline));
	const ticks = $derived(rangeStart < 0 ? [rangeStart, ...dayTimeTicks] : dayTimeTicks);
	const hiddenMidnight = $derived.by(() => {
		if (!timeline.mainSleep) return null;
		const bedtime = Date.parse(timeline.mainSleep.startAt);
		const sleepDayStart = Date.parse(`${sleepKstDate(timeline.mainSleep.startAt)}T00:00:00+09:00`);
		if (bedtime <= sleepDayStart + 21 * 3600000) return null;
		return (sleepDayStart + 86400000 - Date.parse(`${timeline.date}T00:00:00+09:00`)) / 60000;
	});
	const timeX = (minute: number) => dayTimeX(minute, rangeStart);
	const timeWidth = (minutes: number) => timeX(rangeStart + minutes);
	const minuteLabel = (minute: number) =>
		timestampLabel(
			new Date(Date.parse(`${timeline.date}T00:00:00+09:00`) + minute * 60000).toISOString()
		);
	const sleepIntervals = $derived(
		(timeline.sleepSegments.length
			? timeline.sleepSegments
			: timeline.mainSleep
				? [{ ...timeline.mainSleep, stage: 'unspecified' as const }]
				: []
		).flatMap((segment) => {
			const clipped = clipDayInterval(timeline.date, segment.startAt, segment.endAt, rangeStart);
			return clipped ? [{ ...segment, ...clipped }] : [];
		})
	);
	const exerciseIntervals = $derived(
		timeline.exerciseSegments.flatMap((segment) => {
			const clipped = clipDayInterval(timeline.date, segment.startAt, segment.endAt, rangeStart);
			return clipped ? [{ ...segment, ...clipped }] : [];
		})
	);
	const mainSleep = $derived(
		timeline.mainSleep
			? clipDayInterval(
					timeline.date,
					timeline.mainSleep.startAt,
					timeline.mainSleep.endAt,
					rangeStart
				)
			: null
	);
	const heartGroups = $derived(dayHeartSegments(timeline.hourlyHeartRates));
	const heartValues = $derived(heartGroups.flatMap((group) => group.map((point) => point.value)));
	const heartMin = $derived(
		heartValues.length ? Math.floor((Math.min(...heartValues) - 5) / 10) * 10 : 0
	);
	const heartMax = $derived(
		heartValues.length ? Math.ceil((Math.max(...heartValues) + 5) / 10) * 10 : 100
	);
	const heartY = (value: number) => 24 + ((heartMax - value) / (heartMax - heartMin)) * 64;
	const stepMax = $derived(Math.max(1, ...(timeline.hourlySteps ?? [])));
	const stepColorMaximum = $derived(barMaximum(timeline.hourlySteps ?? []));
	const presentStages = $derived([...new Set(sleepIntervals.map((interval) => interval.stage))]);
	const tooltipId = $props.id();
	let selectedKey = $state<string | null>(null);
	let isPinned = $state(false);
	let tooltipAnchor = $state<HTMLElement>();
	let card = $state<HTMLElement>();
	let plot = $state<HTMLElement>();
	let plotWidth = $state(0);
	let axis = $state<HTMLDivElement>();
	let labelWidths = $state(new Map<number, number>());
	const tickLabel = (minute: number) =>
		rangeStart < 0 && (minute === rangeStart || minute === 0 || minute === 1440)
			? minuteLabel(minute).slice(5)
			: clock(minute);
	const visibleTicks = $derived.by(() => {
		const boundaries = rangeStart < 0 ? [rangeStart, 0, 1440] : [0, 1440];
		if (!plotWidth || !labelWidths.size) return boundaries;
		// Prefer an even cadence; text widths decide whether 3, 6 or 12 hours fit.
		for (const interval of [180, 360, 720, 1440]) {
			const candidates = ticks.filter((minute) => minute <= 0 || minute % interval === 0);
			const row = candidates.filter((minute) => rangeStart === 0 || minute !== rangeStart);
			const bounds = row.map((minute) => {
				const width = labelWidths.get(minute) ?? 0;
				const x = (timeX(minute) / 100) * plotWidth;
				const left = minute === 0 ? x : minute === 1440 ? x - width : x - width / 2;
				return { left, right: left + width };
			});
			if (bounds.every((bound, index) => index === 0 || bound.left >= bounds[index - 1].right + 8))
				return candidates;
		}
		return boundaries;
	});
	$effect(() => {
		if (!plot || !axis) return;
		void ticks;
		void timeline.date;
		const current = plot;
		const labels = [...axis.querySelectorAll<HTMLElement>('[data-time-minute]')];
		const measure = () => {
			plotWidth = current.clientWidth;
			labelWidths = new Map(
				labels.map((label) => [
					Number(label.dataset.timeMinute),
					label.getBoundingClientRect().width
				])
			);
		};
		const observer = new ResizeObserver(measure);
		observer.observe(current);
		for (const label of labels) observer.observe(label);
		measure();
		return () => observer.disconnect();
	});
	let keyboardNavigation = false;
	let pointerPosition: { x: number; y: number } | undefined;
	let dismissedPointer: { x: number; y: number } | undefined;
	let focusFrame = 0;
	onDestroy(() => cancelAnimationFrame(focusFrame));
	function handleFocusIn(event: FocusEvent) {
		const target = event.target;
		if (!(target instanceof HTMLElement) || !target.hasAttribute('data-interval-trigger')) return;
		keyboardNavigation = target.matches(':focus-visible');
		if (
			!keyboardNavigation ||
			(event.relatedTarget instanceof Node &&
				document.getElementById(tooltipId)?.contains(event.relatedTarget))
		)
			return;
		cancelAnimationFrame(focusFrame);
		focusFrame = requestAnimationFrame(() => {
			if (document.activeElement === target) revealHealthFocus(target);
		});
	}
	function handleKeydown(event: KeyboardEvent) {
		const panel = document.getElementById(tooltipId);
		const active = document.activeElement;
		if (event.key === 'Escape') {
			dismissTooltip();
		}
		if (event.key === 'Tab') {
			keyboardNavigation = true;
			if (panel?.dataset.scrollable !== 'true') return;
			if (active === tooltipAnchor && !event.shiftKey) {
				event.preventDefault();
				panel.focus({ preventScroll: true });
			} else if (panel.contains(active) && tooltipAnchor) {
				const controls = [
					...(card?.querySelectorAll<HTMLElement>('[data-interval-trigger]') ?? [])
				];
				const next = event.shiftKey ? tooltipAnchor : controls[controls.indexOf(tooltipAnchor) + 1];
				if (next) {
					event.preventDefault();
					next.focus();
					revealHealthFocus(next);
				}
			}
		} else if (
			[
				'ArrowLeft',
				'ArrowRight',
				'ArrowUp',
				'ArrowDown',
				'Home',
				'End',
				'PageUp',
				'PageDown',
				' '
			].includes(event.key) &&
			!panel?.contains(active)
		)
			keyboardNavigation = false;
	}
	function dismissTooltip() {
		dismissedPointer = pointerPosition;
		if (selectedKey !== null && tooltipAnchor && document.activeElement === tooltipAnchor)
			revealHealthFocus(tooltipAnchor);
		selectedKey = null;
		isPinned = false;
	}
	const shortExercises = $derived(
		exerciseIntervals.filter(
			(interval) => (timeWidth(interval.end - interval.start) / 100) * plotWidth < 28
		)
	);
	function positionTooltip(target: EventTarget | null) {
		if (!(target instanceof HTMLElement)) return;
		tooltipAnchor = target;
	}
	const intervalDetails = $derived([
		...sleepIntervals.map((interval) => ({
			...interval,
			key: `sleep:${interval.startAt}`,
			label: stageStyle(interval.stage).label
		})),
		...exerciseIntervals.map((interval) => ({
			...interval,
			key: `exercise:${interval.startAt}`,
			label: '운동',
			typeName: interval.typeName.trim() || '운동'
		}))
	]);
	const selectedInterval = $derived(
		intervalDetails.find((interval) => interval.key === selectedKey)
	);
	const selectedHour = $derived(
		selectedKey?.startsWith('heart:') ? Number(selectedKey.slice(6)) : null
	);
	const selectedHeart = $derived(
		selectedHour !== null ? timeline.hourlyHeartRates?.[selectedHour] : null
	);
	const selectedStepHour = $derived(
		selectedKey?.startsWith('steps:') ? Number(selectedKey.slice(6)) : null
	);
	const selectedSteps = $derived(
		selectedStepHour !== null ? timeline.hourlySteps?.[selectedStepHour] : null
	);
	function handleIntervalHover(event: PointerEvent, key: string) {
		if (event.pointerType === 'mouse') {
			if (dismissedPointer?.x === event.clientX && dismissedPointer.y === event.clientY) return;
			if (
				keyboardNavigation &&
				(!pointerPosition ||
					(pointerPosition.x === event.clientX && pointerPosition.y === event.clientY))
			)
				return;
			if (guardExercisePointer(event)) return;
			positionTooltip(event.currentTarget);
			selectedKey = key;
			isPinned = false;
		}
	}
	function handleIntervalLeave(event: PointerEvent) {
		if (
			event.pointerType === 'mouse' &&
			!isPinned &&
			!document.getElementById(tooltipId)?.contains(document.activeElement) &&
			event.currentTarget !== document.activeElement
		)
			selectedKey = null;
	}
	function handleIntervalClick(key: string, event: MouseEvent) {
		if (guardExercisePointer(event)) return;
		dismissedPointer = undefined;
		positionTooltip(event.currentTarget);
		if (isPinned && selectedKey === key) {
			selectedKey = null;
			isPinned = false;
		} else {
			selectedKey = key;
			isPinned = true;
		}
	}
	function guardExercisePointer(event: MouseEvent) {
		if (event.type === 'click' && event.detail === 0) return false;
		const target = event.currentTarget;
		if (!(target instanceof HTMLElement) || !target.hasAttribute('data-exercise-start'))
			return false;
		// Subpixel intervals can share a rounded screen coordinate. Use the separate
		// record buttons or keyboard focus when the pointer cannot distinguish them.
		const candidates = [
			...(plot?.querySelectorAll<HTMLElement>('[data-exercise-start]') ?? [])
		].filter((element) => {
			const bounds = element.getBoundingClientRect();
			return bounds.right > event.clientX - 1 && bounds.left < event.clientX + 1;
		});
		if (candidates.length > 1 || (candidates.length === 1 && candidates[0] !== target)) {
			event.preventDefault();
			selectedKey = null;
			isPinned = false;
			return true;
		}
		return false;
	}
	const daySleepStageColors = {
		awake: 'var(--color-surface)',
		light: 'var(--color-coral-sub)',
		rem: 'var(--color-coral-soft)',
		deep: 'var(--color-brand)'
	};
	function stageStyle(stage: string) {
		return stage === 'awake' || stage === 'rem' || stage === 'light' || stage === 'deep'
			? { ...sleepStageStyles[stage], color: daySleepStageColors[stage] }
			: { label: '수면 · 단계 정보 없음', color: 'var(--color-coral-soft)' };
	}
	const clock = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:00`;
	const timestampLabel = (timestamp: string) =>
		`${sleepKstDate(timestamp).replaceAll('-', '.')} ${sleepClock(timestamp)}`;
	const intervalClock = (interval: { startAt: string; endAt: string }, timestamp: string) =>
		`${sleepClock(timestamp)}${Date.parse(interval.endAt) - Date.parse(interval.startAt) < 60000 ? `:${String(new Date(timestamp).getUTCSeconds()).padStart(2, '0')}` : ''}`;
</script>

<svelte:window
	onkeydown={handleKeydown}
	onpointermove={(event) => {
		if (event.pointerType === 'mouse') {
			if (dismissedPointer?.x !== event.clientX || dismissedPointer.y !== event.clientY)
				dismissedPointer = undefined;
			pointerPosition = { x: event.clientX, y: event.clientY };
			keyboardNavigation = false;
		}
	}}
	onwheel={(event) => {
		if (event.target instanceof Element && event.target.closest('[data-timeline-scroll]'))
			keyboardNavigation = false;
	}}
	onpointerdown={(event) => {
		keyboardNavigation = false;
		if (
			event.target instanceof Element &&
			!event.target.closest('[data-interval-trigger], [data-interval-tooltip], [role="tooltip"]')
		) {
			selectedKey = null;
			isPinned = false;
		}
	}}
/>

<section
	bind:this={card}
	onfocusin={handleFocusIn}
	aria-labelledby="day-timeline-title"
	class="relative min-w-0 rounded-2xl border border-border bg-card p-4 sm:p-6"
>
	<h2 id="day-timeline-title" class="text-section-title">나의 하루</h2>
	<p class="mt-2 text-[13px] text-text-secondary">
		{minuteLabel(rangeStart)} ~ {minuteLabel(1440)} · 동일한 시간축으로 기록을 확인해요
	</p>
	{#if rangeStart < 0}<p class="mt-2 text-xs text-text-secondary">
			전날 취침부터 표시해요. 타임라인을 좌우로 움직여 확인해 주세요.
		</p>{/if}
	{#if timeline.isInProgress}<p class="mt-2 text-xs text-text-secondary">
			오늘 기록은 수집 중이에요.
		</p>{/if}
	<div
		class="mt-6 grid min-w-0 grid-cols-[44px_minmax(0,1fr)] gap-x-3 sm:grid-cols-[92px_minmax(0,1fr)] sm:gap-x-6"
	>
		<div aria-hidden="true" class="text-xs font-bold sm:text-sm">
			<div class="flex h-16 items-center">구간</div>
			<div class="flex h-32 items-center">심박수</div>
			<div class="flex h-20 items-center">걸음 수</div>
		</div>
		<!-- svelte-ignore a11y_no_noninteractive_tabindex (시간축을 키보드 방향키로 스크롤할 수 있게 한다) -->
		<div
			class="min-w-0 overflow-x-auto focus-visible:outline-2 focus-visible:outline-ring"
			tabindex="0"
			role="region"
			aria-label="하루 기록 시간축"
			data-timeline-scroll
			onscroll={() => {
				if (
					keyboardNavigation &&
					(document.activeElement === tooltipAnchor ||
						document.getElementById(tooltipId)?.contains(document.activeElement))
				)
					return;
				selectedKey = null;
				isPinned = false;
			}}
		>
			<div
				bind:this={plot}
				class="day-time-plot relative min-w-0"
				data-range-start={rangeStart}
				style:width={rangeStart < 0
					? `calc(max(100%, 720px) * ${(1440 - rangeStart) / 1440})`
					: '100%'}
			>
				<div aria-hidden="true" class="pointer-events-none absolute inset-x-0 top-0 bottom-13">
					{#each ticks as minute (minute)}<div
							class="absolute top-0 bottom-0 border-l border-border"
							data-grid-minute={minute}
							style:left={`${timeX(minute)}%`}
							style:transform={minute === 1440 ? 'translateX(-100%)' : undefined}
						></div>{/each}
				</div>
				<div class="relative h-16" aria-label="수면과 운동 구간">
					{#each sleepIntervals as interval (interval.startAt)}<button
							type="button"
							data-interval-trigger
							aria-label={`${stageStyle(interval.stage).label} ${sleepClock(interval.startAt)} ~ ${sleepClock(interval.endAt)} 상세`}
							aria-describedby={selectedKey === `sleep:${interval.startAt}` ? tooltipId : undefined}
							onpointerenter={(event) => handleIntervalHover(event, `sleep:${interval.startAt}`)}
							onpointerleave={handleIntervalLeave}
							onfocus={(event) => {
								positionTooltip(event.currentTarget);
								selectedKey = `sleep:${interval.startAt}`;
								isPinned = false;
							}}
							onblur={(event) => {
								if (
									event.relatedTarget instanceof Element &&
									event.relatedTarget.closest('[role="tooltip"]')
								)
									return;
								if (!isPinned) selectedKey = null;
							}}
							onclick={(event) => handleIntervalClick(`sleep:${interval.startAt}`, event)}
							data-sleep-start={interval.start}
							data-sleep-end={interval.end}
							class="absolute top-3 h-9 cursor-pointer focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-ring"
							style:left={`${timeX(interval.start)}%`}
							style:width={`${timeWidth(interval.end - interval.start)}%`}
							style:background={stageStyle(interval.stage).color}
						></button>{/each}
					{#if mainSleep && timeline.mainSleep}<span
							aria-hidden="true"
							class="sleep-summary pointer-events-none absolute top-3 flex h-9 items-center text-[10px] font-medium sm:text-xs"
							style:left={`${timeX(mainSleep.start)}%`}
							style:width={`${timeWidth(mainSleep.end - mainSleep.start)}%`}
							><span class="sleep-name truncate rounded-sm bg-card/90 px-1"
								>수면 <span class="sleep-clock ml-1"
									>{sleepClock(timeline.mainSleep.startAt)} ~ {sleepClock(
										timeline.mainSleep.endAt
									)}</span
								></span
							><span class="sleep-short rounded-sm bg-card/90" aria-hidden="true">수면</span></span
						>{/if}
					{#each exerciseIntervals as interval (interval.startAt)}<button
							type="button"
							data-interval-trigger
							aria-label={`${interval.typeName.trim() || '운동'} ${sleepClock(interval.startAt)} ~ ${sleepClock(interval.endAt)} 상세`}
							aria-describedby={selectedKey === `exercise:${interval.startAt}`
								? tooltipId
								: undefined}
							onpointerenter={(event) => handleIntervalHover(event, `exercise:${interval.startAt}`)}
							onpointerleave={handleIntervalLeave}
							onfocus={(event) => {
								positionTooltip(event.currentTarget);
								selectedKey = `exercise:${interval.startAt}`;
								isPinned = false;
							}}
							onblur={(event) => {
								if (
									event.relatedTarget instanceof Element &&
									event.relatedTarget.closest('[role="tooltip"]')
								)
									return;
								if (!isPinned) selectedKey = null;
							}}
							onclick={(event) => handleIntervalClick(`exercise:${interval.startAt}`, event)}
							data-exercise-start={interval.start}
							onpointerdown={guardExercisePointer}
							class="exercise-interval absolute top-3 flex h-9 cursor-pointer items-center justify-center rounded-sm bg-success-badge text-[10px] font-normal focus-visible:z-10 focus-visible:outline-2 focus-visible:outline-ring sm:text-xs"
							style:left={`${timeX(interval.start)}%`}
							style:width={`${timeWidth(interval.end - interval.start)}%`}
						>
							<span class="exercise-name overflow-hidden px-0.5 whitespace-nowrap">운동</span>
						</button>{/each}
					{#if !sleepIntervals.length && !exerciseIntervals.length}<p
							class="absolute inset-0 flex items-center justify-center text-xs text-text-secondary"
						>
							구간 기록 없음
						</p>{/if}
				</div>
				<div class="relative h-32" aria-label="시간대별 평균 심박수">
					<svg
						viewBox="0 0 100 100"
						preserveAspectRatio="none"
						class="block h-full w-full"
						aria-hidden="true"
					>
						{#each heartGroups.filter((group) => group.length > 1) as group (group[0].minute)}<polyline
								data-heart-segment
								points={group
									.map((point) => `${timeX(point.minute)},${heartY(point.value)}`)
									.join(' ')}
								fill="none"
								stroke="var(--color-brand)"
								stroke-width="2"
								vector-effect="non-scaling-stroke"
								stroke-linejoin="round"
								stroke-linecap="round"
							/>{/each}
					</svg>
					{#each heartGroups.flat() as point (point.minute)}{@const hour = Math.floor(
							point.minute / 60
						)}<button
							type="button"
							data-interval-trigger
							data-heart-point={point.value}
							aria-label={`${clock(hour * 60)} 평균 심박 ${point.value} bpm 상세`}
							aria-describedby={selectedHour === hour ? tooltipId : undefined}
							onpointerenter={(event) => handleIntervalHover(event, `heart:${hour}`)}
							onpointerleave={handleIntervalLeave}
							onfocus={(event) => {
								positionTooltip(event.currentTarget);
								selectedKey = `heart:${hour}`;
								isPinned = false;
							}}
							onblur={(event) => {
								if (
									event.relatedTarget instanceof Element &&
									event.relatedTarget.closest('[role="tooltip"]')
								)
									return;
								if (!isPinned) selectedKey = null;
							}}
							onclick={(event) => handleIntervalClick(`heart:${hour}`, event)}
							style:width={`${timeWidth(60)}%`}
							class="absolute grid h-7 max-w-7 -translate-x-1/2 -translate-y-1/2 cursor-pointer place-items-center rounded-full focus-visible:outline-2 focus-visible:outline-ring"
							style:left={`${timeX(point.minute)}%`}
							style:top={`${heartY(point.value)}%`}
						>
							<span class="size-1.5 rounded-full bg-brand"></span>
						</button>{/each}
					{#if !heartValues.length}<p
							class="absolute inset-0 flex items-center justify-center text-xs text-text-secondary"
						>
							시간대별 심박 기록 없음
						</p>{/if}
				</div>
				<div class="relative h-20 border-b border-border" aria-label="시간대별 걸음 수">
					{#if timeline.hourlySteps !== null}<svg
							viewBox="0 0 100 100"
							preserveAspectRatio="none"
							class="block h-full w-full"
							aria-hidden="true"
						>
							{#each timeline.hourlySteps as value, hour (hour)}<rect
									data-step-hour={hour}
									data-step-value={value}
									x={timeX(dayHourCenter(hour) - 22)}
									y={100 - (value / stepMax) * 88}
									width={timeWidth(44)}
									height={(value / stepMax) * 88}
									rx="0.35"
									fill="var(--color-primary-light)"
									opacity={barOpacity(value, stepColorMaximum)}
								/>{/each}
						</svg>
						{#each timeline.hourlySteps as value, hour (hour)}<button
								type="button"
								data-interval-trigger
								data-step-trigger={hour}
								aria-label={`${clock(hour * 60)} ~ ${clock((hour + 1) * 60)} 걸음 수 ${value.toLocaleString('ko-KR')}보 상세`}
								aria-describedby={selectedStepHour === hour ? tooltipId : undefined}
								onpointerenter={(event) => handleIntervalHover(event, `steps:${hour}`)}
								onpointerleave={handleIntervalLeave}
								onfocus={(event) => {
									positionTooltip(event.currentTarget);
									selectedKey = `steps:${hour}`;
									isPinned = false;
								}}
								onblur={(event) => {
									if (
										event.relatedTarget instanceof Element &&
										event.relatedTarget.closest('[role="tooltip"]')
									)
										return;
									if (!isPinned) selectedKey = null;
								}}
								onclick={(event) => handleIntervalClick(`steps:${hour}`, event)}
								class="absolute inset-y-0 cursor-pointer rounded-sm hover:bg-brand/5 focus-visible:outline-2 focus-visible:outline-ring"
								style:left={`${timeX(hour * 60)}%`}
								style:width={`${timeWidth(60)}%`}
							></button>{/each}
					{:else}<p
							class="absolute inset-0 flex items-center justify-center text-xs text-text-secondary"
						>
							시간대별 걸음 기록 없음
						</p>{/if}
				</div>
				<div
					bind:this={axis}
					aria-hidden="true"
					class="relative mt-3 h-10 text-xs text-text-secondary"
				>
					{#each ticks as minute (minute)}<span
							data-time-minute={minute}
							class={[
								'absolute whitespace-nowrap',
								rangeStart < 0 && minute !== rangeStart ? 'top-4' : ''
							]}
							style:left={`${timeX(minute)}%`}
							style:visibility={visibleTicks.includes(minute) && minute !== hiddenMidnight
								? undefined
								: 'hidden'}
							style:transform={`translateX(${minute === rangeStart || minute === 0 ? 0 : minute === 1440 ? -100 : -50}%)`}
							>{tickLabel(minute)}</span
						>{/each}
				</div>
			</div>
		</div>
	</div>
	{#if shortExercises.length}
		<div aria-label="짧은 운동 구간 선택" class="mt-3 flex flex-wrap gap-2">
			{#each shortExercises as interval (interval.startAt)}
				<button
					type="button"
					data-interval-trigger
					aria-label={`운동 ${intervalClock(interval, interval.startAt)} ~ ${intervalClock(interval, interval.endAt)} 상세`}
					aria-describedby={selectedKey === `exercise:${interval.startAt}` ? tooltipId : undefined}
					onpointerenter={(event) => handleIntervalHover(event, `exercise:${interval.startAt}`)}
					onpointerleave={handleIntervalLeave}
					onfocus={(event) => {
						positionTooltip(event.currentTarget);
						selectedKey = `exercise:${interval.startAt}`;
						isPinned = false;
					}}
					onblur={(event) => {
						if (
							event.relatedTarget instanceof Element &&
							event.relatedTarget.closest('[role="tooltip"]')
						)
							return;
						if (!isPinned) selectedKey = null;
					}}
					onclick={(event) => handleIntervalClick(`exercise:${interval.startAt}`, event)}
					class="min-h-9 cursor-pointer rounded-lg bg-success-badge px-3 text-xs font-normal focus-visible:outline-2 focus-visible:outline-ring"
				>
					운동 {intervalClock(interval, interval.startAt)}~{intervalClock(interval, interval.endAt)}
				</button>
			{/each}
		</div>
	{/if}
	{#if tooltipAnchor && (selectedInterval || selectedHeart || selectedSteps != null)}
		<HealthChartTooltip
			id={tooltipId}
			ondismiss={dismissTooltip}
			anchor={tooltipAnchor}
			container={card}
			date={timeline.date}
			label="하루 기록"
			value={null}
			unit=""
			wide={!!selectedInterval &&
				sleepKstDate(selectedInterval.startAt) !== sleepKstDate(selectedInterval.endAt)}
		>
			<div data-interval-tooltip>
				{#if selectedInterval}
					<p class="font-bold">{selectedInterval.label}</p>
					{#if 'typeName' in selectedInterval && selectedInterval.typeName !== '운동'}
						<p class="mt-1">{selectedInterval.typeName}</p>
					{/if}
					<p class="mt-1 flex gap-x-1 text-xs whitespace-nowrap text-text-secondary">
						<span data-interval-date>
							{sleepKstDate(selectedInterval.startAt).replaceAll(
								'-',
								'.'
							)}{#if sleepKstDate(selectedInterval.startAt) !== sleepKstDate(selectedInterval.endAt)}
								~ {sleepKstDate(selectedInterval.endAt).replaceAll('-', '.')}{/if}
						</span>
						<span data-interval-time class="whitespace-nowrap">
							{intervalClock(selectedInterval, selectedInterval.startAt)}~{intervalClock(
								selectedInterval,
								selectedInterval.endAt
							)}
						</span>
					</p>
					<p class="mt-1">
						구간 길이 {Date.parse(selectedInterval.endAt) - Date.parse(selectedInterval.startAt) <
						60000
							? '1분 미만'
							: formatSleepMinutes(
									Math.round(
										(Date.parse(selectedInterval.endAt) - Date.parse(selectedInterval.startAt)) /
											60000
									)
								)}
					</p>
					{#if selectedKey?.startsWith('sleep:') && timeline.mainSleep}<p
							class="mt-2 border-t border-border pt-2 text-xs text-text-secondary"
						>
							전체 수면 구간 {sleepClock(timeline.mainSleep.startAt)} ~ {sleepClock(
								timeline.mainSleep.endAt
							)} · {formatSleepMinutes(
								(Date.parse(timeline.mainSleep.endAt) - Date.parse(timeline.mainSleep.startAt)) /
									60000
							)}
						</p>{/if}
				{:else if selectedHeart && selectedHour !== null}
					<p class="font-bold">
						{clock(selectedHour * 60)} ~ {clock((selectedHour + 1) * 60)} 심박수
					</p>
					<p class="mt-1">
						평균 {selectedHeart.avg} bpm · 최저 {selectedHeart.min} bpm · 최고 {selectedHeart.max} bpm
					</p>
				{:else if selectedSteps != null && selectedStepHour !== null}
					<p class="font-bold">
						{clock(selectedStepHour * 60)} ~ {clock((selectedStepHour + 1) * 60)} 걸음 수
					</p>
					<p class="mt-1">{selectedSteps.toLocaleString('ko-KR')}보</p>
				{/if}
			</div>
		</HealthChartTooltip>
	{/if}
	<ul
		aria-label="타임라인 범례"
		class="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-xs text-text-secondary"
	>
		{#each presentStages as stage (stage)}<li class="flex items-center gap-2">
				<span
					aria-hidden="true"
					class={['size-2.5 rounded-sm', stage === 'awake' ? 'border border-border' : '']}
					style:background={stageStyle(stage).color}
				></span>{stageStyle(stage).label}
			</li>{/each}
		{#if exerciseIntervals.length}<li class="flex items-center gap-2">
				<span aria-hidden="true" class="size-2.5 rounded-sm bg-success-badge"></span>운동 구간
			</li>{/if}
		{#if heartValues.length}<li class="flex items-center gap-2">
				<span aria-hidden="true" class="w-4 border-t-2 border-brand"></span>심박수
			</li>{/if}
		{#if timeline.hourlySteps !== null}<li class="flex items-center gap-2">
				<span aria-hidden="true" class="size-2.5 rounded-sm bg-primary-light"></span>걸음 수
			</li>{/if}
	</ul>
	<div class="sr-only">
		<p>
			{minuteLabel(rangeStart)}부터 {minuteLabel(1440)}까지 공통 시간축, 단위: 심박수 bpm, 걸음 수
			보.
		</p>
		{#if timeline.mainSleep}<p>
				수면 {timestampLabel(timeline.mainSleep.startAt)} ~ {timestampLabel(
					timeline.mainSleep.endAt
				)}
			</p>{/if}
		{#each timeline.exerciseSegments as segment (segment.startAt)}<p>
				{segment.typeName}
				{timestampLabel(segment.startAt)} ~ {timestampLabel(segment.endAt)}
			</p>{/each}
		<ul aria-label="시간대별 기록 값">
			{#each Array.from({ length: 24 }, (_, hour) => hour) as hour (hour)}<li>
					{clock(hour * 60)} · 심박 {timeline.hourlyHeartRates?.[hour]?.avg ?? '기록 없음'} · 걸음 {timeline
						.hourlySteps?.[hour] ?? '기록 없음'}
				</li>{/each}
		</ul>
	</div>
</section>

<style>
	.exercise-interval {
		container-type: inline-size;
	}
	.sleep-summary {
		container-type: inline-size;
	}
	.sleep-name,
	.sleep-clock {
		display: none;
	}
	.sleep-short {
		writing-mode: vertical-rl;
		white-space: nowrap;
		font-size: 10px;
	}
	.exercise-name {
		display: none;
	}
	@container (min-width: 32px) {
		.sleep-name {
			display: inline;
		}
		.sleep-short {
			display: none;
		}
	}
	@container (min-width: 28px) {
		.exercise-name {
			display: inline;
		}
	}
	@container (min-width: 160px) {
		.sleep-clock {
			display: inline;
		}
	}
</style>
