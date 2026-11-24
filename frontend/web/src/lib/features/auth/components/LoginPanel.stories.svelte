<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import { fn } from 'storybook/test';
	import AuthShell from './AuthShell.svelte';
	import LoginPanel, { type LoginProvider } from './LoginPanel.svelte';

	const providers: LoginProvider[] = [
		{ id: 'kakao', label: '카카오로 시작하기', isAvailable: true },
		{ id: 'google', label: 'Google로 시작하기', isAvailable: true }
	];

	// 로그인 화면(/login) 그대로 보이도록 공통 틀(AuthShell) 안에 넣는다. 오른쪽 위 툴바에서 화면 폭을 바꿔 볼 수 있다
	const { Story } = defineMeta({
		title: 'Features/auth/LoginPanel',
		component: LoginPanel,
		tags: ['autodocs'],
		args: { providers, onstart: fn() },
		parameters: { layout: 'fullscreen' },
		render: template
	});
</script>

{#snippet template(args: ComponentProps<typeof LoginPanel>)}
	<AuthShell>
		<LoginPanel {...args} />
	</AuthShell>
{/snippet}

<Story name="기본" exportName="Default" />

<!-- 제공자 화면으로 넘어가는 동안 누른 버튼에 동그라미가 돌고 두 버튼이 잠긴다 -->
<Story name="카카오로 이동 중" exportName="Starting" args={{ startingProvider: 'kakao' }} />

<!-- client ID 가 빌드에 없는 제공자만 잠그고 안내한다 -->
<Story
	name="Google 준비 중"
	exportName="OneUnavailable"
	args={{ providers: [providers[0], { ...providers[1], isAvailable: false }] }}
/>

<Story
	name="로그인 준비 중"
	exportName="AllUnavailable"
	args={{ providers: providers.map((provider) => ({ ...provider, isAvailable: false })) }}
/>

<!-- HTTPS 가 아닌 주소이거나 저장소가 막혀 로그인을 시작하지 못했을 때 -->
<Story name="시작 실패" exportName="StartError" args={{ hasStartError: true }} />
