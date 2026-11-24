<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import {
		evidenceCases,
		kindEvidenceJson,
		sleepAndStepsEvidence,
		sleepNightsSpo2,
		sleepSpo2Month,
		spo2Month
	} from '../fixtures';
	import { parseEvidenceList } from '../parse';
	import EvidenceDialog from './EvidenceDialog.svelte';

	/** 서버 JSON 을 실제 파서로 읽은 근거. 모르는 종류(운동 구간)는 빠지고 두 개만 남는다 */
	const kindEvidence = parseEvidenceList({ items: Object.values(kindEvidenceJson) }, 'items');

	// 모달이라 문서(autodocs) 페이지에 여러 개를 동시에 띄우지 않는다. 닫은 뒤에는 Controls의 open으로 다시 연다.
	const { Story } = defineMeta({
		title: 'Features/coaching/EvidenceDialog',
		component: EvidenceDialog
	});
</script>

<Story
	name="수면·활동 비교"
	exportName="SleepAndSteps"
	args={{ evidence: sleepAndStepsEvidence, open: true }}
/>

<Story
	name="주별로 여섯 번 나눠 조회"
	exportName="WeeklySplit"
	args={{ evidence: evidenceCases.weeklySplit, open: true }}
/>

<Story
	name="31일 제한으로 둘로 나눠 조회"
	exportName="LongSplit"
	args={{ evidence: evidenceCases.longSplit, open: true }}
/>

<Story
	name="지표 하나 · 비교 기간 없음"
	exportName="SingleMetric"
	args={{ evidence: sleepAndStepsEvidence.slice(0, 1), open: true }}
/>

<Story
	name="기록 부족 · 7일 중 1일"
	exportName="OneDayOfSeven"
	args={{ evidence: evidenceCases.oneDayOfSeven, open: true }}
/>

<Story
	name="기록 부족 · 기록 없음"
	exportName="NoRecords"
	args={{ evidence: evidenceCases.noRecords, open: true }}
/>

<Story
	name="기록 부족 · 하루 기간 비교"
	exportName="SingleDay"
	args={{ evidence: evidenceCases.singleDay, open: true }}
/>

<Story
	name="기록 부족 · 비교 기간 1일"
	exportName="SparseComparison"
	args={{ evidence: evidenceCases.sparseComparison, open: true }}
/>

<Story
	name="기록 부족 · 연동 전 날짜"
	exportName="BeforeLink"
	args={{ evidence: evidenceCases.beforeLink, open: true }}
/>

<Story
	name="오늘 하루 · 수집 중"
	exportName="TodayOnly"
	args={{ evidence: evidenceCases.todayOnly, open: true }}
/>

<Story
	name="기록 부족 · 오늘만 수집 중"
	exportName="TodayOnlyWeek"
	args={{ evidence: evidenceCases.todayOnlyWeek, open: true }}
/>

<Story
	name="7일 · 오늘 수집 중 (날짜별 기록 줄맞춤)"
	exportName="WeekWithToday"
	args={{ evidence: evidenceCases.weekWithToday, open: true }}
/>

<Story
	name="기록이 모두 0"
	exportName="AllZeroWeek"
	args={{ evidence: evidenceCases.allZeroWeek, open: true }}
/>

<Story
	name="심박 눈금"
	exportName="HeartWeek"
	args={{ evidence: evidenceCases.heartWeek, open: true }}
/>

<Story
	name="기록 과다 · 31일 · 오늘 진행 중"
	exportName="Month"
	args={{ evidence: evidenceCases.month, open: true }}
/>

<Story
	name="기록 과다 · 90일"
	exportName="Quarter"
	args={{ evidence: evidenceCases.quarter, open: true }}
/>

<!-- 248: 답변이 하루 평균 말고 측정 최고·수면 단계·운동 기록을 말할 때 근거에서 찾을 수 있다 -->
<Story
	name="상세 · 심박 측정 최저·최고 (하루)"
	exportName="HeartMeasured"
	args={{ evidence: evidenceCases.heartOneDay, open: true }}
/>

<!-- 이전 30일·최근 30일과 두 달 전체를 함께 조회한 경우. 겹치는 두 달 전체는 머리말에 따로 적고 두 기간을 비교한다 -->
<Story
	name="상세 · 한 달 전과 비교(겹치는 기간 포함)"
	exportName="SleepMonthCompareOverlap"
	args={{ evidence: evidenceCases.sleepMonthCompareOverlap, open: true }}
/>

<!-- 31일을 넘어 차트가 주 단위로 묶이는 수면. 주 막대 아래에 기간을 적고, 날짜별 수면 단계는 최근 7일부터 넘겨 본다 -->
<Story
	name="상세 · 수면 35일(주 단위 차트)"
	exportName="SleepFiveWeeks"
	args={{ evidence: evidenceCases.sleepFiveWeeks, open: true }}
/>

<Story
	name="상세 · 수면 단계 평균"
	exportName="SleepStages"
	args={{ evidence: evidenceCases.sleepStagesWeek, open: true }}
/>

<Story
	name="상세 · 운동 기록 30일"
	exportName="ExerciseSessions"
	args={{ evidence: evidenceCases.exerciseSessionsMonth, open: true }}
/>

<!-- S15P31A403-430 31일씩 여섯 번 나눠 조회한 6개월 운동. 합친 기간도 날짜별 기록은 달력이고 평균은 화면에서 낸다 -->
<Story
	name="운동 · 6개월 나눠 조회 (달력)"
	exportName="ExerciseSixMonths"
	args={{ evidence: evidenceCases.exerciseSixMonths, open: true }}
/>

<!-- S15P31A403-375 운동 시간에 횟수·칼로리·거리가 함께 온 근거. 0은 값, 빈 칸은 이유, 오늘은 수집 중 -->
<Story
	name="상세 · 운동 횟수·칼로리·거리"
	exportName="ExerciseActivity"
	args={{ evidence: evidenceCases.exerciseActivityWeek, open: true }}
/>

<!-- S15P31A403-311 신체 지표. 합계 없음, 빈 날은 비워 두고, 키는 cm 로 보인다 -->
<Story
	name="신체 지표 · 체중·체지방률·기초대사량·키 7일"
	exportName="BodyWeek"
	args={{ evidence: evidenceCases.bodyWeek, open: true }}
/>

<Story
	name="신체 지표 · 오늘 체중만"
	exportName="BodyTodayOnly"
	args={{ evidence: evidenceCases.bodyTodayOnly, open: true }}
/>

<Story
	name="신체 지표 · 체중 90일 (주 마지막 측정값)"
	exportName="BodyQuarter"
	args={{ evidence: evidenceCases.bodyQuarter, open: true }}
/>

<!-- S15P31A403-312 모양이 다른 근거. 백엔드 반환 예시(합성) 그대로. 운동 구간(exercise_intervals)은 아직 모양이 없어 숨는다 -->
<Story
	name="근거 종류 · 수면 기록·시간대 걸음"
	exportName="KindEvidence"
	args={{ evidence: kindEvidence, open: true }}
/>

<Story
	name="근거 종류 · 수면 기록과 걸음 지표 함께"
	exportName="KindWithMetric"
	args={{ evidence: [...sleepAndStepsEvidence.slice(2, 3), ...kindEvidence], open: true }}
/>

<!-- 날짜별 산소포화도 90% 미만 네 가지(측정 없음 / 없음 / 횟수만 / 시각 목록) -->
<Story
	name="수면 · 날짜별 산소포화도"
	exportName="SleepSpo2"
	args={{ evidence: sleepNightsSpo2, open: true }}
/>

<!-- S15P31A403-388 밤이 많으면 평균·최저·최고 요약을 먼저 보여 주고 날짜별 목록은 접는다 -->
<Story
	name="수면 · 한 달 산소포화도 요약"
	exportName="SleepSpo2Month"
	args={{ evidence: sleepSpo2Month, open: true }}
/>

<!-- S15P31A403-388 산소포화도는 90에서 시작하는 꺾은선. 요약은 한 상자에 평균·최저·최고 -->
<Story
	name="산소포화도 · 한 달 꺾은선"
	exportName="Spo2Month"
	args={{ evidence: spo2Month, open: true }}
/>

<!-- 시간대 걸음 기간 합계: 모든 날 기록이 있으면 오늘(진행 중)까지 더한 값이 온다 -->
<Story
	name="근거 종류 · 시간대 걸음 합계 있음"
	exportName="HourlyComplete"
	args={{
		evidence: parseEvidenceList({ items: [kindEvidenceJson.hourlyStepsComplete] }, 'items'),
		open: true
	}}
/>

<!-- S15P31A403-404 화면에는 있는데 스토리에 없던 경우를 채운다 -->
<Story
	name="비교 기간에 기록 없음"
	exportName="ComparisonEmpty"
	args={{ evidence: evidenceCases.comparisonEmpty, open: true }}
/>

<Story
	name="산소포화도 · 비교 기간 있음"
	exportName="Spo2Compare"
	args={{ evidence: evidenceCases.spo2Compare, open: true }}
/>

<Story
	name="상세 · 심박 측정 최저·최고 (7일)"
	exportName="HeartWeekMeasured"
	args={{ evidence: evidenceCases.heartWeekMeasured, open: true }}
/>

<Story
	name="상세 · 수면 단계 (하루)"
	exportName="SleepStagesOneDay"
	args={{ evidence: evidenceCases.sleepStagesOneDay, open: true }}
/>

<Story
	name="수면 · 한 달 산소포화도 요약 (90% 미만 없음)"
	exportName="SleepSpo2MonthClean"
	args={{ evidence: evidenceCases.sleepSpo2MonthClean, open: true }}
/>

<Story
	name="상세 · 운동 횟수·칼로리·거리 14일 (표 접힘)"
	exportName="ExerciseActivityTwoWeeks"
	args={{ evidence: evidenceCases.exerciseActivityTwoWeeks, open: true }}
/>

<Story
	name="상세 · 운동 횟수·칼로리·거리 나눠 조회"
	exportName="ExerciseActivitySplit"
	args={{ evidence: evidenceCases.exerciseActivitySplit, open: true }}
/>

<!-- 최근 8일이 동기화 전인 한 달. 처음 화면은 빈 칸이 아니라 값이 있는 마지막 7일(10.16 ~ 10.22)이다 -->
<Story
	name="기록 부족 · 한 달 중 최근 8일 동기화 전"
	exportName="StepsRecentGap"
	args={{ evidence: evidenceCases.stepsRecentGap, open: true }}
/>

<Story
	name="기록 부족 · 값 없음과 동기화 전"
	exportName="MissingReasons"
	args={{ evidence: evidenceCases.missingReasons, open: true }}
/>
