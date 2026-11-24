import { error } from '@sveltejs/kit';
import { isSleepDetailDate } from '#lib/features/health/sleep-detail.js';
import { activityToday } from '#lib/features/health/activity-api.js';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ params }) => {
	if (!isSleepDetailDate(params.date)) error(404, '올바른 날짜의 수면 기록을 선택해 주세요.');
	if (params.date > activityToday()) error(404, '미래 날짜의 수면 기록은 확인할 수 없어요.');
};
