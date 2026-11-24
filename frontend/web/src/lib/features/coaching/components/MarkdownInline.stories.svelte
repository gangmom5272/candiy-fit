<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import type { ComponentProps } from 'svelte';
	import type { MarkdownInline as InlineNode } from '../markdown';
	import MarkdownInline from './MarkdownInline.svelte';

	// 답변 한 줄 안의 글자·굵게·기울임·줄바꿈·입력 표시. 문단·목록(MarkdownBlocks)과 요약 첫 줄(AssistantMessage)이 쓴다
	const text = (value: string): InlineNode => ({ type: 'text', text: value });
	const strong = (...children: InlineNode[]): InlineNode => ({ type: 'strong', children });
	const em = (...children: InlineNode[]): InlineNode => ({ type: 'em', children });

	const { Story } = defineMeta({
		title: 'Features/coaching/MarkdownInline',
		component: MarkdownInline,
		tags: ['autodocs'],
		parameters: {
			docs: {
				description: {
					component:
						'인라인 노드 배열을 그린다. 글자는 Svelte가 이스케이프해 HTML로 해석되지 않는다. 굵게·기울임 안에 다시 자기 자신을 불러 중첩을 그린다.'
				}
			}
		},
		render: template
	});
</script>

{#snippet template(args: ComponentProps<typeof MarkdownInline>)}
	<p class="max-w-[40rem] text-base leading-[1.7]">
		<MarkdownInline {...args} />
	</p>
{/snippet}

<Story
	name="굵게·기울임"
	exportName="Emphasis"
	args={{
		nodes: [
			text('지난주 평균 수면은 '),
			strong(text('6시간 33분')),
			text('이에요. 이전 7일보다 '),
			em(text('18분')),
			text(' 늘었어요.')
		]
	}}
/>

<Story
	name="중첩 (굵게 안 기울임)"
	exportName="Nested"
	args={{ nodes: [strong(text('잠든 시각은 '), em(text('비슷')), text('했어요'))] }}
/>

<Story
	name="줄바꿈"
	exportName="LineBreak"
	args={{ nodes: [text('첫 줄이에요.'), { type: 'br' }, text('둘째 줄이에요.')] }}
/>

<!-- 스트리밍 중에는 끝에 깜빡이는 입력 표시를 붙인다(움직임 줄이기 설정이면 멈춤) -->
<Story
	name="스트리밍 입력 표시"
	exportName="Cursor"
	args={{ nodes: [text('답변을 쓰고 있어'), { type: 'cursor' }] }}
/>

<!-- HTML처럼 보이는 글자도 그대로 글자로만 보인다 -->
<Story
	name="HTML 글자 그대로"
	exportName="Escaped"
	args={{ nodes: [text('<b>굵게</b> 같은 태그는 글자 그대로 보여요.')] }}
/>
