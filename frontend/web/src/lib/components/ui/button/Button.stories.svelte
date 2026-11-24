<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import Button from './Button.svelte';
	import {
		BUTTON_SIZES,
		BUTTON_VARIANTS,
		type ButtonSize,
		type ButtonVariant
	} from './button-variants';

	const variants = Object.keys(BUTTON_VARIANTS) as ButtonVariant[];
	const sizes = Object.keys(BUTTON_SIZES) as ButtonSize[];

	/** 크기마다 실제로 쓰는 곳 */
	const SIZE_USAGE: Record<ButtonSize, string> = {
		sm: '안내 상자 안의 작은 버튼',
		default: '대화 상자·카드 안의 버튼',
		lg: '폼의 제출·돌아가기, 로그인 전 화면의 큰 버튼',
		icon: '아이콘만 있는 둥근 버튼(닫기)'
	};

	const { Story } = defineMeta({
		title: 'Components/ui/Button',
		component: Button,
		tags: ['autodocs'],
		argTypes: {
			variant: { control: 'inline-radio', options: variants },
			size: { control: 'inline-radio', options: sizes },
			disabled: { control: 'boolean' }
		},
		args: { variant: 'default', size: 'default', disabled: false }
	});
</script>

{#snippet closeIcon()}
	<svg
		class="size-5"
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		aria-hidden="true"
	>
		<path d="M6 6l12 12M18 6 6 18" stroke-linecap="round" />
	</svg>
{/snippet}

<Story name="기본" exportName="Default">
	{#snippet template(args)}
		<Button {...args}>저장하기</Button>
	{/snippet}
</Story>

<Story name="모양" exportName="Variants">
	{#snippet template()}
		<div class="flex flex-wrap items-center gap-4">
			{#each variants as variant (variant)}
				<Button {variant}>{variant}</Button>
			{/each}
		</div>
	{/snippet}
</Story>

<Story name="크기" exportName="Sizes">
	{#snippet template()}
		<div class="flex flex-col items-start gap-4">
			{#each sizes as size (size)}
				<div class="flex items-center gap-4">
					{#if size === 'icon'}
						<Button {size} variant="ghost" class="text-muted-foreground" aria-label="닫기">
							{@render closeIcon()}
						</Button>
						<Button {size} variant="outline" aria-label="닫기">{@render closeIcon()}</Button>
					{:else}
						<Button {size}>{size}</Button>
						<Button {size} variant="outline">{size}</Button>
					{/if}
					<span class="text-sm text-muted-foreground">{SIZE_USAGE[size]}</span>
				</div>
			{/each}
		</div>
	{/snippet}
</Story>

<Story name="누를 수 없음" exportName="Disabled">
	{#snippet template()}
		<div class="flex flex-wrap items-center gap-4">
			<Button disabled>저장하기</Button>
			<Button disabled aria-busy="true">저장하는 중…</Button>
		</div>
	{/snippet}
</Story>

<Story name="링크" exportName="AsLink">
	{#snippet template()}
		<Button href="#login" size="lg">로그인 화면으로</Button>
	{/snippet}
</Story>

<!-- 폭·배치는 class 로 덧붙인다. 로그인 전 화면처럼 세로로 쌓을 때는 w-full, 폼 아래처럼 나란히 둘 때는 flex-1 -->
<Story name="폭과 배치" exportName="Layout">
	{#snippet template()}
		<div class="flex w-80 flex-col gap-6">
			<div class="flex flex-col gap-3">
				<Button size="lg" class="w-full">다시 시도</Button>
				<Button size="lg" variant="outline" href="#login" class="w-full">로그인 화면으로</Button>
			</div>
			<div class="flex gap-3">
				<Button size="lg" variant="outline" class="flex-1">돌아가기</Button>
				<Button size="lg" type="submit" class="flex-1">가입하기</Button>
			</div>
		</div>
	{/snippet}
</Story>
