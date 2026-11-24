<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import QuestionInput from './QuestionInput.svelte';

	const { Story } = defineMeta({
		title: 'Features/coaching/QuestionInput',
		component: QuestionInput,
		tags: ['autodocs'],
		args: { onsubmit: fn() },
		argTypes: {
			size: { control: 'inline-radio', options: ['default', 'large'] }
		}
	});
</script>

<Story name="대화 화면" exportName="Default" args={{ size: 'default' }} />

<Story name="첫 화면" exportName="Large" args={{ size: 'large' }} />

<Story name="입력한 질문" exportName="WithValue" args={{ value: '지난주보다 얼마나 더 걸었어?' }} />

<Story name="답변 작성 중" exportName="Generating" args={{ isGenerating: true }} />

<Story name="2,000자 초과" exportName="TooLong" args={{ value: '가'.repeat(2001) }} />

<!-- 휴대폰 대화 화면 폭(375px 화면에서 입력 상자 약 260px): 긴 안내 문구 대신 짧은 문구 (S15P31A403-240) -->
<Story name="좁은 칸 · 짧은 안내 문구" exportName="Narrow" asChild>
	<div style:width="16rem">
		<QuestionInput onsubmit={fn()} />
	</div>
</Story>

<!-- 320px 화면: 짧은 문구도 안 들어가 끝을 "…"으로 줄인다 -->
<Story name="아주 좁은 칸 · 말줄임" exportName="VeryNarrow" asChild>
	<div style:width="12rem">
		<QuestionInput onsubmit={fn()} />
	</div>
</Story>
