<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import CoachingErrorNotice from './CoachingErrorNotice.svelte';

	// 코칭 화면의 오류 상자. 답변 실패·지난 대화 불러오기 실패·공통 오류(명세 7.3)가 같은 모양을 쓴다
	const { Story } = defineMeta({
		title: 'Features/coaching/CoachingErrorNotice',
		component: CoachingErrorNotice,
		tags: ['autodocs'],
		args: { message: '답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.' }
	});
</script>

<Story
	name="답변 실패 · 다시 질문하기"
	exportName="AnswerFailed"
	args={{ onretry: fn(), retryLabel: '다시 질문하기' }}
/>

<Story
	name="지난 대화 불러오기 실패"
	exportName="HistoryFailed"
	args={{
		message: '이전 대화를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.',
		onretry: fn(),
		retryLabel: '다시 불러오기'
	}}
/>

<Story
	name="공통 오류 (버튼 없음)"
	exportName="CommonError"
	args={{ message: '코칭 서버와 연결이 끊겼어요. 잠시 후 다시 질문해 주세요.' }}
/>
