<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import CoachingProgress from './CoachingProgress.svelte';

	// 질문 보내는 중·답변 작성 중처럼 "지금 하고 있는 일"을 알리는 진행 표시. 불러오는 동안 자리를 잡는 스켈레톤과는 역할이 다르다
	const { Story } = defineMeta({
		title: 'Features/coaching/CoachingProgress',
		component: CoachingProgress,
		tags: ['autodocs'],
		args: { text: '답변을 작성하고 있어요' },
		parameters: {
			docs: {
				description: {
					component:
						'세 점이 차례로 깜빡이는 진행 안내. 움직임 줄이기 설정이면 점이 멈춘다(motion-reduce). 코칭 첫 화면의 "질문을 보내고 있어요"와 답변 생성 중의 AssistantMessage가 쓴다.'
				}
			}
		}
	});
</script>

<Story name="답변 작성 중" exportName="Generating" />

<Story
	name="질문 보내는 중 (첫 화면)"
	exportName="Sending"
	args={{ text: '질문을 보내고 있어요' }}
/>

<Story name="기록 확인 중" exportName="Reading" args={{ text: '기록을 확인하고 있어요' }} />
