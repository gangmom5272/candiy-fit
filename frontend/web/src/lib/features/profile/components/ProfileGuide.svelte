<script lang="ts">
	import characterImage from '#lib/assets/images/character-profile.webp';

	type Props = {
		/** 큰 문구. 줄마다 나눠 준다(Figma 줄바꿈 그대로) */
		titleLines: string[];
		/** 설명. 넓은 화면(sm 이상)에서만 줄을 나눈다 */
		descriptionLines: string[];
	};

	let { titleLines, descriptionLines }: Props = $props();

	const guideItems = [
		{ title: '닉네임', description: '서비스에서 표시할 이름' },
		{ title: '성별 · 태어난 연도', description: '기록 해석을 위한 기본 정보' },
		{ title: '키 · 몸무게', description: '건강 데이터 요약에 활용' }
	];
</script>

<!--
	기본 정보 안내(Figma WEB_기본정보입력 · WEB_마이페이지 왼쪽 패널). 회원가입과 마이페이지가 같은 모양을 쓴다.
	lg 이상의 간격·글자 크기는 화면의 .fit-height 가 정하는 변수(--guide-*, --character-width)를 따른다.
	화면 높이가 낮으면 캐릭터는 숨기지 않고 패널 남는 높이만큼 줄여 패널 아래가 비지 않게 한다.
	캐릭터 높이를 0에서 남는 만큼 늘리므로(h-0 grow) 캐릭터가 카드 높이를 늘리지 않는다.
	휴대폰(sm 미만)에서는 보여 주지 않는다(S15P31A403-347). 입력칸마다 이름표가 있고 폼 아래 안내가 쓰임을 알려 주므로,
	좁은 화면에서는 입력칸이 바로 보이게 한다
-->
<section
	aria-label="기본 정보 안내"
	class="hidden flex-col rounded-[32px] bg-primary-soft px-6 pt-8 pb-6 sm:flex sm:px-8 lg:pt-(--guide-top)"
>
	<p
		class="text-2xl leading-[1.5] font-bold break-keep sm:text-[30px] lg:text-(length:--guide-title)"
	>
		{#each titleLines as line, index (index)}
			{#if index > 0}<br />{/if}{line}
		{/each}
	</p>
	<p class="mt-6 text-base leading-[1.6] break-keep sm:text-lg lg:mt-(--guide-body-gap)">
		{#each descriptionLines as line, index (index)}
			<!-- 휴대폰에서는 이어 쓰므로 줄 앞에 띄어쓰기를 둔다(줄 첫 공백은 보이지 않는다) -->
			{#if index > 0}<br class="hidden sm:inline" />{/if}{index > 0 ? ' ' : ''}{line}
		{/each}
	</p>
	<ol class="mt-10 flex flex-col gap-7 lg:mt-(--guide-list-gap) lg:gap-(--guide-item-gap)">
		{#each guideItems as item, index (item.title)}
			<li class="flex gap-4">
				<span
					class="grid size-9 shrink-0 place-items-center rounded-full border border-border bg-card text-base font-semibold text-primary"
					aria-hidden="true">{index + 1}</span
				>
				<div class="leading-[normal]">
					<p class="text-lg font-bold">{item.title}</p>
					<p class="mt-1.5 text-[15px] text-text-secondary">{item.description}</p>
				</div>
			</li>
		{/each}
	</ol>
	<img
		src={characterImage}
		alt=""
		width="300"
		height="163"
		class="mx-auto mt-6 hidden w-75 max-w-full sm:block lg:mt-0 lg:h-0 lg:min-h-0 lg:w-(--character-width) lg:grow lg:object-contain lg:object-bottom"
	/>
</section>
