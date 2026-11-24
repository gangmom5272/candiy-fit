<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HealthSummaryChart from './HealthSummaryChart.svelte';
	import { healthSummaries, type HealthSummary } from '../summary';
	const examples = healthSummaries.map((summary): HealthSummary => ({
		...summary,
		days: summary.values.map((value, index) => ({
			date: `2026-10-${19 + index}`,
			value,
			missing: value === null,
			reason: value === null ? 'NO_RECORD' : null,
			isInProgress: false
		}))
	}));
	const { Story } = defineMeta({
		title: 'Features/health/HealthSummaryChart',
		component: HealthSummaryChart,
		tags: ['autodocs'],
		args: { interactive: true },
		parameters: {
			docs: {
				description: {
					component:
						'합성 데이터 예시. 정상값과 유효한 0은 hover·tap·focus로 KST 날짜와 일별 값을 확인한다. 선택 테두리·툴팁·두 영역 사이의 좁은 연결 구간 위에서는 표시를 유지하며, 키보드 포커스가 남아 있으면 마우스가 벗어나도 유지한다. 방향키·Home·End로 이동하고 Escape로 닫는다. Escape 후 같은 지점의 작은 마우스 이동으로는 다시 열리지 않으며, 벗어났다가 재진입하거나 다른 지점을 선택하면 다시 열린다. 회색 결측 지점은 선택하지 않으며 날짜별 사유는 접근성 설명에 보존한다.'
				}
			}
		}
	});
</script>

<Story name="걸음 · 결측" exportName="Steps" args={{ summary: examples[0] }} />
<Story name="운동 · 유효한 0" exportName="Exercise" args={{ summary: examples[1] }} />
<Story name="수면 · 시간과 분" exportName="Sleep" args={{ summary: examples[2] }} />
<Story name="심박 · 일별 평균" exportName="Heart" args={{ summary: examples[3] }} />
<Story
	name="전체 결측"
	exportName="Missing"
	args={{
		summary: {
			...examples[0],
			values: [null],
			days: [
				{ date: '2026-10-19', value: null, missing: true, reason: 'UNSYNCED', isInProgress: false }
			],
			average: null,
			value: '기록 없음'
		}
	}}
/>
