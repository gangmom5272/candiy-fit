<script lang="ts">
	import { untrack } from 'svelte';
	import HealthChartTooltip from './HealthChartTooltip.svelte';
	let {
		root,
		dates,
		values,
		label,
		unit,
		formatValue,
		allowMissing = false,
		focusOverflowOnTab = false,
		messages,
		notes,
		navigationMessage,
		rows,
		message
	}: {
		root: HTMLElement | undefined;
		dates: readonly string[];
		values: readonly (number | null)[];
		label: string;
		unit: string;
		formatValue?: (value: number) => string;
		allowMissing?: boolean;
		/** 스크롤 툴팁 진입과 차트 내부의 안전한 키보드 포커스 이동. */
		focusOverflowOnTab?: boolean;
		messages?: readonly string[];
		notes?: readonly string[];
		navigationMessage?: string;
		rows?: readonly (readonly { label: string; value: number | null; unit: string }[])[];
		message?: string;
	} = $props();
	const id = $props.id();
	let anchor = $state<HTMLElement>();
	let selected = $state<number | null>(null);
	const value = $derived(selected === null ? null : (values[selected] ?? null));
	const isNavigation = $derived(anchor?.hasAttribute('data-chart-navigation') ?? false);
	let dismissed: HTMLElement | undefined;
	let pointerPosition: { x: number; y: number } | undefined;
	let dismissedPosition: { x: number; y: number } | undefined;
	let pinned = false;
	function close() {
		anchor = undefined;
		selected = null;
		pinned = false;
	}
	export function dismiss() {
		dismissed = anchor;
		dismissedPosition = pointerPosition;
		close();
	}
	function isDismissedPointer(event: PointerEvent) {
		return dismissedPosition?.x === event.clientX && dismissedPosition.y === event.clientY;
	}
	function target(event: Event) {
		const point =
			event.target instanceof Element
				? event.target.closest<HTMLElement>('[data-chart-point], [data-chart-navigation]')
				: null;
		return point && root?.contains(point) ? point : null;
	}
	function open(point: HTMLElement) {
		const index = Number(point.dataset.chartPoint ?? point.dataset.chartNavigation);
		if (
			point === dismissed ||
			(!allowMissing && !point.hasAttribute('data-chart-navigation') && values[index] == null)
		)
			return;
		anchor = point;
		selected = index;
	}
	function contains(
		event: PointerEvent,
		bounds: Pick<DOMRect, 'left' | 'right' | 'top' | 'bottom'>
	) {
		return (
			event.clientX >= bounds.left &&
			event.clientX <= bounds.right &&
			event.clientY >= bounds.top &&
			event.clientY <= bounds.bottom
		);
	}
	$effect(() => {
		// 기간이 바뀌면 이전 데이터의 선택을 정리한다.
		void values;
		close();
		dismissed = undefined;
		dismissedPosition = undefined;
	});
	$effect(() => {
		if (!anchor) return;
		const current = anchor;
		const description = current.getAttribute('aria-describedby');
		current.setAttribute('aria-describedby', [description, id].filter(Boolean).join(' '));
		return () => {
			if (description === null) current.removeAttribute('aria-describedby');
			else current.setAttribute('aria-describedby', description);
		};
	});
	$effect(() => {
		if (!root) return;
		const current = root;
		function over(event: PointerEvent) {
			// 닫힌 패널 아래 요소에 발생하는 pointerover는 실제 포인터 이동과 구분한다.
			if (event.pointerType !== 'mouse' || isDismissedPointer(event)) return;
			const active = document.activeElement;
			const tooltip = document.getElementById(id);
			// 키보드로 읽는 동안 패널 재배치가 정지된 포인터 아래 날짜를 선택하지 않게 한다.
			if (
				focusOverflowOnTab &&
				(tooltip?.contains(active) ||
					(active instanceof HTMLElement &&
						current.contains(active) &&
						active.hasAttribute('data-chart-point') &&
						pointerPosition?.x === event.clientX &&
						pointerPosition.y === event.clientY))
			)
				return;
			dismissedPosition = undefined;
			const panel = document.getElementById(id)?.getBoundingClientRect();
			if (panel && contains(event, panel)) return;
			const point = target(event);
			if (point && point !== anchor) {
				pinned = false;
				open(point);
			}
		}
		function focus(event: FocusEvent) {
			const point = target(event);
			if (point) {
				dismissed = undefined;
				dismissedPosition = undefined;
				pinned = false;
				open(point);
			}
		}
		function blur(event: FocusEvent) {
			if (
				event.relatedTarget instanceof Node &&
				document.getElementById(id)?.contains(event.relatedTarget)
			)
				return;
			// 포커스된 점이 렌더링 중 제거되어 발생한 focusout도 안전하게 정리한다.
			if (!anchor?.matches(':hover') && !document.getElementById(id)?.matches(':hover'))
				untrack(close);
		}
		function click(event: MouseEvent) {
			const point = target(event);
			if (!point || point.tagName !== 'BUTTON') return;
			if (anchor === point && pinned) dismiss();
			else {
				dismissed = undefined;
				dismissedPosition = undefined;
				open(point);
				pinned =
					event instanceof PointerEvent &&
					event.pointerType !== 'mouse' &&
					event.pointerType !== '';
			}
		}
		function move(event: PointerEvent) {
			if (event.pointerType !== 'mouse') return;
			pointerPosition = { x: event.clientX, y: event.clientY };
			if (focusOverflowOnTab && document.getElementById(id)?.contains(document.activeElement))
				return;
			if (isDismissedPointer(event)) return;
			dismissedPosition = undefined;
			if (dismissed && !contains(event, dismissed.getBoundingClientRect())) dismissed = undefined;
			if (!anchor || pinned) return;
			const point = anchor.getBoundingClientRect();
			const panel = document.getElementById(id)?.getBoundingClientRect();
			// 가장자리에서 툴팁이 옆으로 이동해도 두 영역 사이의 직선 이동을 허용한다.
			const upper = panel && panel.bottom <= point.top ? panel : point;
			const lower = upper === panel ? point : panel;
			const startY = upper === panel ? upper.bottom : point.top;
			const endY = lower ? (lower === panel ? lower.top : point.bottom) : startY;
			const progress = lower ? (event.clientY - startY) / (endY - startY) : -1;
			const inBridge =
				lower &&
				endY > startY &&
				progress >= 0 &&
				progress <= 1 &&
				event.clientX >= upper.left + (lower.left - upper.left) * progress &&
				event.clientX <= upper.right + (lower.right - upper.right) * progress;
			if (contains(event, point) || (panel && contains(event, panel)) || inBridge) return;
			const focused = current.querySelector<HTMLElement>(
				'[data-chart-point]:focus-visible, [data-chart-navigation]:focus-visible'
			);
			if (focused && focused !== dismissed) open(focused);
			else close();
		}
		function outside(event: PointerEvent) {
			if (event.target instanceof Node && !current.contains(event.target)) dismiss();
		}
		function focusVisibleControl(control: HTMLElement) {
			control.focus({ preventScroll: true });
			const header = document.querySelector('header');
			const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
			const top = Math.max(
				0,
				header && ['fixed', 'sticky'].includes(getComputedStyle(header).position)
					? header.getBoundingClientRect().bottom
					: 0
			);
			const bottom = Math.min(
				innerHeight,
				navigation && getComputedStyle(navigation).position === 'fixed'
					? navigation.getBoundingClientRect().top
					: innerHeight
			);
			if (bottom <= top) return;
			const bounds = control.getBoundingClientRect();
			// 범위 전체를 포함하는 큰 조작 영역은 실제 표시 포인트가 보이도록 맞춘다.
			const marker =
				control.dataset.chartPoint === undefined
					? null
					: current.querySelector<HTMLElement>(
							`[data-chart-focus-target="${control.dataset.chartPoint}"]`
						);
			const target =
				bounds.height > bottom - top ? (marker?.getBoundingClientRect() ?? bounds) : bounds;
			const padding = Math.max(0, Math.min(8, (bottom - top - target.height) / 2));
			const upper = top + padding;
			const lower = bottom - padding;
			const distance =
				target.height > lower - upper
					? (target.top + target.bottom - upper - lower) / 2
					: target.top < upper
						? target.top - upper
						: target.bottom > lower
							? target.bottom - lower
							: 0;
			if (distance) window.scrollBy({ top: distance, behavior: 'instant' });
		}
		function key(event: KeyboardEvent) {
			if (event.key === 'Escape') dismiss();
			if (!focusOverflowOnTab || event.key !== 'Tab') return;
			const panel = document.getElementById(id);
			const active = document.activeElement;
			if (
				panel?.dataset.scrollable === 'true' &&
				active === anchor &&
				!isNavigation &&
				!event.shiftKey
			) {
				event.preventDefault();
				panel.focus({ preventScroll: true });
				return;
			}
			const controls = [
				...current.querySelectorAll<HTMLElement>('[data-chart-point], [data-chart-navigation]')
			];
			const index = active instanceof HTMLElement ? controls.indexOf(active) : -1;
			const next =
				panel?.contains(active) && anchor
					? event.shiftKey
						? anchor
						: controls[controls.indexOf(anchor) + 1]
					: index < 0
						? undefined
						: controls[index + (event.shiftKey ? -1 : 1)];
			if (next) {
				event.preventDefault();
				focusVisibleControl(next);
			}
		}
		current.addEventListener('pointerover', over);
		current.addEventListener('focusin', focus);
		current.addEventListener('focusout', blur);
		current.addEventListener('click', click);
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerdown', outside);
		window.addEventListener('keydown', key);
		return () => {
			current.removeEventListener('pointerover', over);
			current.removeEventListener('focusin', focus);
			current.removeEventListener('focusout', blur);
			current.removeEventListener('click', click);
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerdown', outside);
			window.removeEventListener('keydown', key);
		};
	});
</script>

{#if root && anchor && selected !== null && (value !== null || allowMissing || isNavigation)}
	<HealthChartTooltip
		{id}
		ondismiss={dismiss}
		{anchor}
		container={root.closest('section') ?? root}
		date={dates[selected]}
		{label}
		{value}
		{unit}
		valueText={value === null ? undefined : formatValue?.(value)}
		missingValueText={allowMissing ? '—' : undefined}
		message={isNavigation ? navigationMessage : (messages?.[selected] ?? message)}
		note={notes?.[selected]}
		rows={rows?.[selected]}
	/>
{/if}
