<script lang="ts" module>
	import type { HTMLAnchorAttributes, HTMLButtonAttributes } from 'svelte/elements';
	import type { ButtonSize, ButtonVariant } from './button-variants';

	export type ButtonProps = {
		variant?: ButtonVariant;
		size?: ButtonSize;
		/** 요소 참조. 포커스를 옮길 때 쓴다 */
		ref?: HTMLButtonElement | HTMLAnchorElement | null;
	} & (
		| (HTMLButtonAttributes & { href?: undefined })
		| (HTMLAnchorAttributes & { href: string; disabled?: boolean })
	);
</script>

<script lang="ts">
	import { buttonVariants } from './button-variants';

	/**
	 * 공통 Button(S15P31A403-362). shadcn/ui Button 과 같은 사용법이다.
	 * - href 를 주면 같은 모양의 링크(a)가 된다. 동작은 button, 이동은 link 라는 기준(docs/design-system.md)을 지킨다.
	 * - type 기본값은 button 이라 폼 안에서 뜻하지 않게 제출되지 않는다. 제출 버튼은 type="submit" 을 준다.
	 * - class 는 폭·배치(w-full, flex-1 등)를 덧붙이는 데만 쓴다. 색·높이는 variant · size 로 고른다.
	 */
	let {
		variant = 'default',
		size = 'default',
		ref = $bindable(null),
		href = undefined,
		type = 'button',
		disabled = false,
		class: className,
		children,
		...rest
	}: ButtonProps = $props();

	const classes = $derived([buttonVariants({ variant, size }), className]);
</script>

{#if href !== undefined}
	<!-- 누를 수 없는 링크는 href 를 빼고 탭 순서에서 뺀다. 주소는 호출하는 쪽에서 resolve() 로 만들어 넘긴다 -->
	<!-- eslint-disable svelte/no-navigation-without-resolve -->
	<a
		bind:this={ref}
		data-slot="button"
		class={classes}
		href={disabled ? undefined : href}
		aria-disabled={disabled || undefined}
		role={disabled ? 'link' : undefined}
		tabindex={disabled ? -1 : undefined}
		{...rest as HTMLAnchorAttributes}
	>
		{@render children?.()}
	</a>
	<!-- eslint-enable svelte/no-navigation-without-resolve -->
{:else}
	<button
		bind:this={ref}
		data-slot="button"
		class={classes}
		type={type as HTMLButtonAttributes['type']}
		{disabled}
		{...rest as HTMLButtonAttributes}
	>
		{@render children?.()}
	</button>
{/if}
