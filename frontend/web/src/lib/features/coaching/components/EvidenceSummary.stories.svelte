<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import { evidenceCases, kindEvidenceJson, sleepAndStepsEvidence } from '../fixtures';
	import { parseEvidenceList } from '../parse';
	import EvidenceSummary from './EvidenceSummary.svelte';

	const kindEvidence = parseEvidenceList({ items: Object.values(kindEvidenceJson) }, 'items');

	const { Story } = defineMeta({
		title: 'Features/coaching/EvidenceSummary',
		component: EvidenceSummary,
		tags: ['autodocs'],
		parameters: {
			docs: {
				description: {
					component:
						'답변 아래 근거 카드. 지표 옆에 기록 상태(7일 중 n일·기록 없음)를 붙이고, 상세 창에 차트가 없으면 "기록 보기"로 부른다.'
				}
			}
		}
	});
</script>

<Story
	name="수면·활동 비교"
	exportName="SleepAndSteps"
	args={{ evidence: sleepAndStepsEvidence }}
/>

<Story
	name="7일 중 1일"
	exportName="OneDayOfSeven"
	args={{ evidence: evidenceCases.oneDayOfSeven }}
/>

<Story name="기록 없음" exportName="NoRecords" args={{ evidence: evidenceCases.noRecords }} />

<Story name="하루 기간 비교" exportName="SingleDay" args={{ evidence: evidenceCases.singleDay }} />

<Story
	name="오늘 하루 · 수집 중"
	exportName="TodayOnly"
	args={{ evidence: evidenceCases.todayOnly }}
/>

<Story
	name="기록이 모두 0"
	exportName="AllZeroWeek"
	args={{ evidence: evidenceCases.allZeroWeek }}
/>

<Story name="31일 · 오늘 진행 중" exportName="Month" args={{ evidence: evidenceCases.month }} />

<!-- S15P31A403-404 지난 6일은 기록이 없고 오늘 값만 있을 때 카드에 "오늘만 수집 중"이 붙는다 -->
<Story
	name="7일 중 오늘만 수집 중"
	exportName="TodayOnlyWeek"
	args={{ evidence: evidenceCases.todayOnlyWeek }}
/>

<!-- S15P31A403-312 지표 근거 없이 모양이 다른 근거(수면 기록·시간대 걸음)만 와도 카드가 보인다 -->
<Story name="근거 종류만" exportName="KindOnly" args={{ evidence: kindEvidence }} />
