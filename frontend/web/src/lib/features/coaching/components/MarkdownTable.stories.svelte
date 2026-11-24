<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { parseMarkdown, type MarkdownBlock } from '../markdown';
	import MarkdownTable from './MarkdownTable.svelte';

	/** 표 Markdown → 표 블록. 합성 값이다 (S15P31A403-236) */
	const tableOf = (markdown: string) =>
		parseMarkdown(markdown).find(
			(block): block is Extract<MarkdownBlock, { type: 'table' }> => block.type === 'table'
		)!;

	const week = tableOf(
		[
			'| 날짜 | 걸음 | 운동 | 수면 |',
			'| --- | --- | --- | --- |',
			'| 10월 24일 (토) | 11,650걸음 | 43분 | 7시간 12분 |',
			'| 10월 25일 (일) | 5,394걸음 | 0분 | 8시간 5분 |',
			'| 10월 26일 (월) | 9,913걸음 | 40분 | 6시간 30분 |'
		].join('\n')
	);
	const aligned = tableOf(
		[
			'| 지표 | 지난주 | 이번 주 | 차이 |',
			'| :--- | ---: | ---: | :---: |',
			'| 평균 걸음 | 8,120걸음 | **9,430걸음** | +1,310걸음 |',
			'| 평균 수면 | 6시간 40분 | 6시간 55분 | +15분 |'
		].join('\n')
	);

	const { Story } = defineMeta({
		title: 'Features/coaching/MarkdownTable',
		component: MarkdownTable,
		parameters: {
			docs: {
				description: {
					component:
						'AI 답변의 비교 표. 첫 열은 항목 이름(행 머리)이다. 표가 상자보다 넓으면 상자만 가로로 밀리고, 더 밀 수 있는 쪽 가장자리가 흐려진다. 상자는 키보드로도 밀 수 있다(Tab → 방향키).'
				}
			}
		}
	});
</script>

<Story name="기본 (넓은 자리)" exportName="Default" args={{ table: week }} />

<!-- 휴대폰 답변 본문 폭(약 240px)에서 열 경계가 잘려 오른쪽이 흐려진다 -->
<Story name="좁은 자리 · 가로로 밀기" exportName="Narrow" asChild>
	<div style:width="15rem">
		<MarkdownTable table={week} />
	</div>
</Story>

<Story name="정렬·칸 안 굵게" exportName="AlignedAndBold" args={{ table: aligned }} />

<!-- 스트리밍 중 구분선까지 들어오고 행은 아직 없을 때 -->
<Story
	name="머리줄만 (행 들어오기 전)"
	exportName="HeaderOnly"
	args={{ table: { ...week, rows: [] } }}
/>
