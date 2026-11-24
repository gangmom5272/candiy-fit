<script lang="ts" module>
	import coachIcon from '#lib/assets/icons/icon-coach.svg';
	import healthIcon from '#lib/assets/icons/icon-health.svg';
	import heartIcon from '#lib/assets/icons/icon-heart.svg';

	export type AuthShellPoint = { title: string; description: string; icon?: string };

	/** 로그인 전 화면에서 서비스를 소개하는 기본 문구. 홈·건강 데이터·AI 코치 세 메뉴와 같은 순서다 */
	export const INTRO_POINTS: AuthShellPoint[] = [
		{
			title: '워치 기록을 자동으로',
			description: 'LiNK 앱이 걸음·심박·수면을 모아 보내요',
			icon: heartIcon
		},
		{
			title: '한눈에 보는 건강 데이터',
			description: '활동·수면·심박을 주간과 하루 단위로',
			icon: healthIcon
		},
		{
			title: '내 기록으로 답하는 AI 코치',
			description: '궁금한 생활 습관을 기록과 함께 물어봐요',
			icon: coachIcon
		}
	];
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import logo from '#lib/assets/images/logo.svg';
	import characterImage from '#lib/assets/images/character-profile.webp';

	type Props = {
		/** 소개 패널 큰 문구. 줄바꿈(\n)마다 줄을 나눈다. 소개 패널은 태블릿 이상에서만 보인다 */
		headline?: string;
		/** 소개 패널 목록. 아이콘이 없으면 순번으로 보여 준다 */
		points?: AuthShellPoint[];
		children: Snippet;
	};

	let {
		headline = '워치가 모은 기록을\n한눈에 보고, 물어보세요',
		points = INTRO_POINTS,
		children
	}: Props = $props();

	const lines = $derived(headline.split('\n'));
</script>

<!--
	로그인 전 화면(로그인·로그인 처리·가입·로그인 확인 실패)의 공통 틀. 마이페이지와 같은 구성으로
	큰 흰 카드 안 왼쪽에 연분홍 소개 패널, 오른쪽에 화면 내용을 둔다. 태블릿에서는 위아래로 쌓는다.
	휴대폰(sm 미만)에서는 소개 패널을 빼고 화면 내용만 카드 가운데에 둔다(S15P31A403-347). 내용이 적어 위쪽 상자만 떠 보였다.
	카드는 화면 높이를 꽉 채우고(위아래 여백만 남김), 넓은 화면에서는 화면 밖으로 넘치지 않게 높이를 화면에 맞춘다.
	공통 헤더가 없으므로 로고는 소개 패널에 둔다.
-->
<div class="min-h-dvh bg-background text-text-primary">
	<main
		class="mx-auto flex min-h-dvh w-full max-w-[1344px] flex-col px-4 py-3 sm:px-8 sm:py-6 lg:h-dvh lg:py-8"
	>
		<div
			class="grid w-full flex-1 grid-cols-1 grid-rows-1 gap-5 rounded-[40px] border border-border bg-card p-4 sm:grid-rows-[auto_1fr] sm:gap-8 sm:p-8 lg:min-h-[32rem] lg:grid-cols-[minmax(0,30rem)_minmax(0,1fr)] lg:grid-rows-1 lg:p-10"
		>
			<!-- 휴대폰에는 없다. 태블릿은 안내 목록까지, 캐릭터는 넓은 화면(lg)에서만 둔다. 쌓인 화면에서도 첫 화면에 버튼이 들어온다 -->
			<section
				aria-label="캔디핏 소개"
				class="relative hidden min-h-0 flex-col overflow-hidden rounded-[32px] bg-primary-soft sm:flex sm:p-9 lg:pb-6"
			>
				<div>
					<img src={logo} alt="캔디핏" width="68" height="28" class="h-7 w-auto" />
					<p class="mt-8 text-[28px] leading-[1.45] font-bold break-keep lg:text-[30px]">
						{#each lines as line, index (index)}
							<span class="block">{line}</span>
						{/each}
					</p>
				</div>
				<ol class="mt-8 flex flex-col gap-6">
					{#each points as point, index (point.title)}
						<li class="flex items-center gap-4">
							<span
								class="grid size-11 shrink-0 place-items-center rounded-full border border-border bg-card text-base font-semibold text-primary"
								aria-hidden="true"
							>
								{#if point.icon}
									<img src={point.icon} alt="" width="22" height="22" class="size-5.5" />
								{:else}
									{index + 1}
								{/if}
							</span>
							<div class="leading-[normal]">
								<p class="text-[17px] font-bold">{point.title}</p>
								<p class="mt-1 text-sm break-keep text-text-secondary">{point.description}</p>
							</div>
						</li>
					{/each}
				</ol>
				<!--
					회원가입·마이페이지 안내 패널(ProfileGuide)과 같은 캐릭터. 패널에 남는 높이만큼만 차지하고(h-0 grow)
					아래에 붙는다. 화면 높이가 낮으면 그만큼 줄어들어 넘치지 않는다
				-->
				<img
					src={characterImage}
					alt=""
					width="300"
					height="163"
					class="mx-auto hidden h-0 min-h-0 w-75 max-w-full grow object-contain object-bottom lg:block"
				/>
			</section>

			<!-- 버튼·입력칸이 넓은 화면에서 지나치게 늘어나지 않게 폭을 제한한다 -->
			<div class="flex flex-col justify-center px-1 pb-2 sm:px-4 lg:px-8">
				<div class="mx-auto w-full max-w-[30rem]">{@render children()}</div>
			</div>
		</div>
	</main>
</div>
