<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import { conversationFixtures } from '../fixtures';
	import type { BoundaryNoticeContent } from '../types';
	import BoundaryNotice from './BoundaryNotice.svelte';

	const boundaryFixture = conversationFixtures[2];
	const content = Object.values(boundaryFixture.extras)[0].boundary as BoundaryNoticeContent;

	const { Story } = defineMeta({
		title: 'Features/coaching/BoundaryNotice',
		component: BoundaryNotice,
		tags: ['autodocs'],
		args: { content, onselect: fn() },
		parameters: {
			docs: {
				description: {
					component:
						'진단·처방 질문에 답하는 대신 보여 주는 경계 안내. API 명세에 아직 없어 더미 데이터로만 보여 준다. 추천 질문을 누르면 입력창에 채워진다.'
				}
			}
		}
	});
</script>

<Story name="기본 · 추천 질문 있음" exportName="Default" />

<Story
	name="추천 질문 없음"
	exportName="WithoutSuggestions"
	args={{ content: { ...content, suggestions: [] } }}
/>

<Story name="좁은 화면 (320px)" exportName="Narrow" asChild>
	<div style:width="17.5rem">
		<BoundaryNotice {content} />
	</div>
</Story>
