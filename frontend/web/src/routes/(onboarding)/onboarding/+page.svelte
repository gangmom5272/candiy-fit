<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { ApiError } from '#lib/api/errors.js';
	import type { SignupProfile } from '#lib/features/auth/types.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import AuthShell from '#lib/features/auth/components/AuthShell.svelte';
	import AuthStatusPanel from '#lib/features/auth/components/AuthStatusPanel.svelte';
	import SignupPanel from '#lib/features/auth/components/SignupPanel.svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import {
		getReasonMessage,
		isProfileField,
		type ProfileErrors
	} from '#lib/features/profile/validation.js';

	let isReady = $state(false);
	let isSubmitting = $state(false);
	let isLoginRequired = $state(false);
	let serverErrors = $state<ProfileErrors>({});
	let formError = $state<{ message: string; requestId: string | null } | null>(null);

	onMount(() => {
		if (authSession.status === 'authenticated') {
			goto(resolve('/(app)/home'), { replaceState: true });
			return;
		}
		// 가입 자격은 메모리에만 있으므로 새로고침하면 소셜 로그인부터 다시 한다
		if (!authSession.getSignupCredential()) {
			goto(resolve('/(public)/login'), { replaceState: true });
			return;
		}
		isReady = true;
	});

	async function handleSubmit(profile: SignupProfile) {
		isSubmitting = true;
		serverErrors = {};
		formError = null;
		try {
			const outcome = await authSession.signup(profile);
			if (outcome === 'signed-up') {
				await goto(resolve('/(app)/home'), { replaceState: true });
				return;
			}
			isLoginRequired = true;
		} catch (error) {
			// 422는 자격을 쓰지 않았으므로 같은 화면에서 고쳐 다시 보낼 수 있다(명세 3.4)
			if (error instanceof ApiError && error.code === 'INVALID_REQUEST') {
				const fieldErrors: ProfileErrors = {};
				for (const { field, reason } of error.details) {
					if (isProfileField(field)) fieldErrors[field] = getReasonMessage(field, reason);
				}
				serverErrors = fieldErrors;
				// 입력칸에 붙일 수 없는 오류(signupToken 등)가 섞이면 아래 공통 문구도 보여 준다
				const hasOnlyFieldErrors =
					error.details.length > 0 && error.details.every(({ field }) => isProfileField(field));
				if (hasOnlyFieldErrors) return;
			}
			formError = {
				message:
					error instanceof ApiError && error.code
						? error.message
						: '가입하지 못했어요. 잠시 후 다시 시도해 주세요.',
				requestId: error instanceof ApiError ? error.requestId : null
			};
		} finally {
			isSubmitting = false;
		}
	}
</script>

<svelte:head><title>회원가입 | 캔디핏</title></svelte:head>

<!-- 입력 화면은 Figma WEB_기본정보입력(마이페이지와 같은 카드). 다시 로그인 안내만 로그인 전 공통 틀을 쓴다 -->
{#if isLoginRequired}
	<AuthShell>
		<AuthStatusPanel
			tone="error"
			title="다시 로그인해 주세요"
			message="가입 가능 시간(10분)이 지났거나 가입 정보를 확인할 수 없어요."
		>
			{#snippet actions()}
				<Button size="lg" class="w-full" href={resolve('/(public)/login')}>로그인 화면으로</Button>
			{/snippet}
		</AuthStatusPanel>
	</AuthShell>
{:else if isReady}
	<!-- 돌아가기: 다른 소셜 계정으로 다시 시작할 수 있게 로그인 화면으로 보낸다. 가입 자격은 10분 뒤 스스로 만료된다 -->
	<SignupPanel
		{isSubmitting}
		{serverErrors}
		{formError}
		onBack={() => goto(resolve('/(public)/login'))}
		onSubmit={handleSubmit}
	/>
{/if}
