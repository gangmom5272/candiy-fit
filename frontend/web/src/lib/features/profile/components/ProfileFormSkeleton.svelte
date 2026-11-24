<script lang="ts">
	import Skeleton from '#lib/components/feedback/Skeleton.svelte';

	// 입력칸 상자: ProfileForm의 boxClass와 같은 높이·모서리·위 여백
	const BOX = 'mt-3 h-15 lg:h-(--profile-field-height,4.75rem)';
	// 성별·태어난 연도·키·몸무게 이름표 폭(글자 수에 맞춤)
	const LABELS = ['w-8', 'w-20', 'w-5', 'w-12'];
</script>

<!--
	저장된 기본 정보를 불러오는 동안 ProfileForm과 같은 자리에 같은 크기의 상자를 둔다.
	닉네임 → (성별·태어난 연도·키·몸무게) 2열 → 안내 상자 → 저장 버튼 순서와 간격(--profile-*)이 폼과 같아
	불러온 뒤 화면이 튀지 않는다.
-->
<div role="status" aria-label="기본 정보를 불러오고 있어요.">
	<span class="sr-only">기본 정보를 불러오고 있어요.</span>
	<div aria-hidden="true" class="flex flex-col">
		<div>
			<Skeleton class="h-4 w-12" />
			<Skeleton rounded="2xl" class={BOX} />
		</div>

		<div
			class="mt-10 grid grid-cols-1 gap-x-7 gap-y-10 sm:grid-cols-2 lg:mt-(--profile-gap,2.5rem) lg:gap-y-(--profile-gap,2.5rem)"
		>
			{#each LABELS as width (width)}
				<div>
					<Skeleton class={`h-4 ${width}`} />
					<Skeleton rounded="2xl" class={BOX} />
				</div>
			{/each}
		</div>

		<Skeleton
			rounded="2xl"
			class="mt-7 h-[5.75rem] lg:mt-(--profile-notice-gap,1.75rem) lg:h-[calc(3.25rem+var(--profile-notice-padding,1.25rem)*2)]"
		/>
		<Skeleton
			rounded="2xl"
			class="mt-6 h-13 w-full self-end sm:w-50 lg:mt-(--profile-button-gap,1.5rem)"
		/>
	</div>
</div>
