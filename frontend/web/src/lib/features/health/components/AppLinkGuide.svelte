<script lang="ts">
	import type { Snippet } from 'svelte';
	import illustration from '#lib/assets/images/app-link-guide.webp';
	import loginIcon from '#lib/assets/icons/icon-app-login.svg';
	import syncIcon from '#lib/assets/icons/icon-app-health-sync.svg';
	import returnIcon from '#lib/assets/icons/icon-web-return.svg';

	let { actions }: { actions?: Snippet } = $props();

	// 표시 여부는 확정된 연동 상태를 가진 호출부가 결정한다.
	// CANDIY_USER_NOT_LINKED는 첫 데이터 준비 전도 포함하므로 단독 판별에 쓰지 않는다.
	const steps = [
		{
			label: 'STEP 01',
			title: '앱 설치 및 로그인',
			description: '웹에서 사용한 계정으로 앱에 로그인해요.',
			icon: loginIcon
		},
		{
			label: 'STEP 02',
			title: '건강 데이터 연동',
			description: '앱에서 워치와 건강 데이터를 연결해요.',
			icon: syncIcon
		},
		{
			label: 'STEP 03',
			title: '웹으로 돌아오기',
			description: '연동을 마쳤다면 웹으로 돌아와 새로고침하면 돼요.',
			icon: returnIcon
		}
	];
</script>

<main class="guide-main mx-auto w-full max-w-health-page px-5 py-5 sm:px-8 sm:py-8">
	<div class="flex justify-center">
		<section
			aria-labelledby="app-link-title"
			class="guide-panel flex w-full flex-col items-center rounded-[28px] border border-border bg-card px-4 py-5 sm:px-8 sm:py-6"
		>
			<p
				class="inline-flex max-w-full items-center justify-center gap-2 rounded-full border border-coral-sub bg-accent px-3 py-1.5 text-center text-sm font-bold text-accent-foreground lg:min-w-[272px] lg:gap-4 lg:px-4 lg:py-2"
			>
				<span class="size-4 shrink-0 rounded-full bg-brand" aria-hidden="true"></span>
				앱 연동이 필요해요
			</p>
			<h1
				id="app-link-title"
				class="mt-3 text-center text-page-title break-keep text-card-foreground lg:mt-6"
			>
				건강 기록을 보려면 먼저 앱 연동이 필요해요
			</h1>
			<p
				class="mt-2 max-w-4xl text-center text-base leading-relaxed break-keep text-muted-foreground lg:mt-4"
			>
				모바일 앱에서 워치와 건강 데이터를 연동하면 웹에서도 건강 기록을 확인할 수 있어요.
			</p>
			<img
				src={illustration}
				alt=""
				width="2400"
				height="1020"
				class="guide-illustration my-5 h-auto w-full max-w-[320px] object-contain md:max-w-[440px] lg:my-4 lg:max-w-[600px]"
			/>
			<ol
				class="grid w-full max-w-[1030px] gap-3 lg:grid-cols-3 lg:gap-6"
				aria-label="앱 연동 3단계"
			>
				{#each steps as step (step.label)}
					<li
						class="grid min-w-0 grid-cols-[37px_minmax(0,1fr)] items-start gap-x-3 rounded-2xl border border-border p-3.5 lg:content-start lg:gap-x-2.5 lg:rounded-3xl lg:p-4"
					>
						<img
							src={step.icon}
							alt=""
							width="37"
							height="37"
							class="row-span-3 shrink-0 lg:row-span-1"
						/>
						<p class="self-center text-sm font-bold text-primary">{step.label}</p>
						<h2
							class="col-start-2 mt-1 text-card-title text-card-foreground lg:col-span-2 lg:col-start-1 lg:mt-2 lg:px-2 lg:text-card-title-lg"
						>
							{step.title}
						</h2>
						<p
							class="col-start-2 mt-1 text-sm leading-relaxed break-keep text-muted-foreground lg:col-span-2 lg:col-start-1 lg:mt-2 lg:px-2"
						>
							{step.description}
						</p>
					</li>
				{/each}
			</ol>
			<div
				class="mt-4 flex w-full min-w-0 flex-col items-center gap-3 lg:mt-6 lg:flex-row lg:justify-center"
			>
				<p class="text-center text-sm leading-relaxed break-keep text-muted-foreground">
					첫 건강 기록이 표시되기까지는 시간이 조금 걸릴 수 있어요.
				</p>
				{@render actions?.()}
			</div>
		</section>
	</div>
</main>

<style>
	@media (min-width: 64rem) {
		.guide-main {
			display: flex;
			min-height: calc(100dvh - var(--app-header-height));
			flex-direction: column;
			justify-content: center;
			padding-block: 1.5rem;
		}

		/* 글자는 토큰 크기를 유지하고, 화면 높이에 맞춰 일러스트만 줄인다. */
		.guide-illustration {
			width: clamp(20rem, calc(100dvh - 20rem), 37.5rem);
		}
	}

	@media (min-width: 64rem) and (min-height: 64rem) {
		.guide-main {
			justify-content: flex-start;
			padding-block: 3.5rem;
		}

		.guide-panel {
			min-height: 792px;
			padding-block: 2.25rem;
		}

		.guide-illustration {
			margin-block: 4rem 2rem;
		}
	}
</style>
