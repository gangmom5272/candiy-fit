import type { LifestyleScore } from './home-api';
import { formatSleepMinutes } from '#lib/features/health/sleep.js';

// data/pipeline/src/candiy_pipeline/score.py와의 일치 확인은 문구 선택에만 쓴다.
// 점수·등급을 바꾸거나 사용자에게 내부 산식을 노출하지 않는다.
const stepBands = [
	[7000, 60],
	[5000, 48],
	[3000, 30],
	[2000, 15]
] as const;
const sleepAnchors = [
	[3.5, 0],
	[4.5, 20],
	[5.5, 40],
	[6.5, 70],
	[8, 100]
] as const;

function matchesGrade(score: number | null, grade: number | null): boolean {
	if (score === null || grade === null) return false;
	return grade === (score >= 80 ? 1 : score >= 65 ? 2 : score >= 50 ? 3 : 4);
}

function matchesActivity(activity: LifestyleScore['areas']['activity']): boolean {
	const { steps, exercise } = activity;
	if (
		steps.value === null ||
		steps.value <= 0 ||
		exercise.minutes === null ||
		steps.score === null ||
		exercise.score === null
	)
		return false;
	const value = steps.value;
	const expectedSteps = stepBands.find(([minimum]) => value >= minimum)?.[1] ?? 0;
	return (
		expectedSteps === steps.score &&
		Math.round(Math.min(exercise.minutes / 30, 1) * 40) === exercise.score &&
		steps.score + exercise.score === activity.score
	);
}

function activityDescription(activity: LifestyleScore['areas']['activity']): string {
	const { steps, exercise } = activity;
	if (steps.value === null && exercise.minutes === null)
		return activity.score === null
			? '아직 활동 기록과 점수를 확인할 수 없어요.'
			: '활동 점수는 확인되지만 걸음 수와 인정 운동 시간 정보가 없어요.';
	let record: string;
	if (steps.value !== null && exercise.minutes !== null)
		record =
			steps.value === 0
				? `걸음 수는 0보, 인정 운동은 ${exercise.minutes.toLocaleString('ko-KR')}분으로 기록됐어요.`
				: `오늘은 ${steps.value.toLocaleString('ko-KR')}보를 걸었고 인정 운동은 ${exercise.minutes.toLocaleString('ko-KR')}분이에요.`;
	else if (steps.value !== null)
		record =
			steps.value === 0
				? '걸음 수는 0보로 기록됐어요.'
				: `오늘은 ${steps.value.toLocaleString('ko-KR')}보를 걸었어요.`;
	else record = `인정 운동은 ${exercise.minutes!.toLocaleString('ko-KR')}분으로 기록됐어요.`;
	const missing = [
		steps.value === null ? '걸음 수' : null,
		exercise.minutes === null ? '인정 운동 시간' : null,
		activity.score === null ? '활동 점수' : null
	].filter((value) => value !== null);
	if (missing.length) return `${record} ${missing.join('·')} 정보는 아직 확인할 수 없어요.`;
	if (!matchesActivity(activity) || !matchesGrade(activity.score, activity.grade)) return record;
	if (activity.grade === 1) return `${record} 걷기와 운동을 함께 챙긴 좋은 활동 기록이에요!`;
	if (steps.score === 60 && exercise.score! < 40)
		return `${record} 걷기는 잘 챙겼으니 짧은 운동도 더해보세요.`;
	if (exercise.score === 40 && steps.score! < 60)
		return `${record} 운동은 잘 챙겼으니 일상 속 걷기도 늘려보세요.`;
	if (activity.grade === 2) return `${record} 걷기와 운동을 꾸준히 이어가 보세요.`;
	// 배점이 다른 두 항목의 상대 기여를 비교해 제안할 활동만 선택한다.
	const difference = steps.score! * 40 - exercise.score! * 60;
	if (difference < 0) return `${record} 일상 속 걷기를 조금 더 늘려보세요.`;
	if (difference > 0) return `${record} 짧은 운동부터 조금씩 더해보세요.`;
	return `${record} 가벼운 걷기부터 활동량을 늘려보세요.`;
}

function matchesSleep(minutes: number, score: number): boolean {
	const hours = minutes / 60;
	if (hours >= 10) return score === 40;
	if (hours >= 9) return score === 90;
	if (hours >= 8) return score === 100;
	if (hours <= 3.5) return score === 0;
	for (let index = 1; index < sleepAnchors.length; index++) {
		const [h0, p0] = sleepAnchors[index - 1];
		const [h1, p1] = sleepAnchors[index];
		if (h0 <= hours && hours <= h1)
			return Math.round(p0 + ((p1 - p0) * (hours - h0)) / (h1 - h0)) === score;
	}
	return false;
}

function sleepDescription(sleep: LifestyleScore['areas']['sleep']): string {
	const minutes = sleep.sleepMinutes;
	if (minutes === null)
		return sleep.score === null
			? '아직 주 수면 기록과 점수를 확인할 수 없어요.'
			: '수면 점수는 확인되지만 점수에 사용된 수면 시간 정보가 없어요.';
	const record =
		minutes === 0 ? '주 수면은 0분으로 기록됐어요.' : `${formatSleepMinutes(minutes)} 주무셨네요.`;
	if (sleep.score === null) return `${record} 수면 점수는 아직 확인할 수 없어요.`;
	if (!matchesSleep(minutes, sleep.score) || !matchesGrade(sleep.score, sleep.grade)) return record;
	if (minutes === 0) return `${record} 수면 기록을 한 번 확인해 보세요.`;
	if (sleep.grade === 1) return `${record} 좋은 수면 기록이에요!`;
	if (minutes >= 600) return `${record} 긴 수면이 기록됐으니 평소 수면 패턴과 함께 살펴보세요.`;
	if (sleep.grade === 2) return `${record} 수면 시간을 조금 더 확보해 보세요.`;
	if (sleep.grade === 3) return `${record} 충분히 쉴 수 있도록 수면 시간을 더 확보해 보세요.`;
	return `${record} 수면 시간을 조금 더 확보해 보면 좋겠어요.`;
}

function matchesHeart(heart: LifestyleScore['areas']['heart'], difference: number): boolean {
	if (heart.sleepHrAvg === null || heart.baselineHrAvg === null || heart.score === null)
		return false;
	// 실제 소수 자릿수는 보존하고 이진 부동소수 오차만 제거한다.
	const rawDifference = Number((heart.sleepHrAvg - heart.baselineHrAvg).toFixed(10));
	const expected = (value: number) => (value <= 5 ? 100 : value <= 7 ? 70 : value < 10 ? 55 : 40);
	return expected(rawDifference) === heart.score && expected(difference) === heart.score;
}

function heartDescription(heart: LifestyleScore['areas']['heart']): string {
	const format = (value: number) =>
		value.toLocaleString('ko-KR', { maximumFractionDigits: 1, useGrouping: false });
	if (heart.sleepHrAvg === null) {
		if (heart.baselineHrAvg !== null)
			return (
				`평소 수면 중 평균 심박은 ${format(heart.baselineHrAvg)} bpm이에요. ` +
				(heart.checkWear === true
					? '오늘 측정값은 확인할 수 없으니 워치 착용 상태도 확인해 주세요.'
					: '오늘 수면 중 평균 심박은 확인할 수 없어요.')
			);
		const missing =
			heart.score === null
				? '아직 수면 중 평균 심박 기록과 점수를 확인할 수 없어요.'
				: '심박 점수는 확인되지만 수면 중 평균 심박 정보가 없어요.';
		return heart.checkWear === true ? `${missing} 워치 착용 상태를 확인해 주세요.` : missing;
	}
	const current = format(heart.sleepHrAvg);
	if (heart.baselineHrAvg === null) {
		const comparison =
			heart.checkWear === true
				? '평소 기준은 확인할 수 없으니 워치 착용 상태도 확인해 주세요.'
				: heart.score === null
					? '비교할 평소 심박 기록을 쌓는 중이에요.'
					: '평소 심박 정보가 없어 비교 설명은 어려워요.';
		return `수면 중 평균 심박은 ${current} bpm이에요. ${comparison}`;
	}
	const baseline = format(heart.baselineHrAvg);
	const difference = Number((Number(current) - Number(baseline)).toFixed(1));
	const comparison =
		difference === 0
			? `평소 ${baseline} bpm과 같아요.`
			: `평소 ${baseline} bpm보다 ${format(Math.abs(difference))} bpm ${difference > 0 ? '높아요' : '낮아요'}.`;
	const record = `수면 중 평균 심박은 ${current} bpm으로, ${comparison}`;
	if (heart.checkWear === true)
		return (
			`${record} ` +
			(heart.score === null
				? '심박 점수는 아직 확인할 수 없으니 워치 착용 상태도 확인해 주세요.'
				: '워치 착용 상태와 기록을 함께 확인해 주세요.')
		);
	if (heart.score === null) return `${record} 심박 점수는 아직 확인할 수 없어요.`;
	if (!matchesHeart(heart, difference) || !matchesGrade(heart.score, heart.grade)) return record;
	if (heart.grade === 2) return `${record} 다음 기록도 가볍게 살펴보세요.`;
	if (heart.grade === 3) return `${record} 평소보다 높은 기록이 이어지는지 며칠간 살펴보세요.`;
	return (
		`${record} ` +
		(heart.score === 100 ? '현재 패턴을 꾸준히 살펴보세요.' : '며칠간의 변화를 함께 살펴보세요.')
	);
}

/** 확인 가능한 측정값과 서버 평가만으로 짧은 안내를 만드는 순수 함수. */
export function homeScoreDescriptions(result: LifestyleScore | null): string[] {
	const metrics = ['활동', '수면', '심박'];
	if (result?.reason === 'BEFORE_LINK')
		return metrics.map((metric) => `앱을 연동하면 ${metric} 기록과 점수를 확인할 수 있어요.`);
	if (result?.reason === 'UNSYNCED')
		return metrics.map(() => '건강 기록의 동기화를 기다리고 있어요.');
	if (!result || result.reason === 'NO_RECORD')
		return metrics.map((metric) => `오늘의 ${metric} 기록이 없어요.`);
	return [
		activityDescription(result.areas.activity),
		sleepDescription(result.areas.sleep),
		heartDescription(result.areas.heart)
	];
}
