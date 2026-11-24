<script lang="ts">
	type Props = { content: string };

	let { content }: Props = $props();
</script>

<!--
	질문은 말풍선, 답변은 말풍선 없는 넓은 본문으로 구분한다(앱 AI 코치 탭과 같은 기준).
	질문 글자는 답변 본문과 같은 16px 이다(멘토링 피드백, S15P31A403-315). 둘은 크기가 아니라 위치·말풍선으로 구분하고,
	답변 첫 줄(17px 굵게)만 요약이라 한 단계 크다. 말풍선은 글 길이에 맞춰 줄어든다.
-->
<div class="flex justify-end">
	<p
		class="max-w-[min(78%,40rem)] rounded-2xl bg-primary-soft px-4 py-3 text-base leading-relaxed wrap-break-word break-keep whitespace-pre-wrap text-text-primary"
	>
		{content}
	</p>
</div>
