<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.js';
	import type { SignupProfile } from '#lib/features/auth/types.js';
	import {
		applyLimit,
		validateProfile,
		type ProfileErrors,
		type ProfileField,
		type ProfileFormValues,
		type TextProfileField
	} from '../validation';

	type Props = {
		submitLabel: string;
		/** 저장된 값으로 시작한다(마이페이지). */
		initialValues?: ProfileFormValues;
		isSubmitting?: boolean;
		/** 서버 422 details 등 바깥에서 정한 입력칸 오류 */
		serverErrors?: ProfileErrors;
		/** 있으면 제출 버튼 왼쪽에 돌아가기 버튼을 둔다(회원가입에서 로그인 화면으로) */
		onBack?: () => void;
		onSubmit: (profile: SignupProfile) => void;
	};

	let {
		submitLabel,
		initialValues,
		isSubmitting = false,
		serverErrors = {},
		onBack,
		onSubmit
	}: Props = $props();

	const EMPTY: ProfileFormValues = {
		displayName: '',
		birthYear: '',
		gender: '',
		heightCm: '',
		weightKg: ''
	};

	const formId = $props.id();
	// 처음 값만 쓴다. 저장 후 새 값으로 시작하려면 바깥에서 {#key}로 다시 만든다.
	let values = $state<ProfileFormValues>({ ...untrack(() => initialValues ?? EMPTY) });
	let localErrors = $state<ProfileErrors>({});
	const errors = $derived<ProfileErrors>({ ...serverErrors, ...localErrors });

	const genders = [
		{ value: 'MALE', label: '남성' },
		{ value: 'FEMALE', label: '여성' }
	] as const;

	let form = $state<HTMLFormElement>();

	/**
	 * 칸에 들어갈 수 없는 글자는 입력 즉시 지운다(붙여넣기 포함).
	 * 한글 조합 중에 값을 바꾸면 글자가 깨지므로 조합이 끝난 뒤(compositionend) 정리한다.
	 */
	function applyInput(field: TextProfileField, event: Event & { currentTarget: HTMLInputElement }) {
		if (event instanceof InputEvent && event.isComposing) return;
		const input = event.currentTarget;
		const raw = input.value;
		const next = applyLimit(field, raw, input.selectionStart ?? raw.length);
		values[field] = next.value;
		if (next.value === raw) return;
		// 같은 값이면 화면이 다시 그려지지 않으므로 직접 넣고, 입력하던 자리에 커서를 둔다
		input.value = next.value;
		input.setSelectionRange(next.caret, next.caret);
	}

	async function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		if (isSubmitting) return;
		const result = validateProfile(values);
		localErrors = result.ok ? {} : result.errors;
		if (!result.ok) {
			await tick();
			// radio는 aria-invalid를 지원하지 않아 data-invalid로 표시한다
			form?.querySelector<HTMLElement>('[aria-invalid="true"], [data-invalid="true"]')?.focus();
			return;
		}
		onSubmit(result.profile);
	}

	// legend와 label의 높이를 맞추려고 둘 다 block으로 둔다
	// lg 이상 세로 크기는 화면이 정한 CSS 변수(--profile-*)를 따르고, 없으면 Figma 값을 쓴다
	const labelClass = 'block text-base leading-[normal] font-bold';
	const boxClass =
		'mt-3 flex h-15 items-center gap-3 rounded-2xl border border-border bg-card px-6 has-[[aria-invalid=true]]:border-danger has-focus:outline-2 has-focus:outline-offset-2 has-focus:outline-ring lg:h-(--profile-field-height,4.75rem)';
	const inputClass =
		'h-full min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-text-secondary';

	function errorId(field: ProfileField) {
		return `${formId}-${field}-error`;
	}

	function describedBy(field: ProfileField, hintId?: string) {
		return (
			[hintId, errors[field] ? errorId(field) : undefined].filter(Boolean).join(' ') || undefined
		);
	}
</script>

{#snippet fieldError(field: ProfileField)}
	{#if errors[field]}
		<p id={errorId(field)} class="mt-1.5 text-base text-danger">{errors[field]}</p>
	{/if}
{/snippet}

<form bind:this={form} class="flex flex-col" novalidate onsubmit={handleSubmit}>
	<!-- 저장 중에는 입력을 잠근다. 응답 후 저장값으로 폼을 다시 시작하므로 그 사이 입력은 사라진다 -->
	<fieldset class="contents" disabled={isSubmitting}>
		<div>
			<label for={`${formId}-displayName`} class={labelClass}>닉네임</label>
			<div class={boxClass}>
				<input
					id={`${formId}-displayName`}
					class={inputClass}
					value={values.displayName}
					oninput={(event) => applyInput('displayName', event)}
					oncompositionend={(event) => applyInput('displayName', event)}
					autocomplete="nickname"
					placeholder="닉네임을 입력해 주세요"
					aria-invalid={errors.displayName ? 'true' : undefined}
					aria-describedby={describedBy('displayName', `${formId}-displayName-hint`)}
				/>
				<span id={`${formId}-displayName-hint`} class="shrink-0 text-base text-text-secondary"
					>최대 12자</span
				>
			</div>
			{@render fieldError('displayName')}
		</div>

		<div
			class="mt-10 grid grid-cols-1 gap-x-7 gap-y-10 sm:grid-cols-2 lg:mt-(--profile-gap,2.5rem) lg:gap-y-(--profile-gap,2.5rem)"
		>
			<fieldset aria-describedby={errors.gender ? errorId('gender') : undefined}>
				<legend class={labelClass}>성별</legend>
				<div
					class={[
						'mt-3 grid h-15 grid-cols-2 items-center gap-2 rounded-2xl border bg-card px-6 lg:h-(--profile-field-height,4.75rem)',
						errors.gender ? 'border-danger' : 'border-border'
					]}
				>
					{#each genders as gender (gender.value)}
						<label
							class="flex h-10 cursor-pointer items-center justify-center rounded-full border border-border text-base font-semibold text-text-secondary has-checked:border-primary-light has-checked:bg-primary-soft has-checked:text-primary has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring"
						>
							<input
								type="radio"
								class="sr-only"
								name={`${formId}-gender`}
								value={gender.value}
								bind:group={values.gender}
								data-invalid={errors.gender ? 'true' : undefined}
							/>
							{gender.label}
						</label>
					{/each}
				</div>
				{@render fieldError('gender')}
			</fieldset>

			<div>
				<label for={`${formId}-birthYear`} class={labelClass}>태어난 연도</label>
				<div class={boxClass}>
					<input
						id={`${formId}-birthYear`}
						class={inputClass}
						value={values.birthYear}
						oninput={(event) => applyInput('birthYear', event)}
						oncompositionend={(event) => applyInput('birthYear', event)}
						inputmode="numeric"
						maxlength="4"
						autocomplete="bday-year"
						placeholder="예: 2007"
						aria-invalid={errors.birthYear ? 'true' : undefined}
						aria-describedby={describedBy('birthYear', `${formId}-birthYear-hint`)}
					/>
					<span id={`${formId}-birthYear-hint`} class="shrink-0 text-base text-text-secondary"
						>숫자 4자리</span
					>
				</div>
				{@render fieldError('birthYear')}
			</div>

			{#each [{ field: 'heightCm', label: '키', unit: 'cm', placeholder: '예: 170' }, { field: 'weightKg', label: '몸무게', unit: 'kg', placeholder: '예: 55' }] as const as measure (measure.field)}
				<div>
					<label for={`${formId}-${measure.field}`} class={labelClass}>{measure.label}</label>
					<div class={boxClass}>
						<input
							id={`${formId}-${measure.field}`}
							class={inputClass}
							value={values[measure.field]}
							oninput={(event) => applyInput(measure.field, event)}
							oncompositionend={(event) => applyInput(measure.field, event)}
							inputmode="decimal"
							placeholder={measure.placeholder}
							aria-invalid={errors[measure.field] ? 'true' : undefined}
							aria-describedby={describedBy(measure.field, `${formId}-${measure.field}-unit`)}
						/>
						<span id={`${formId}-${measure.field}-unit`} class="shrink-0 text-base font-semibold"
							>{measure.unit}</span
						>
					</div>
					{@render fieldError(measure.field)}
				</div>
			{/each}
		</div>

		<div
			class="mt-7 flex items-center gap-4 rounded-2xl bg-surface px-6 py-5 lg:mt-(--profile-notice-gap,1.75rem) lg:py-(--profile-notice-padding,1.25rem)"
		>
			<span
				class="grid size-8 shrink-0 place-items-center rounded-full bg-primary-soft text-base font-bold text-primary"
				aria-hidden="true">i</span
			>
			<div class="leading-[normal] break-keep">
				<p class="text-base font-bold">입력한 정보는 건강 기록을 보여주는 데에만 사용돼요.</p>
				<p class="mt-1 text-base text-text-secondary">
					질문에 따라 AI 코치 답변의 기준 정보로 함께 활용될 수 있어요.
				</p>
			</div>
		</div>
	</fieldset>

	<!-- 돌아가기가 있으면 휴대폰에서는 두 버튼이 반씩, sm 이상에서는 오른쪽에 나란히 놓인다. 읽는 순서는 돌아가기 → 제출이다.
	     두 버튼의 폭이 같도록 Button 은 모양과 관계없이 테두리를 둔다(주요 버튼은 투명 테두리) -->
	<div
		class="mt-6 flex w-full gap-3 self-end sm:w-auto sm:gap-4 lg:mt-(--profile-button-gap,1.5rem)"
	>
		{#if onBack}
			<Button size="lg" variant="outline" class="flex-1 sm:w-50 sm:flex-none" onclick={onBack}
				>돌아가기</Button
			>
		{/if}
		<Button
			size="lg"
			type="submit"
			class="flex-1 sm:w-50 sm:flex-none"
			disabled={isSubmitting}
			aria-busy={isSubmitting}
		>
			{submitLabel}
		</Button>
	</div>
</form>
