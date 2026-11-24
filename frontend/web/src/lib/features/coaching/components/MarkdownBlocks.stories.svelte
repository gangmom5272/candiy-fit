<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { parseMarkdown } from '../markdown';
	import MarkdownBlocks from './MarkdownBlocks.svelte';

	/** 답변 본문 블록. AssistantMessage와 달리 첫 문단 요약(굵은 첫 줄) 없이 블록만 본다. 합성 값이다 */
	const blocksOf = (lines: string[], streaming = false) =>
		parseMarkdown(lines.join('\n'), { streaming });

	const { Story } = defineMeta({
		title: 'Features/coaching/MarkdownBlocks',
		component: MarkdownBlocks,
		tags: ['autodocs'],
		parameters: {
			docs: {
				description: {
					component:
						'AI 답변 Markdown 블록(문단·목록·표)을 그린다. 목록 안 목록은 자기 자신을 다시 불러 그린다. 글자는 Svelte가 이스케이프해 HTML로 해석되지 않는다.'
				}
			}
		}
	});
</script>

<Story
	name="문단과 굵게·기울임"
	exportName="Paragraphs"
	args={{
		blocks: blocksOf([
			'지난주 평균 수면은 **6시간 33분**이에요.',
			'',
			'이전 7일보다 *18분* 늘었고, 잠든 시각은 비슷했어요.'
		])
	}}
/>

<Story
	name="점 목록 · 번호 목록"
	exportName="Lists"
	args={{
		blocks: blocksOf([
			'- 잠든 시각은 비슷했어요',
			'- 깨는 시각이 **조금 늦어졌어요**',
			'',
			'3. 세 번째부터 시작하는 번호',
			'4. 시작 번호를 그대로 따른다'
		])
	}}
/>

<Story
	name="목록 안 목록"
	exportName="NestedList"
	args={{
		blocks: blocksOf([
			'- 수면',
			'  - 평균 6시간 33분',
			'  - 기록 없는 날 2일',
			'- 걸음',
			'  1. 평일 평균 8,120걸음',
			'  2. 주말 평균 5,394걸음'
		])
	}}
/>

<Story
	name="문단·목록·표 섞임"
	exportName="Mixed"
	args={{
		blocks: blocksOf([
			'최근 3일 기록이에요.',
			'',
			'| 날짜 | 걸음 | 수면 |',
			'| --- | --- | --- |',
			'| 10월 24일 (토) | 11,650걸음 | 7시간 12분 |',
			'| 10월 25일 (일) | 5,394걸음 | 8시간 5분 |',
			'| 10월 26일 (월) | 9,913걸음 | 6시간 30분 |',
			'',
			'- 일요일만 걸음이 크게 줄었어요',
			'- 수면은 일요일이 가장 길었어요'
		])
	}}
/>

<!-- 스트리밍 중: 덜 들어온 표 줄은 숨기고 들어온 행까지만 그린다 -->
<Story
	name="작성 중 · 덜 들어온 표 줄"
	exportName="Streaming"
	args={{
		blocks: blocksOf(
			[
				'| 날짜 | 걸음 |',
				'| --- | --- |',
				'| 10월 24일 (토) | 11,650걸음 |',
				'| 10월 25일 (일) | 5,3'
			],
			true
		)
	}}
/>
