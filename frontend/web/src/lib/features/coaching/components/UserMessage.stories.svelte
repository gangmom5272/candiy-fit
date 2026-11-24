<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import UserMessage from './UserMessage.svelte';

	const { Story } = defineMeta({
		title: 'Features/coaching/UserMessage',
		component: UserMessage,
		tags: ['autodocs'],
		parameters: {
			docs: {
				description: {
					component:
						'사용자 질문 말풍선. 오른쪽에 붙고 글 길이에 맞춰 줄어든다. 글자는 답변 본문과 같은 16px 이고 위치·말풍선으로 구분한다. 입력한 줄바꿈은 그대로 보이고 한국어는 단어 단위로 줄을 바꾼다.'
				}
			}
		}
	});
</script>

<Story name="짧은 질문" exportName="Short" args={{ content: '어제 몇 걸음 걸었어?' }} />

<Story
	name="긴 질문 (단어 단위 줄바꿈)"
	exportName="Long"
	args={{
		content:
			'요즘 잠드는 시간이 들쭉날쭉한데 지난주랑 비교해서 수면 시간이 얼마나 달라졌는지, 깊은 잠도 줄었는지 같이 알려 줄 수 있어?'
	}}
/>

<Story
	name="여러 줄로 입력한 질문"
	exportName="Multiline"
	args={{ content: '세 가지만 알려 줘.\n1. 지난주 평균 수면\n2. 걸음 수 변화\n3. 운동한 날' }}
/>

<!-- 띄어쓰기 없는 긴 글자(주소 등)도 말풍선 밖으로 넘치지 않는다 -->
<Story
	name="띄어쓰기 없는 긴 글자"
	exportName="NoSpaces"
	args={{ content: 'https://example.com/very/long/path/without/any/spaces/'.repeat(3) }}
/>

<Story name="좁은 화면 (320px)" exportName="Narrow" asChild>
	<div style:width="17.5rem">
		<UserMessage content="지난주보다 이번 주에 운동을 더 많이 했어?" />
	</div>
</Story>
