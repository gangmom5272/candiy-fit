<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import CoachingScreenExample from '#lib/testing/CoachingScreenExample.svelte';
	import { conversationFixtures, conversations, evidenceCases } from '../fixtures';
	import type { CoachingMessage } from '../types';

	const [sleepAndSteps, generating, boundary, sleepOnly] = conversationFixtures;

	/** 픽스처 대화의 마지막 답변만 바꾼다. 합성 값이다 (S15P31A403-247) */
	const withLastAnswer = (
		fixture: (typeof conversationFixtures)[number],
		patch: Partial<CoachingMessage>,
		question?: string
	) => ({
		...fixture,
		messages: fixture.messages.map((message, index) => {
			if (index === fixture.messages.length - 1) return { ...message, ...patch };
			if (question && message.role === 'user') return { ...message, content: question };
			return message;
		})
	});

	// 247 테스트에서 "차트가 안 나온다"던 하루짜리 질문. 근거 창에 차트 대신 이유가 한 줄 보인다
	const oneDay = withLastAnswer(
		sleepAndSteps,
		{
			content: '어제는 8,240걸음 걸었어요.\n그제보다 1,310걸음 더 걸었어요.',
			evidence: evidenceCases.singleDay
		},
		'어제 몇 걸음 걸었어?'
	);
	const allZero = withLastAnswer(
		sleepAndSteps,
		{
			content: '지난주에는 운동 기록이 0분이었어요.\n가벼운 산책부터 시작해 보세요.',
			evidence: evidenceCases.allZeroWeek
		},
		'지난주 운동 얼마나 했어?'
	);
	const timedOut = withLastAnswer(sleepOnly, {
		content: '최근 7일 평균 수면은',
		requestStatus: 'failed',
		evidence: []
	});

	const { Story } = defineMeta({
		title: 'Features/coaching/화면 조합',
		component: CoachingScreenExample,
		args: { items: conversations },
		parameters: {
			layout: 'fullscreen',
			docs: {
				description: {
					component:
						'코칭 대화 화면 전체를 픽스처로 조합한 모습. 실제 화면은 소켓·API를 써서 Storybook에 못 띄우므로 같은 배치를 `lib/testing/CoachingScreenExample.svelte`에 옮겼다. 폭에 따른 모습은 툴바의 Viewport로 바꿔 본다(lg 1024px 미만은 목록이 서랍).'
				}
			}
		}
	});
</script>

<Story
	name="답변 완료 · 근거와 추천 질문"
	exportName="Completed"
	args={{ fixture: sleepAndSteps }}
/>

<Story
	name="답변 작성 중"
	exportName="Generating"
	args={{
		fixture: generating,
		isGenerating: true,
		lastAnswer: { progressText: '답변을 작성하고 있어요' }
	}}
/>

<Story
	name="하루 기록 근거 (차트 대신 안내)"
	exportName="OneDayEvidence"
	args={{ fixture: oneDay }}
/>

<Story name="모두 0인 주 근거" exportName="AllZeroEvidence" args={{ fixture: allZero }} />

<Story
	name="답변을 가져오지 못함 (다시 시도)"
	exportName="TimedOut"
	args={{
		fixture: timedOut,
		lastAnswer: {
			errorMessage: '답변을 가져오지 못했습니다. 다시 시도해 주세요.',
			retryable: true
		}
	}}
/>

<Story name="진단·처방 질문 경계 안내" exportName="Boundary" args={{ fixture: boundary }} />
