<script lang="ts">
	type Props = {
		/** 크기·여백(h-*, w-*, mt-*). 모서리는 rounded로 정한다 */
		class?: string;
		/** 모서리. 글줄·입력칸은 lg(8px), 말풍선 xl, 큰 상자 2xl, 동그라미 full */
		rounded?: 'lg' | 'xl' | '2xl' | 'full';
	};

	let { class: className = '', rounded = 'lg' }: Props = $props();

	const ROUNDED = {
		lg: 'rounded-lg',
		xl: 'rounded-xl',
		'2xl': 'rounded-2xl',
		full: 'rounded-full'
	} as const;
</script>

<!--
	불러오는 동안 자리만 잡는 회색 상자. 건강·홈 화면의 .health-skeleton(routes/layout.css)과 같은 모양이다:
	surface 색, 120ms 뒤에 서서히 나타나 아주 짧은 로딩에는 깜빡이지 않는다(움직임 줄이기 설정이면 바로 보인다).
	의미 없는 장식이라 aria-hidden이다. 읽어 줄 안내(role="status"·sr-only)는 쓰는 쪽이 한 번만 둔다.
-->
<span aria-hidden="true" class={['skeleton block', ROUNDED[rounded], className]}></span>

<style>
	.skeleton {
		background-color: var(--color-surface);
		opacity: 0;
		animation: skeleton-appear 120ms ease-out 120ms forwards;
	}
	@keyframes skeleton-appear {
		to {
			opacity: 1;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.skeleton {
			animation: none;
			opacity: 1;
		}
	}
</style>
