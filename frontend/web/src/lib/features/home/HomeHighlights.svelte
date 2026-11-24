<script lang="ts">
	import { healthSyncContext } from '#lib/features/health/sync-context.js';
	const healthSync = healthSyncContext();
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import characterHeart from '#lib/assets/images/character-heart.webp';
	import characterCoach from '#lib/assets/images/character-coach.webp';
	import { activityToday } from '#lib/features/health/activity-api.js';
	import {
		dailyMessageError,
		getDailyMessage,
		messageMetricLabels,
		type DailyMessage
	} from './daily-message-api';
	let { onDateChange }: { onDateChange?: (date: string) => void } = $props();

	let message = $state<DailyMessage | null>(null);
	let loading = $state(true);
	let failure = $state<ReturnType<typeof dailyMessageError> | null>(null);
	let reload = $state(() => {});

	$effect(() => {
		if (healthSync.revision > 0) reload();
	});
	onMount(() => {
		let controller: AbortController | undefined;
		let requestId = 0;
		let requestDate = '';
		let disposed = false;
		let timer: ReturnType<typeof setTimeout>;

		async function load(dateRetry = false) {
			const id = ++requestId;
			controller?.abort();
			controller = new AbortController();
			const signal = controller.signal;
			const date = activityToday();
			if (requestDate !== date) {
				message = null;
				// 자정·복귀 감지는 한 곳에서 유지하고 홈의 점수·지표도 같은 KST 날짜로 갱신한다.
				onDateChange?.(date);
			}
			requestDate = date;
			loading = true;
			failure = null;
			try {
				const result = await healthSync.read(signal, (requestSignal, client) =>
					getDailyMessage(requestSignal, client)
				);
				if (disposed || id !== requestId || signal.aborted) return;
				if (date !== activityToday()) {
					void load();
					return;
				}
				if (result.date !== date) {
					message = null;
					if (!dateRetry) {
						void load(true);
						return;
					}
					throw new Error('daily-message date mismatch');
				}
				message = result;
			} catch (error) {
				if (disposed || id !== requestId || signal.aborted) return;
				if (date !== activityToday()) {
					void load();
					return;
				}
				message = null;
				failure = dailyMessageError(error);
			} finally {
				if (!disposed && id === requestId) loading = false;
			}
		}
		function checkDate() {
			if (requestDate !== activityToday()) void load();
		}
		function scheduleMidnight() {
			clearTimeout(timer);
			const midnight = Date.parse(`${activityToday()}T00:00:00+09:00`) + 86400000;
			timer = setTimeout(
				() => {
					checkDate();
					scheduleMidnight();
				},
				Math.max(1, midnight - Date.now())
			);
		}
		function onVisible() {
			if (document.visibilityState === 'visible') {
				checkDate();
				scheduleMidnight();
			}
		}
		reload = () => {
			void load();
		};
		void load();
		scheduleMidnight();
		document.addEventListener('visibilitychange', onVisible);
		window.addEventListener('focus', onVisible);
		return () => {
			disposed = true;
			requestId++;
			controller?.abort();
			clearTimeout(timer);
			document.removeEventListener('visibilitychange', onVisible);
			window.removeEventListener('focus', onVisible);
		};
	});
</script>

<div class="home-highlights grid items-stretch gap-4 lg:grid-cols-2 lg:gap-8">
	<section
		aria-label="오늘의 한마디"
		aria-busy={loading}
		class="health-highlight relative isolate grid rounded-lg bg-coral-soft p-4 lg:p-6"
	>
		<div
			class="message-content relative z-10 col-start-1 row-span-2 row-start-1 min-w-0 self-center rounded-2xl bg-warning-soft p-2 break-keep md:p-3"
		>
			<p
				class="message-label text-xs leading-none font-medium lg:text-sm xl:text-[15px] xl:leading-[normal]"
			>
				오늘의 한마디
			</p>
			{#if loading && !message}<div role="status" class="mt-2 space-y-3">
					<span class="sr-only">오늘의 한마디를 불러오는 중이에요.</span>
					<div aria-hidden="true" class="health-skeleton h-5 w-full"></div>
					<div aria-hidden="true" class="health-skeleton h-3 w-full"></div>
					<div aria-hidden="true" class="health-skeleton h-3 w-3/4"></div>
				</div>{/if}
			{#if message}
				<h2
					class="mt-2 text-base leading-tight font-bold lg:text-xl xl:text-[22px] xl:leading-[normal]"
				>
					{message.title}
				</h2>
				<p
					class="message-description mt-2 text-xs leading-snug text-text-secondary lg:text-sm xl:leading-[normal]"
				>
					{message.description}
				</p>
				{#if message.notices.length > 0}<ul
						class="mt-4 space-y-2 text-xs leading-relaxed text-text-secondary"
						aria-label="지표별 안내"
					>
						{#each message.notices as notice (`${notice.metric}-${notice.code}`)}
							<li>{messageMetricLabels[notice.metric]}: {notice.message}</li>
						{/each}
					</ul>{/if}
			{:else if failure}
				<div role={failure.preparing ? 'status' : 'alert'} class="mt-2">
					<h2 class="text-base leading-tight font-bold lg:text-xl">{failure.title}</h2>
					<p class="mt-2 text-xs leading-relaxed text-text-secondary lg:text-sm">
						{failure.description}
					</p>
				</div>
			{/if}
			{#if message?.status === 'PARTIAL' || failure?.retryable}
				<button
					type="button"
					onclick={() => (failure?.preparing ? void healthSync.recheck() : reload())}
					disabled={loading || (failure?.preparing && healthSync.rechecking)}
					class="mt-4 cursor-pointer rounded-lg border border-brand bg-card px-3 py-2 text-sm font-semibold text-accent-foreground focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring disabled:cursor-wait disabled:opacity-60"
					>{loading ? '다시 확인하는 중' : '다시 확인하기'}</button
				>
			{/if}
		</div>
		<img
			src={characterHeart}
			alt=""
			width="315"
			height="235"
			class="message-image col-start-2 row-start-2 h-auto max-h-32 w-full max-w-[315px] self-end justify-self-end object-contain"
		/>
	</section>

	<section
		class="coaching-highlight relative isolate grid min-h-[220px] items-center rounded-lg bg-coral-main p-4 lg:p-6"
	>
		<div class="coaching-content relative z-10 max-w-[70%] min-w-0">
			<h2 class="text-base leading-[1.3] font-bold text-primary-foreground lg:text-xl xl:text-2xl">
				AI 코치와 함께<br />나에게 맞는 건강 루틴을
				<span class="coaching-ending">만들어보세요 !</span>
			</h2>
			<a
				href={resolve('/(app)/coaching')}
				class="health-link-motion mt-8 inline-block cursor-pointer rounded-lg bg-primary-foreground px-3 py-2 text-sm leading-5 font-semibold text-coral-main focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring lg:px-6 xl:px-[26px] xl:py-2.5 xl:font-bold"
			>
				AI 코치에게 물어보기
			</a>
		</div>
		<img
			src={characterCoach}
			alt=""
			width="234"
			height="234"
			class="absolute right-0 bottom-0 h-auto w-[37.5%] max-w-[234px] object-contain xl:-right-2.5"
		/>
	</section>
</div>

<style>
	.health-highlight {
		grid-template-columns: minmax(0, 7fr) minmax(0, 3fr);
		/* 기존 제목 행 높이는 보존하고 말풍선은 두 행을 함께 사용한다. */
		grid-template-rows: 0.75rem 1fr;
		gap: 0.75rem;
		overflow-wrap: anywhere;
	}
	.message-description {
		min-height: 2lh;
	}
	.message-image {
		object-position: right bottom;
		transform: scale(1.1);
		transform-origin: 25% bottom;
	}
	.coaching-ending {
		white-space: nowrap;
	}
	@media (width < 64rem) {
		.coaching-highlight {
			display: flex;
			flex-wrap: wrap;
			min-height: 10rem;
			gap: 1rem;
		}
		.coaching-content {
			display: grid;
			flex: 1 1 10rem;
			min-width: min-content;
			max-width: 100%;
			gap: 1rem;
			justify-items: start;
		}
		.coaching-highlight h2 {
			word-break: keep-all;
			overflow-wrap: anywhere;
			text-wrap: balance;
		}
		.coaching-highlight a {
			margin-top: 0;
			word-break: keep-all;
		}
		.coaching-highlight img {
			position: static;
			flex: 0 0 auto;
			width: clamp(6rem, 26vw, 7rem);
			margin-left: auto;
		}
	}
	@media (min-width: 48rem) {
		.health-highlight {
			grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
		}
		.message-content::after {
			position: absolute;
			right: -0.375rem;
			bottom: 1.25rem;
			width: 0.75rem;
			height: 0.75rem;
			transform: rotate(45deg);
			border-radius: 2px;
			background: inherit;
			content: '';
			pointer-events: none;
		}
	}
	@media (40rem <= width < 64rem) {
		.health-highlight {
			grid-template-columns: minmax(0, 1fr) clamp(8rem, 20vw, 11rem);
			grid-template-rows: auto;
			min-height: 9.25rem;
			/* 아래쪽 기준으로 확대된 캐릭터의 상단까지 카드 안에 남긴다. */
			padding-block: 0.5rem 0.375rem;
			gap: 1rem;
		}
		.message-content {
			grid-row: 1;
			padding: 0.75rem;
		}
		.message-image {
			grid-row: 1;
		}
		.coaching-highlight {
			min-height: 9.75rem;
			padding-block: 0.375rem;
		}
		.coaching-highlight img {
			width: clamp(8rem, 18vw, 9rem);
		}
	}
	@media (64rem <= width < 80rem) {
		.coaching-content {
			max-width: 60%;
		}
		.coaching-highlight h2 {
			word-break: keep-all;
		}
	}
	@media (min-width: 64rem) {
		.health-highlight {
			grid-template-rows: 0.875rem 1fr;
		}
		.home-highlights section {
			min-height: calc(13.75rem - var(--home-compression, 0px) * 40 / 312);
			padding: calc(1.5rem - var(--home-compression, 0px) * 8 / 312);
			align-items: center;
		}
		.coaching-highlight a {
			margin-top: calc(2.5rem - var(--home-compression, 0px) * 20 / 312);
		}
		.coaching-highlight img {
			height: calc(14.625rem - var(--home-compression, 0px) * 54 / 312);
			width: auto;
			max-width: 37.5%;
		}
	}
	@media (min-width: 80rem) {
		.message-image {
			transform: scale(1.22);
			transform-origin: right bottom;
		}
		.health-highlight {
			grid-template-rows: 1.125rem 1fr;
		}
		.home-highlights section {
			align-items: start;
		}
		.health-highlight > img {
			max-width: 315px;
		}
		.home-highlights .health-highlight {
			padding-inline: calc(2rem - var(--home-compression, 0px) * 16 / 312);
			padding-top: calc(1.75rem - var(--home-compression, 0px) * 12 / 312);
		}
		.home-highlights .coaching-highlight {
			padding-inline: calc(1.75rem - var(--home-compression, 0px) * 12 / 312);
			padding-top: calc(3rem - var(--home-compression, 0px) * 24 / 312);
		}
	}
</style>
