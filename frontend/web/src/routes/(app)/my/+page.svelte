<script lang="ts">
	import { onMount } from 'svelte';
	import Toast from '#lib/components/feedback/Toast.svelte';
	import { ApiError } from '#lib/api/errors.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { SignupProfile, User } from '#lib/features/auth/types.js';
	import { authSession } from '#lib/features/auth/session.svelte.js';
	import { getMe, updateMe } from '#lib/features/profile/api.js';
	import ProfileForm from '#lib/features/profile/components/ProfileForm.svelte';
	import ProfileFormSkeleton from '#lib/features/profile/components/ProfileFormSkeleton.svelte';
	import ProfileGuide from '#lib/features/profile/components/ProfileGuide.svelte';
	import {
		getProfileChanges,
		getReasonMessage,
		isProfileField,
		toFormValues,
		type ProfileErrors
	} from '#lib/features/profile/validation.js';

	/** 저장 실패처럼 사용자가 읽고 고쳐야 하는 안내는 제목 아래에 남긴다 */
	type Notice = { kind: 'error'; message: string; requestId?: string | null };
	/**
	 * 저장했어요·바뀐 내용이 없어요는 잠깐 띄웠다 사라지는 토스트로 보여 준다(S15P31A403-438).
	 * 처음에는 제목 아래 초록 상자로 두었는데, 사라지지 않고 남아 폼을 아래로 밀었다(팀원 지적). 다른 화면 토스트와 같은 3초다
	 */
	const TOAST_DURATION_MS = 3000;

	let savedUser = $state<User | null>(null);
	let isLoading = $state(true);
	let hasLoadError = $state(false);
	let isSaving = $state(false);
	let serverErrors = $state<ProfileErrors>({});
	let notice = $state<Notice | null>(null);
	let toast = $state<string | null>(null);
	let toastTimer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => () => clearTimeout(toastTimer));

	function showToast(message: string) {
		toast = message;
		clearTimeout(toastTimer);
		toastTimer = setTimeout(() => (toast = null), TOAST_DURATION_MS);
	}

	function toApiNotice(error: unknown, fallback: string): Notice {
		return {
			kind: 'error',
			message: error instanceof ApiError && error.code ? error.message : fallback,
			requestId: error instanceof ApiError ? error.requestId : null
		};
	}

	const isAuthRequired = (error: unknown) =>
		error instanceof ApiError && error.code === 'AUTH_REQUIRED';

	async function load() {
		isLoading = true;
		hasLoadError = false;
		try {
			savedUser = await getMe();
			authSession.updateUser(savedUser);
		} catch (error) {
			// 탈퇴했거나 세션이 무효(401 AUTH_REQUIRED, 갱신으로도 복구 못 함)면 로그인 상태를 지워 AuthGate가 로그인 화면으로 보낸다.
			// 홈·건강 화면과 같은 처리다. 만료(AUTH_EXPIRED)는 API 클라이언트가 갱신 후 다시 보낸다
			if (isAuthRequired(error)) return authSession.clearLocalSession();
			hasLoadError = true;
		} finally {
			isLoading = false;
		}
	}

	onMount(load);

	async function handleSubmit(profile: SignupProfile) {
		if (!savedUser || isSaving) return;
		const changes = getProfileChanges(profile, savedUser);
		serverErrors = {};
		// 앞뒤 공백만 바꾼 경우 등. 빈 PATCH는 서버가 422로 거절한다
		if (Object.keys(changes).length === 0) {
			notice = null;
			showToast('바뀐 내용이 없어요.');
			return;
		}

		isSaving = true;
		notice = null;
		try {
			savedUser = await updateMe(changes);
			authSession.updateUser(savedUser);
			showToast('기본 정보를 저장했어요.');
		} catch (error) {
			if (isAuthRequired(error)) return authSession.clearLocalSession();
			if (error instanceof ApiError && error.code === 'INVALID_REQUEST') {
				const fieldErrors: ProfileErrors = {};
				for (const { field, reason } of error.details) {
					if (isProfileField(field)) fieldErrors[field] = getReasonMessage(field, reason);
				}
				serverErrors = fieldErrors;
				if (Object.keys(fieldErrors).length > 0) return;
			}
			// 응답 유실·형식이 다른 응답(502·504 등 프록시 오류)은 서버 저장 여부를 알 수 없으므로
			// 다시 보내지 않고 저장값을 다시 읽는다(명세 4.2). 명세 오류 코드가 있으면 저장되지 않은 것이다
			if (!(error instanceof ApiError) || error.code === null) {
				notice = {
					kind: 'error',
					message: '저장 결과를 확인하지 못해 저장된 정보를 다시 불러왔어요.'
				};
				await load();
				return;
			}
			notice = toApiNotice(error, '저장하지 못했어요. 잠시 후 다시 시도해 주세요.');
		} finally {
			isSaving = false;
		}
	}
</script>

<svelte:head><title>마이페이지 | 캔디핏</title></svelte:head>

<main
	class="fit-height mx-auto w-full max-w-[1344px] flex-1 px-5 pt-8 pb-12 sm:px-8 lg:flex lg:flex-col lg:pt-(--main-top) lg:pb-(--main-bottom)"
>
	<!-- 화면이 내용보다 높으면 카드가 남은 높이를 채워 아래에 빈 공간이 어색하게 남지 않게 한다 -->
	<div
		class="grid grid-cols-1 gap-8 rounded-[40px] border border-border bg-card p-5 sm:p-8 lg:flex-1 lg:grid-cols-[minmax(0,454px)_minmax(0,1fr)] lg:p-(--card-padding)"
	>
		<ProfileGuide
			titleLines={['입력한 정보는', '건강 데이터를 더 잘', '보여주기 위해 활용돼요!']}
			descriptionLines={[
				'닉네임, 성별, 태어난 연도, 키, 몸무게를',
				'현재 정보에 맞게 수정할 수 있어요.'
			]}
		/>

		<div class="min-w-0 lg:pt-(--column-top) lg:pr-2">
			<h1 class="text-page-title lg:text-(length:--page-title)">기본 정보 수정</h1>
			<p class="mt-3 text-[15px] leading-[normal] text-text-secondary">
				건강 기록을 더 잘 이해할 수 있도록 기본 정보를 입력해 주세요.
			</p>

			{#if notice}
				<div
					role="alert"
					class="mt-6 rounded-2xl bg-danger-soft px-5 py-3 text-sm leading-[normal]"
				>
					<p>{notice.message}</p>
					{#if notice.requestId}
						<p class="mt-1">요청 ID: {notice.requestId}</p>
					{/if}
				</div>
			{/if}

			<div class="mt-10 lg:mt-(--profile-gap)">
				{#if isLoading}
					<ProfileFormSkeleton />
				{:else if hasLoadError || !savedUser}
					<div role="alert" class="py-10 text-center">
						<p class="text-text-secondary">기본 정보를 불러오지 못했어요.</p>
						<Button variant="outline" size="lg" class="mt-4" onclick={load}>다시 시도</Button>
					</div>
				{:else}
					<!-- 저장하면 저장값으로 폼을 다시 시작해 바뀐 내용 판단을 새로 한다 -->
					{#key savedUser}
						<ProfileForm
							submitLabel="저장하기"
							initialValues={toFormValues(savedUser)}
							isSubmitting={isSaving}
							{serverErrors}
							onSubmit={handleSubmit}
						/>
					{/key}
				{/if}
			</div>
		</div>
	</div>
</main>

{#if toast}
	<Toast message={toast} />
{/if}

<style>
	/*
	 * 노트북·모니터(lg 이상)에서 세로 스크롤 없이 보이도록 간격·크기를 화면 높이에 맞춰 줄인다.
	 * 높이 640px에서 최소값, 1080px(Figma)에서 시안 값이 되고 그 사이는 고르게 바뀐다.
	 * 계산식: 최소값 + (100dvh - 640px) × (시안 값 - 최소값) / 440
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
			--page-title: clamp(1.375rem, calc(1.375rem + (100dvh - 640px) * 0.0136), 1.875rem);
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
