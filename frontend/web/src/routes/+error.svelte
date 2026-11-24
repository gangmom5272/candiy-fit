<script lang="ts">
	import { onMount } from 'svelte';
	import { afterNavigate, beforeNavigate, goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { navigating, page } from '$app/state';
	import AppHeader from '#lib/components/layout/AppHeader.svelte';
	import Toast from '#lib/components/feedback/Toast.svelte';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import { LogoutController } from '#lib/features/auth/logout.svelte.js';
	import character from '#lib/assets/images/character-404.webp';
	import background from '#lib/assets/images/background-404.svg';
	import infoIcon from '#lib/assets/icons/icon-404-info.svg';

	const isNotFound = $derived(page.status === 404);
	let previousAppUrl: string | undefined;
	let pendingBack = $state<number | null>(null);
	let backRequestId = 0;
	const logout = new LogoutController();

	onMount(() => {
		// 공개 404를 유지하면서 직접 진입한 사용자의 세션만 확인한다.
		if (isNotFound && authSession.status === 'unknown') void authSession.restore();
	});

	async function handleLogout() {
		if (await logout.run()) await goto(resolve('/(public)/login'), { replaceState: true });
	}

	function finishBack(requestId: number) {
		if (pendingBack === requestId) pendingBack = null;
	}

	beforeNavigate(({ complete }) => {
		const requestId = pendingBack;
		if (requestId !== null) {
			void complete.then(
				() => finishBack(requestId),
				() => finishBack(requestId)
			);
		}
	});

	afterNavigate(({ from }) => {
		previousAppUrl = from?.route.id ? from.url.href : undefined;
		pendingBack = null;
	});

	function handleBack() {
		if (pendingBack !== null || navigating.type !== null) return;
		const requestId = ++backRequestId;
		pendingBack = requestId;

		try {
			const navigation = window.navigation;
			const previousEntry = navigation?.entries()[(navigation.currentEntry?.index ?? 0) - 1];
			const previousUrl = previousAppUrl ?? document.referrer;

			if (
				previousUrl &&
				previousEntry?.url === previousUrl &&
				new URL(previousUrl).origin === page.url.origin &&
				new URL(previousUrl).pathname !== page.url.pathname
			) {
				window.history.back();
			} else {
				void goto(resolve('/(app)/home')).then(
					() => finishBack(requestId),
					() => finishBack(requestId)
				);
			}
		} catch {
			finishBack(requestId);
		}
	}
</script>

<svelte:window
	onpageshow={(event) => {
		if (event.persisted) pendingBack = null;
	}}
/>

<svelte:head>
	<title>{isNotFound ? '페이지를 찾을 수 없어요' : `오류 ${page.status}`} | 캔디핏</title>
	<meta name="robots" content="noindex" />
</svelte:head>

<div class="min-h-dvh bg-background text-text-primary" class:error-layout-not-found={isNotFound}>
	<AppHeader
		onLogout={isNotFound && authSession.status === 'authenticated' ? handleLogout : undefined}
	/>
	<main
		class="flex min-h-[calc(100dvh-var(--app-header-height))] items-center justify-center px-5 py-8 sm:px-8 lg:pt-0 lg:pb-20"
		class:error-main-not-found={isNotFound}
	>
		<div class="error-stage">
			<section
				aria-labelledby="error-title"
				class="error-card gap-8 rounded-[2.5rem] border border-transparent bg-card px-6 py-8 sm:px-10 sm:py-12 lg:min-h-[43.75rem] lg:gap-24 lg:px-14 lg:py-16"
				class:error-card-not-found={isNotFound}
			>
				<div class="error-content">
					{#if isNotFound}
						<p
							class="error-badge inline-flex min-h-[2.625rem] w-[170px] items-center justify-center rounded-full bg-primary-soft text-sm font-bold whitespace-nowrap text-primary sm:text-lg"
						>
							PAGE NOT FOUND
						</p>
					{/if}
					<p class="error-status mt-3 leading-none font-bold lg:-mt-3">
						{page.status}
					</p>
					<h1
						id="error-title"
						class="error-title mt-2 text-3xl leading-tight font-bold break-keep sm:text-4xl xl:text-[54px]"
					>
						{isNotFound ? '페이지를 찾을 수 없어요' : '오류가 발생했어요'}
					</h1>
					<div
						class="error-description mt-4 text-base leading-normal break-keep text-text-secondary sm:text-xl xl:text-2xl"
					>
						{#if isNotFound}
							<p>요청하신 페이지가 이동되었거나 삭제되었을 수 있어요.</p>
							<p>홈으로 돌아가 다시 탐색해 보세요.</p>
						{:else}
							<p>{page.error?.message ?? '잠시 후 다시 시도해 주세요.'}</p>
						{/if}
					</div>
					<div class="error-actions">
						<a
							href={resolve('/(app)/home')}
							class="error-action error-action-primary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
						>
							홈으로 가기
						</a>
						<button
							type="button"
							onclick={handleBack}
							aria-disabled={pendingBack !== null || navigating.type !== null}
							aria-busy={pendingBack !== null || navigating.type !== null}
							class="error-action error-action-secondary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring"
						>
							이전 페이지
						</button>
					</div>
					{#if isNotFound}
						<aside class="error-note">
							<img src={infoIcon} alt="" width="40" height="40" />
							<div>
								<h2>찾으시는 페이지가 보이지 않나요?</h2>
								<p>
									주소를 다시 확인하거나, 홈에서 원하는 메뉴로 이동할 수 있어요.<br />
									문제가 계속되면 잠시 후 다시 시도해 주세요.
								</p>
							</div>
						</aside>
					{/if}
				</div>
				{#if isNotFound}
					<div class="error-visual" aria-hidden="true">
						<img
							src={background}
							alt=""
							width="530"
							height="436"
							class="absolute inset-x-0 top-0 w-full"
						/>
						<img src={character} alt="" width="420" height="420" class="error-character" />
					</div>
				{/if}
			</section>
		</div>
	</main>
	{#if logout.message}
		<Toast kind="error" message={logout.message} />
	{/if}
</div>

<style>
	.error-stage {
		width: 100%;
		max-width: 1300px;
	}

	.error-card {
		display: grid;
		width: 100%;
		max-width: 1300px;
		min-width: 0;
	}

	.error-status {
		font-size: 6rem;
	}

	.error-content {
		min-width: 0;
		max-width: 560px;
		align-self: center;
	}

	.error-actions {
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin-top: 32px;
	}

	.error-action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 100%;
		max-width: 100%;
		min-height: 60px;
		padding: 12px 24px;
		border-radius: 16px;
		font-size: 18px;
		font-weight: 700;
		line-height: normal;
		cursor: pointer;
	}

	.error-action-primary {
		background: var(--color-primary);
		color: var(--color-primary-foreground);
	}

	.error-action-primary:hover {
		background: var(--color-primary-hover);
	}

	.error-action-secondary {
		border: 1px solid var(--color-border);
		background: var(--color-card);
		color: var(--color-text-primary);
	}

	.error-card-not-found .error-action {
		/* 포커스 테두리까지 고정 헤더 아래에 드러내고, 이미 보이는 버튼은 움직이지 않는다. */
		scroll-margin-block: calc(var(--app-header-height) + 1rem)
			calc(var(--app-bottom-nav-height, 0px) + 1rem);
	}

	.error-note {
		display: flex;
		gap: 8px;
		width: 100%;
		max-width: 560px;
		margin-top: 40px;
		padding: 16px 20px;
		border: 1px solid var(--color-coral-soft);
		border-radius: 24px;
		background: color-mix(in srgb, var(--color-primary-soft) 60%, var(--color-card));
		word-break: keep-all;
	}

	.error-note > img {
		flex-shrink: 0;
		width: 40px;
		height: 40px;
		margin-top: 4px;
	}

	.error-note > div {
		min-width: 0;
	}

	.error-note h2 {
		font-size: 18px;
		font-weight: 700;
		line-height: 1.25;
	}

	.error-note p {
		margin-top: 4px;
		color: var(--color-text-secondary);
		font-size: 14px;
		line-height: 1.3;
	}

	.error-visual {
		position: relative;
		width: min(100%, 530px);
		aspect-ratio: 530 / 446;
		margin-inline: auto;
		align-self: start;
	}

	.error-character {
		position: absolute;
		top: 5.8%;
		left: 10.2%;
		width: 79.25%;
		max-width: 420px;
		height: auto;
		object-fit: contain;
	}

	@media (width < 48rem) {
		.error-layout-not-found {
			--app-header-height: 4rem;
			--app-bottom-nav-height: calc(5rem + env(safe-area-inset-bottom, 0px));
			padding-bottom: var(--app-bottom-nav-height);
		}
	}

	@media (min-width: 40rem) {
		.error-status {
			font-size: 130px;
		}

		.error-actions {
			flex-direction: row;
			gap: 20px;
		}

		.error-action {
			width: 220px;
			font-size: 22px;
		}

		.error-note {
			padding-inline: 24px;
		}

		.error-note h2 {
			font-size: 22px;
		}

		.error-note p {
			font-size: 18px;
		}

		/* 데스크톱의 글자 크기를 유지해 페이지 확대가 반응형 축소로 상쇄되지 않게 한다. */
		.error-card-not-found .error-title {
			font-size: 54px;
		}

		.error-card-not-found .error-description {
			font-size: 24px;
		}
	}

	@media (min-width: 64rem) {
		.error-card {
			grid-template-columns: minmax(0, 560fr) minmax(0, 530fr);
		}

		.error-visual {
			margin-top: 0.5rem;
		}
	}

	@media (min-width: 48rem) {
		.error-main-not-found {
			padding-block: 24px calc(var(--app-header-height) + 24px);
		}

		.error-card-not-found .error-content {
			width: 100%;
			margin-inline: auto;
		}
	}

	@media (64rem <= width < 80rem) {
		/* 두 열의 본문 폭이 부족한 태블릿에서는 축소 대신 문서 흐름을 유지한다. */
		.error-card-not-found {
			grid-template-columns: minmax(0, 1fr);
			min-height: 0;
			gap: 2rem;
		}
	}

	@media (min-width: 80rem) {
		.error-card-not-found {
			padding-bottom: 56px;
		}

		.error-card-not-found .error-status {
			margin-top: -4px;
		}

		.error-card-not-found .error-visual {
			width: 100%;
			margin-top: 0;
			align-self: center;
		}
	}
</style>
