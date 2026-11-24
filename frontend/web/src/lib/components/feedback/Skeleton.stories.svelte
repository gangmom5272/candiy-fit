<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import Skeleton from './Skeleton.svelte';

	// 불러오는 동안 자리를 잡는 회색 상자. 건강·홈의 .health-skeleton과 같은 모양(surface 색·8px 모서리·120ms 뒤 나타남)
	const { Story } = defineMeta({
		title: 'Components/feedback/Skeleton',
		component: Skeleton,
		tags: ['autodocs'],
		args: { class: 'h-4 w-48' }
	});
</script>

<Story name="글줄 (기본 lg 모서리)" exportName="Line" />

<Story name="말풍선 (xl 모서리)" exportName="Bubble" args={{ rounded: 'xl', class: 'h-13 w-72' }} />

<Story
	name="입력칸 (2xl 모서리)"
	exportName="Field"
	args={{ rounded: '2xl', class: 'h-15 w-80' }}
/>

<Story
	name="동그라미 (캐릭터 자리)"
	exportName="Circle"
	args={{ rounded: 'full', class: 'size-10' }}
/>
