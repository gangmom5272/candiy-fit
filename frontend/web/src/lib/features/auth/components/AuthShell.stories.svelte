<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import AuthShell, { INTRO_POINTS } from './AuthShell.svelte';
	import { Button } from '#lib/components/ui/button/index.js';

	// 로그인 전 화면(로그인·로그인 처리·가입·로그인 확인 실패)의 공통 틀. 왼쪽 소개 패널 + 오른쪽 내용. 오른쪽 위 툴바에서 화면 폭을 바꿔 쌓이는 모양을 본다
	const { Story } = defineMeta({
		title: 'Features/auth/AuthShell',
		component: AuthShell,
		tags: ['autodocs'],
		parameters: { layout: 'fullscreen' },
		render: template
	});
</script>

{#snippet template({ headline, points }: ComponentProps<typeof AuthShell>)}
	<AuthShell {headline} {points}>
		<!-- 오른쪽 칸에 들어갈 내용 예시. 실제 화면은 LoginPanel·AuthStatusPanel·SignupPanel이 들어간다 -->
		<div class="flex flex-col gap-4">
			<p class="text-xs font-bold tracking-wide text-brand">CANDiY-FiT</p>
			<h1 class="text-[26px] leading-[normal] font-bold">오른쪽 내용 자리</h1>
			<p class="text-sm leading-relaxed text-text-secondary">
				로그인 버튼이나 안내 글처럼 화면마다 다른 내용이 이 칸에 들어가요.
			</p>
			<Button size="lg" class="w-full">버튼 예시</Button>
		</div>
	</AuthShell>
{/snippet}

<Story name="기본 문구·아이콘 목록" exportName="Default" />

<!-- 소개 목록에 아이콘이 없으면 순번(1·2·3)으로 보여 준다 -->
<Story
	name="아이콘 없는 목록 (순번)"
	exportName="NumberedPoints"
	args={{
		headline: '로그인 상태를\n확인하고 있어요',
		points: INTRO_POINTS.map(({ title, description }) => ({ title, description }))
	}}
/>

<Story name="짧은 문구" exportName="ShortHeadline" args={{ headline: '다시 로그인해 주세요' }} />
