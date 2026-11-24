<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { fn } from 'storybook/test';
	import { conversationFixtures } from '../fixtures';
	import AssistantMessage from './AssistantMessage.svelte';

	const [sleepAndSteps, generating, boundary, , noEvidence] = conversationFixtures;
	const answerOf = (fixture: (typeof conversationFixtures)[number]) => fixture.messages[1];
	/** Markdown 답변 모양 확인용 합성 답변 (S15P31A403-191) */
	const markdownAnswer = (
		content: string,
		requestStatus: 'completed' | 'generating' = 'completed'
	) => ({ ...answerOf(noEvidence), content, requestStatus });
	/** 표 모양 확인용 합성 값 (S15P31A403-236). AI 팀과 맞춘 표 형식 예시 */
	const weekTable = [
		'최근 7일 활동량이에요.',
		'',
		'| 날짜 | 걸음 | 운동 | 수면 |',
		'| --- | --- | --- | --- |',
		'| 10월 24일 (토) | 11,650걸음 | 43분 | 7시간 12분 |',
		'| 10월 25일 (일) | 5,394걸음 | 0분 | 8시간 5분 |',
		'| 10월 26일 (월) | 9,913걸음 | 40분 | 6시간 30분 |',
		'',
		'일요일만 걸음이 크게 줄었어요.'
	].join('\n');

	const { Story } = defineMeta({
		title: 'Features/coaching/AssistantMessage',
		component: AssistantMessage,
		tags: ['autodocs'],
		args: { onquestion: fn(), onopenevidence: fn() },
		parameters: {
			docs: {
				description: {
					component:
						'AI 답변 한 건. 경계 안내(`extras.boundary`)와 추천 질문(`extras.followUpQuestions`)은 API 명세에 아직 없어 더미 데이터로만 보여 준다.'
				}
			}
		}
	});
</script>

<Story name="답변 작성 중" exportName="Generating" args={{ message: answerOf(generating) }} />

<Story
	name="답변 완료 · 근거와 추천 질문"
	exportName="CompletedWithEvidence"
	args={{
		message: answerOf(sleepAndSteps),
		extras: sleepAndSteps.extras[answerOf(sleepAndSteps).id]
	}}
/>

<Story
	name="답변 완료 · 근거 없음"
	exportName="CompletedWithoutEvidence"
	args={{ message: answerOf(noEvidence) }}
/>

<Story
	name="Markdown · 문단·목록·굵게"
	exportName="MarkdownBasic"
	args={{
		message: markdownAnswer(
			[
				'지난주보다 수면이 조금 늘었어요.',
				'평균 수면은 **6시간 33분**으로 이전 7일보다 18분 길어요.',
				'',
				'- 잠든 시각은 비슷했어요',
				'- 깨는 시각이 **조금 늦어졌어요**',
				'',
				'주말에 늦게 일어난 날이 평균을 끌어올렸을 수 있어요.'
			].join('\n')
		)
	}}
/>

<Story
	name="Markdown · ### 소제목으로 나눈 답변"
	exportName="MarkdownSubheadings"
	args={{
		message: markdownAnswer(
			[
				'지난주는 수면이 조금 늘고 걸음은 비슷했어요. 기록이 빠진 날이 하루 있어 평균은 6일 기준이에요.',
				'',
				'### 수면',
				'평균 수면은 6시간 33분으로 이전 7일보다 18분 길어요.',
				'',
				'- 잠든 시각은 비슷했어요',
				'- 깨는 시각이 조금 늦어졌어요',
				'',
				'### 걸음',
				'하루 평균 8,120걸음으로 이전 7일과 비슷해요.',
				'주말에 걸음이 줄어드는 흐름은 그대로예요.'
			].join('\n')
		)
	}}
/>

<Story
	name="Markdown · 소제목 작성 중 (기호만 온 줄은 숨김)"
	exportName="MarkdownSubheadingStreaming"
	args={{ message: markdownAnswer('지난주 기록을 정리해 볼게요.\n\n### ', 'generating') }}
/>

<Story
	name="Markdown · 목록으로 시작"
	exportName="MarkdownStartsWithList"
	args={{
		message: markdownAnswer(
			'1. 수면 시간은 늘었어요\n2. 걸음 수는 비슷해요\n3. 심박은 안정적이에요'
		)
	}}
/>

<Story
	name="Markdown · 표 (여러 날 비교)"
	exportName="MarkdownTable"
	args={{ message: markdownAnswer(weekTable) }}
/>

<Story
	name="Markdown · 표로 시작"
	exportName="MarkdownStartsWithTable"
	args={{ message: markdownAnswer(weekTable.split('\n').slice(2).join('\n')) }}
/>

<Story
	name="Markdown · 넓은 표 (가로로 밀어 보기)"
	exportName="MarkdownWideTable"
	args={{
		message: markdownAnswer(
			[
				'지표별로 지난 두 주를 비교했어요.',
				'',
				'| 지표 | 10월 13일 주 | 10월 20일 주 | 차이 | 평소 범위 | 메모 |',
				'| --- | --- | --- | --- | --- | --- |',
				'| 평균 걸음 | 8,120걸음 | 9,430걸음 | +1,310걸음 | 7,000~10,000걸음 | 주말 산책이 늘었어요 |',
				'| 평균 수면 | 6시간 40분 | 6시간 55분 | +15분 | 6시간 30분~7시간 30분 | 잠든 시각은 비슷해요 |',
				'| 운동 시간 | 120분 | 165분 | +45분 | 150분 | 목표에 가까워졌어요 |'
			].join('\n')
		)
	}}
/>

<Story
	name="Markdown · 표 작성 중 (덜 들어온 행은 숨김)"
	exportName="MarkdownTableStreaming"
	args={{
		message: markdownAnswer(
			weekTable.split('\n').slice(0, 5).join('\n') + '\n| 10월 25일 (일) | 5,3',
			'generating'
		)
	}}
/>

<Story
	name="Markdown · 쓰지 않기로 한 문법"
	exportName="MarkdownUnsupported"
	args={{
		message: markdownAnswer(
			[
				'## 제목은 굵은 글로만',
				'[링크](https://example.com)는 글자만 남아요.',
				'',
				'<b>HTML</b>은 글자 그대로 보여요.'
			].join('\n')
		)
	}}
/>

<Story
	name="Markdown · 작성 중 (굵게 미완성)"
	exportName="MarkdownStreaming"
	args={{ message: markdownAnswer('정리해 볼게요.\n평균 수면은 **6시간', 'generating') }}
/>

<!-- 상태 조회를 다 써도 생성 중에 머문 요청(247). 부분 답변은 지우지 않고 다시 묻기를 연다 -->
<Story
	name="실패 · 답변을 가져오지 못함 (다시 시도)"
	exportName="FailedTimeout"
	args={{
		message: { ...markdownAnswer('지난주 평균 수면은 6시간'), requestStatus: 'failed' },
		errorMessage: '답변을 가져오지 못했습니다. 다시 시도해 주세요.',
		onretry: fn()
	}}
/>

<Story
	name="실패 · 다시 묻기 없음"
	exportName="FailedNotRetryable"
	args={{
		message: { ...markdownAnswer(''), requestStatus: 'failed' },
		errorMessage: '건강데이터 조회 권한을 확인해 주세요.'
	}}
/>

<Story
	name="진단·처방 질문 경계 안내"
	exportName="BoundaryNotice"
	args={{ message: answerOf(boundary), extras: boundary.extras[answerOf(boundary).id] }}
/>
