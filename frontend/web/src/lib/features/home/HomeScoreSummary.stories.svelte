<script module lang="ts">
	import { defineMeta } from '@storybook/addon-svelte-csf';
	import HomeScoreSummary from './HomeScoreSummary.svelte';
	import { homeScoreStatuses, type LifestyleScore } from './home-api';

	const scoreRecord: LifestyleScore = {
		date: '2026-11-17',
		isInProgress: true,
		reason: null,
		total: { score: 71, missingAreas: [] },
		areas: {
			activity: {
				score: 85,
				grade: 1,
				steps: { score: 60, value: 7200 },
				exercise: { score: 25, minutes: 19 }
			},
			sleep: { score: 58, grade: 3, sleepMinutes: 366 },
			heart: {
				score: 70,
				grade: 2,
				sleepHrAvg: 64.5,
				baselineHrAvg: 58,
				baselineNights: 20,
				checkWear: false
			}
		},
		computedAt: '2026-11-17T03:00:00Z'
	};

	const { Story } = defineMeta({
		title: 'Features/home/HomeScoreSummary',
		component: HomeScoreSummary,
		args: {
			score: scoreRecord.total.score,
			statuses: homeScoreStatuses(scoreRecord, false, null)
		},
		parameters: { layout: 'padded' }
	});
</script>

<Story name="Default" />
<Story name="DetailsOpen" args={{ open: true }} />
<Story
	name="NoHeartBaseline"
	args={{
		open: true,
		score: 72,
		statuses: homeScoreStatuses(
			{
				...scoreRecord,
				total: { score: 72, missingAreas: ['heart'] },
				areas: {
					...scoreRecord.areas,
					heart: {
						...scoreRecord.areas.heart,
						score: null,
						grade: null,
						baselineHrAvg: null,
						baselineNights: 0
					}
				}
			},
			false,
			null
		)
	}}
/>
<Story
	name="NoRecords"
	args={{ open: true, score: null, statuses: homeScoreStatuses(null, false, null) }}
/>
<Story
	name="ScoresWithoutMeasurements"
	args={{
		open: true,
		statuses: homeScoreStatuses(
			{
				...scoreRecord,
				areas: {
					activity: {
						...scoreRecord.areas.activity,
						steps: { value: null, score: null },
						exercise: { minutes: null, score: null }
					},
					sleep: { ...scoreRecord.areas.sleep, sleepMinutes: null },
					heart: {
						...scoreRecord.areas.heart,
						sleepHrAvg: null,
						baselineHrAvg: null,
						baselineNights: null,
						checkWear: null
					}
				}
			},
			false,
			null
		)
	}}
/>
<Story
	name="LongDescription"
	args={{
		open: true,
		// 실제 문구를 늘려 패널의 줄바꿈·스크롤만 확인하는 표시용 시나리오.
		statuses: homeScoreStatuses(scoreRecord, false, null).map((status, index) => ({
			...status,
			description: index === 0 ? `${status.description} `.repeat(4) : status.description
		}))
	}}
/>
