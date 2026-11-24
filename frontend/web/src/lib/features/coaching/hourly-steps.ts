import { getActivityHours } from '#lib/features/health/activity-api.js';

/** 그날 0시~23시 걸음 24칸. 시간대 기록이 없는 날은 null */
export type HourlySteps = readonly number[] | null;

export interface DateRange {
	start: string;
	end: string;
}

function monthEnd(date: string): string {
	const [year, month] = date.split('-').map(Number);
	const last = new Date(Date.UTC(year, month, 0)).getUTCDate();
	return `${date.slice(0, 7)}-${String(last).padStart(2, '0')}`;
}

function datesBetween({ start, end }: DateRange): string[] {
	const dates: string[] = [];
	const last = Date.parse(`${end}T00:00:00Z`);
	for (let time = Date.parse(`${start}T00:00:00Z`); time <= last; time += 86400000) {
		dates.push(new Date(time).toISOString().slice(0, 10));
	}
	return dates;
}

/**
 * 그날이 든 달 가운데 근거 기간에 드는 범위(S15P31A403-439).
 * 시간대 걸음 조회(/health/timeline)는 한 번에 31일까지라 한 달을 한 번에 부를 수 있다.
 * 근거 기간 밖(다음 달 초, 오늘 뒤)은 부르지 않는다
 */
export function hourlyRange(date: string, period: DateRange): DateRange {
	const first = `${date.slice(0, 7)}-01`;
	const last = monthEnd(date);
	return {
		start: first < period.start ? period.start : first,
		end: last > period.end ? period.end : last
	};
}

/**
 * 활동 달력의 시간대별 걸음을 달 단위로 불러와 날짜별로 쌓아 둔다(S15P31A403-439).
 * 날짜를 누를 때마다 그날 하루를 따로 부르면, 빠르게 여러 날을 누를 때 누른 날마다 자리 표시가 떴다 사라지고
 * 앞 요청이 취소돼 같은 날을 다시 눌러도 또 불러왔다. 한 달을 한 번에 받아 두면 같은 달 안에서는 바로 그린다.
 * - 같은 범위를 동시에 여러 번 청해도 요청은 하나다
 * - 응답에 없는 날도 "기록 없음"으로 채워 다시 부르지 않는다
 * - 실패한 범위는 쌓지 않아 다시 부를 수 있다
 */
export function createHourlyStepsLoader(fetchHours: typeof getActivityHours = getActivityHours) {
	const loaded = new Map<string, HourlySteps>();
	const pending = new Map<string, Promise<void>>();
	const controller = new AbortController();

	return {
		/** 불러온 날이면 그날 값(기록 없음은 null), 아직이면 undefined */
		peek(date: string): HourlySteps | undefined {
			return loaded.get(date);
		},
		load(range: DateRange): Promise<void> {
			const key = `${range.start}~${range.end}`;
			const running = pending.get(key);
			if (running) return running;
			if (datesBetween(range).every((date) => loaded.has(date))) return Promise.resolve();
			const request = fetchHours(range.start, range.end, controller.signal)
				.then((days) => {
					const byDate = new Map(days.map((day) => [day.date, day.hourlySteps]));
					for (const date of datesBetween(range)) loaded.set(date, byDate.get(date) ?? null);
				})
				.finally(() => pending.delete(key));
			pending.set(key, request);
			return request;
		},
		/** 달력이 사라지면 오가던 요청을 끊는다 */
		dispose() {
			controller.abort();
		}
	};
}

export type HourlyStepsLoader = ReturnType<typeof createHourlyStepsLoader>;
