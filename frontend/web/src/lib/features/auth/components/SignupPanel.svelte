<script lang="ts">
	import logo from '#lib/assets/images/logo.svg';
	import ProfileForm from '#lib/features/profile/components/ProfileForm.svelte';
	import ProfileGuide from '#lib/features/profile/components/ProfileGuide.svelte';
	import type { ProfileErrors } from '#lib/features/profile/validation.js';
	import type { SignupProfile } from '../types';

	type Props = {
		isSubmitting?: boolean;
		/** 입력칸에 붙는 서버 검증 오류(422) */
		serverErrors?: ProfileErrors;
		/** 입력칸에 붙일 수 없는 오류 */
		formError?: { message: string; requestId: string | null } | null;
		/** 로그인 화면으로 돌아가기. 다른 소셜 계정으로 다시 시작할 때 쓴다 */
		onBack?: () => void;
		onSubmit: (profile: SignupProfile) => void;
	};

	let {
		isSubmitting = false,
		serverErrors = {},
		formError = null,
		onBack,
		onSubmit
	}: Props = $props();
</script>

<!--
	회원가입 화면(Figma WEB_기본정보입력). 마이페이지(WEB_마이페이지)와 같은 카드 구성이고,
	가입 전이라 머리말에는 메뉴 없이 로고만 둔다. 로고 자리는 공통 헤더(AppHeader)와 같다
-->
<div class="flex min-h-dvh flex-col bg-background text-text-primary">
	<header class="shrink-0 rounded-b-[2.5rem] bg-card">
		<div class="mx-auto flex h-16 max-w-[1792px] items-center px-5 sm:px-8 2xl:h-20">
			<img src={logo} alt="캔디핏" width="68" height="28" />
		</div>
	</header>

	<main
		class="fit-height mx-auto w-full max-w-[1344px] flex-1 px-5 pt-8 pb-12 sm:px-8 lg:flex lg:flex-col lg:pt-(--main-top) lg:pb-(--main-bottom)"
	>
		<div
			class="grid grid-cols-1 gap-8 rounded-[40px] border border-border bg-card p-5 sm:p-8 lg:flex-1 lg:grid-cols-[minmax(0,454px)_minmax(0,1fr)] lg:p-(--card-padding)"
		>
			<ProfileGuide
				titleLines={[
					'기본 정보를 입력하면',
					'나에게 맞는 건강 기록을',
					'더 정확하게 보여줄 수 있어요.'
				]}
				descriptionLines={[
					'처음 한 번만 입력하면 되고,',
					'나중에 마이페이지에서 수정할 수 있어요.'
				]}
			/>

			<div class="min-w-0 lg:pt-(--column-top) lg:pr-2">
				<h1 class="text-[28px] leading-[normal] font-bold lg:text-(length:--page-title)">
					기본 정보 입력
				</h1>
				<p class="mt-3 text-[15px] leading-[normal] text-text-secondary">
					건강 기록을 더 잘 이해할 수 있도록 기본 정보를 입력해 주세요.
				</p>
				{#if formError}
					<div
						role="alert"
						class="mt-6 rounded-2xl bg-danger-soft px-5 py-3 text-sm leading-[normal] text-text-primary"
					>
						<p>{formError.message}</p>
						{#if formError.requestId}
							<p class="mt-1">요청 ID: {formError.requestId}</p>
						{/if}
					</div>
				{/if}
				<div class="mt-10 lg:mt-(--profile-gap)">
					<!-- 다른 소셜 계정으로 다시 시작할 수 있게 로그인 화면으로 돌아가는 길을 둔다(S15P31A403-347) -->
					<ProfileForm submitLabel="가입하기" {onBack} {isSubmitting} {serverErrors} {onSubmit} />
				</div>
			</div>
		</div>
	</main>
</div>

<style>
	/*
	 * 마이페이지와 같은 식: 노트북·모니터(lg 이상)에서 세로 스크롤 없이 보이도록 간격·크기를 화면 높이에 맞춘다.
	 * 높이 640px에서 최소값, 1080px(Figma)에서 시안 값
	 */
	@media (width >= 64rem) {
		.fit-height {
			--main-top: clamp(1rem, calc(1rem + (100dvh - 640px) * 0.1), 3.75rem);
			--main-bottom: clamp(1rem, calc(1rem + (100dvh - 640px) * 0.0364), 2rem);
			--card-padding: clamp(1.25rem, calc(1.25rem + (100dvh - 640px) * 0.0273), 2rem);
			--guide-top: clamp(1.5rem, calc(1.5rem + (100dvh - 640px) * 0.0818), 3.75rem);
			--guide-title: clamp(1.375rem, calc(1.375rem + (100dvh - 640px) * 0.0182), 1.875rem);
			--guide-body-gap: clamp(0.75rem, calc(0.75rem + (100dvh - 640px) * 0.0636), 2.5rem);
			--guide-list-gap: clamp(1rem, calc(1rem + (100dvh - 640px) * 0.0727), 3rem);
			--guide-item-gap: clamp(0.75rem, calc(0.75rem + (100dvh - 640px) * 0.0455), 2rem);
			--character-width: clamp(11rem, calc(11rem + (100dvh - 640px) * 0.2818), 18.75rem);
			--column-top: clamp(0.25rem, calc(0.25rem + (100dvh - 640px) * 0.0455), 1.5rem);
			--page-title: clamp(1.375rem, calc(1.375rem + (100dvh - 640px) * 0.0136), 1.75rem);
			--profile-field-height: clamp(2.75rem, calc(2.75rem + (100dvh - 640px) * 0.0727), 4.75rem);
			--profile-gap: clamp(1rem, calc(1rem + (100dvh - 640px) * 0.0545), 2.5rem);
			--profile-notice-gap: clamp(0.75rem, calc(0.75rem + (100dvh - 640px) * 0.0364), 1.75rem);
			--profile-notice-padding: clamp(
				0.625rem,
				calc(0.625rem + (100dvh - 640px) * 0.0227),
				1.25rem
			);
			--profile-button-gap: clamp(0.75rem, calc(0.75rem + (100dvh - 640px) * 0.0273), 1.5rem);
		}
	}
</style>
