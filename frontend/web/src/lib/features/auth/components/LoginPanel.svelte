<script lang="ts" module>
	import type { OAuthProvider } from '../oauth';

	export type LoginProvider = { id: OAuthProvider; label: string; isAvailable: boolean };
</script>

<script lang="ts">
	type Props = {
		providers: LoginProvider[];
		/** 제공자 화면으로 넘어가는 중인 제공자. 그동안 두 버튼을 모두 잠근다 */
		startingProvider?: OAuthProvider | null;
		/** HTTPS가 아니거나 저장소가 막혀 로그인을 시작하지 못했을 때 */
		hasStartError?: boolean;
		onstart: (provider: OAuthProvider) => void;
	};

	let { providers, startingProvider = null, hasStartError = false, onstart }: Props = $props();

	const providerNames: Record<OAuthProvider, string> = { kakao: '카카오', google: 'Google' };
	const unavailable = $derived(providers.filter((provider) => !provider.isAvailable));
</script>

<div class="flex flex-col">
	<p class="text-sm font-semibold tracking-wide text-coral-main sm:text-base">CANDiY-FiT</p>
	<h1 class="mt-2 text-[28px] leading-[normal] font-bold sm:text-[32px]">캔디핏 로그인</h1>
	<p class="mt-3 text-[15px] leading-[1.6] break-keep text-text-secondary sm:text-base">
		소셜 계정으로 로그인하고 건강 기록을 확인해 보세요.
	</p>

	<!-- 제공자 버튼 색은 각 브랜드 가이드를 따르고 서비스 token과 섞지 않는다 -->
	<ul class="mt-7 flex flex-col gap-3 sm:mt-10">
		{#each providers as provider (provider.id)}
			{@const isStarting = startingProvider === provider.id}
			<li>
				<button
					type="button"
					class={[
						'flex min-h-14 w-full items-center justify-center gap-2.5 rounded-2xl px-5 text-base font-semibold transition-[opacity,box-shadow] hover:shadow-[0_2px_2px_rgb(44_46_53/0.08)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text-primary disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:shadow-none',
						provider.id === 'kakao'
							? 'bg-[#FEE500] text-black/85'
							: 'border border-[#747775] bg-white text-[#1F1F1F]'
					]}
					disabled={!provider.isAvailable || startingProvider !== null}
					aria-busy={isStarting}
					aria-describedby={provider.isAvailable ? undefined : 'login-config-note'}
					onclick={() => provider.isAvailable && onstart(provider.id)}
				>
					{#if isStarting}
						<svg class="size-5 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
							<circle
								cx="12"
								cy="12"
								r="9"
								stroke="currentColor"
								stroke-opacity="0.2"
								stroke-width="2.5"
							/>
							<path
								d="M21 12a9 9 0 0 0-9-9"
								stroke="currentColor"
								stroke-width="2.5"
								stroke-linecap="round"
							/>
						</svg>
					{:else if provider.id === 'kakao'}
						<svg class="size-5" viewBox="0 0 24 24" aria-hidden="true">
							<path
								fill="currentColor"
								d="M12 3C6.48 3 2 6.48 2 10.77c0 2.77 1.86 5.2 4.66 6.57l-.95 3.48c-.08.3.26.55.53.38l4.15-2.74c.53.06 1.06.09 1.61.09 5.52 0 10-3.48 10-7.78S17.52 3 12 3Z"
							/>
						</svg>
					{:else}
						<svg class="size-5" viewBox="0 0 48 48" aria-hidden="true">
							<path
								fill="#EA4335"
								d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5Z"
							/>
							<path
								fill="#4285F4"
								d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65Z"
							/>
							<path
								fill="#FBBC05"
								d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19Z"
							/>
							<path
								fill="#34A853"
								d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48Z"
							/>
						</svg>
					{/if}
					{provider.label}
				</button>
			</li>
		{/each}
	</ul>

	{#if hasStartError}
		<p
			role="alert"
			class="mt-4 rounded-2xl bg-danger-soft px-4 py-3 text-sm leading-[1.5] break-keep text-text-primary"
		>
			이 브라우저에서는 로그인을 시작할 수 없어요. HTTPS 주소로 접속했는지 확인해 주세요.
		</p>
	{/if}
	{#if unavailable.length === providers.length}
		<div
			id="login-config-note"
			class="mt-4 rounded-2xl bg-surface px-4 py-3 text-sm leading-[1.5] break-keep text-text-primary"
		>
			<p class="font-bold">지금은 로그인을 사용할 수 없어요.</p>
			<p class="mt-1">로그인 기능을 준비하고 있어요. 잠시 후 다시 시도해 주세요.</p>
		</div>
	{:else if unavailable.length > 0}
		<p
			id="login-config-note"
			class="mt-4 rounded-2xl bg-surface px-4 py-3 text-sm leading-[1.5] break-keep text-text-primary"
		>
			{unavailable.map((provider) => providerNames[provider.id]).join(', ')} 로그인은 준비 중이에요. 다른
			방법으로 로그인해 주세요.
		</p>
	{/if}

	<div class="mt-7 border-t border-border pt-5 sm:mt-10 sm:pt-6">
		<p class="text-sm leading-[1.6] break-keep text-text-secondary">
			처음이라면 로그인 후 닉네임·키·몸무게 같은 기본 정보만 입력하면 가입이 끝나요.
		</p>
		<p class="mt-2 text-sm leading-[1.6] break-keep text-text-secondary">
			건강 기록은 워치와 연결한 <strong class="font-semibold text-text-primary">LiNK 앱</strong>이
			모아 보내요.
		</p>
	</div>
</div>
