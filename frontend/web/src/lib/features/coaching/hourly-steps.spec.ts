import { describe, expect, it, vi } from 'vitest';
import { createHourlyStepsLoader, hourlyRange } from './hourly-steps';

const hours = (peak: number) => Array.from({ length: 24 }, (_, hour) => (hour === 18 ? peak : 0));

describe('hourlyRange', () => {
	const period = { start: '2026-10-18', end: '2026-11-17' };

	it('그날이 든 달을 근거 기간 안으로 자른다', () => {
		expect(hourlyRange('2026-10-24', period)).toEqual({ start: '2026-10-18', end: '2026-10-31' });
		expect(hourlyRange('2026-11-03', period)).toEqual({ start: '2026-11-01', end: '2026-11-17' });
	});

	it('달이 기간 안에 다 들면 그 달 첫날부터 마지막 날까지다', () => {
		const long = { start: '2026-01-15', end: '2026-06-20' };
		expect(hourlyRange('2026-02-10', long)).toEqual({ start: '2026-02-01', end: '2026-02-28' });
		expect(hourlyRange('2024-02-10', { start: '2024-01-01', end: '2024-03-31' })).toEqual({
			start: '2024-02-01',
			end: '2024-02-29'
		});
	});

	it('한 번에 부르는 날은 31일을 넘지 않는다', () => {
		const long = { start: '2026-01-01', end: '2026-12-31' };
		for (let month = 1; month <= 12; month += 1) {
			const { start, end } = hourlyRange(`2026-${String(month).padStart(2, '0')}-15`, long);
			const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
			expect(days).toBeLessThanOrEqual(31);
		}
	});
});

describe('createHourlyStepsLoader', () => {
	const range = { start: '2026-10-01', end: '2026-10-03' };

	it('한 범위를 한 번에 받아 날짜별로 쌓고, 응답에 없는 날은 기록 없음으로 둔다', async () => {
		const fetchHours = vi.fn().mockResolvedValue([
			{ date: '2026-10-01', hourlySteps: hours(1510) },
			{ date: '2026-10-02', hourlySteps: null }
		]);
		const loader = createHourlyStepsLoader(fetchHours);
		expect(loader.peek('2026-10-01')).toBeUndefined();
		await loader.load(range);
		expect(loader.peek('2026-10-01')).toEqual(hours(1510));
		expect(loader.peek('2026-10-02')).toBeNull();
		expect(loader.peek('2026-10-03')).toBeNull();
		// 범위 밖은 아직 모른다
		expect(loader.peek('2026-10-04')).toBeUndefined();
		expect(fetchHours).toHaveBeenCalledTimes(1);
		expect(fetchHours.mock.calls[0].slice(0, 2)).toEqual(['2026-10-01', '2026-10-03']);
	});

	it('같은 범위를 동시에 여러 번 청해도, 다 받은 뒤 다시 청해도 요청은 하나다', async () => {
		const fetchHours = vi.fn().mockResolvedValue([{ date: '2026-10-01', hourlySteps: hours(1) }]);
		const loader = createHourlyStepsLoader(fetchHours);
		await Promise.all([loader.load(range), loader.load(range), loader.load(range)]);
		await loader.load(range);
		expect(fetchHours).toHaveBeenCalledTimes(1);
	});

	it('실패한 범위는 쌓지 않아 다시 부를 수 있다', async () => {
		const fetchHours = vi
			.fn()
			.mockRejectedValueOnce(new Error('network'))
			.mockResolvedValue([{ date: '2026-10-01', hourlySteps: hours(1510) }]);
		const loader = createHourlyStepsLoader(fetchHours);
		await expect(loader.load(range)).rejects.toThrow('network');
		expect(loader.peek('2026-10-01')).toBeUndefined();
		await loader.load(range);
		expect(loader.peek('2026-10-01')).toEqual(hours(1510));
		expect(fetchHours).toHaveBeenCalledTimes(2);
	});

	it('달력이 사라지면 오가던 요청을 끊는다', () => {
		const fetchHours = vi.fn().mockReturnValue(new Promise(() => {}));
		const loader = createHourlyStepsLoader(fetchHours);
		void loader.load(range);
		const signal = fetchHours.mock.calls[0][2] as AbortSignal;
		expect(signal.aborted).toBe(false);
		loader.dispose();
		expect(signal.aborted).toBe(true);
	});
});
