import { describe, expect, it } from 'vitest';
import { healthSummaryAxis, healthSummaryY, formatHealthSummaryTick } from './health-summary-axis';

const defaults = {
	steps: { min: 0, max: 10000, unit: '보' },
	exercise: { min: 0, max: 120, unit: '분' },
	sleep: { min: 0, max: 480, unit: '분' },
	heart: { min: 60, max: 90, unit: 'bpm' }
};

const boundaryCases: [keyof typeof defaults, number, number[]][] = [
	...([0, 1, 9999, 10000] as const).map((value): [keyof typeof defaults, number, number[]] => [
		'steps',
		value,
		[10000, 5000, 0]
	]),
	['steps', 10001, [12000, 6000, 0]],
	['steps', 12001, [15000, 7500, 0]],
	['steps', 15001, [16000, 8000, 0]],
	['steps', 20001, [24000, 12000, 0]],
	['steps', 30001, [36000, 18000, 0]],
	['steps', 40000, [40000, 20000, 0]],
	['steps', 60001, [70000, 35000, 0]],
	['exercise', 120, [120, 60, 0]],
	['exercise', 120.1, [150, 75, 0]],
	['exercise', 121, [150, 75, 0]],
	['exercise', 600.5, [660, 330, 0]],
	['sleep', 480, [480, 240, 0]],
	['sleep', 480.1, [540, 270, 0]],
	['sleep', 481, [540, 270, 0]],
	['sleep', 1200, [1200, 600, 0]],
	['heart', 50, [90, 70, 50]],
	['heart', 90, [90, 75, 60]],
	['heart', 90.1, [100, 80, 60]],
	['heart', 91, [100, 80, 60]],
	['heart', 160.1, [180, 120, 60]],
	['heart', 250, [260, 160, 60]]
];

describe('건강 요약 표시 축', () => {
	it.each(boundaryCases)(
		'%s %s: 실제 값과 기본 범위를 포함한 정확한 눈금 %j',
		(metric, value, ticks) => {
			const input = { ...defaults[metric], values: [value], average: value };
			const axis = healthSummaryAxis(input);
			expect(axis.ticks).toEqual(ticks);
			expect(axis.min).toBeLessThanOrEqual(Math.min(input.min, value));
			expect(axis.max).toBeGreaterThanOrEqual(Math.max(input.max, value));
			expect(axis.ticks.every(Number.isInteger)).toBe(true);
			for (const tick of axis.ticks) {
				const label = formatHealthSummaryTick(tick, input.unit);
				const time = label.match(/^(\d+)h(?: (\d+)m)?$/);
				const representedValue = time
					? Number(time[1]) * 60 + Number(time[2] ?? 0)
					: Number(label.replace('분', '').replaceAll(',', ''));
				expect(representedValue).toBe(tick);
			}
		}
	);

	it('10,000보를 1보 초과해도 상한은 12,000보이며 두 배로 확장하지 않는다', () => {
		const before = healthSummaryAxis({ ...defaults.steps, values: [10000], average: null });
		const after = healthSummaryAxis({ ...defaults.steps, values: [10001], average: null });
		expect(before.max).toBe(10000);
		expect(after.max).toBe(12000);
		expect(10001 / after.max).toBeGreaterThan(0.8);
		expect(after.ticks).toHaveLength(before.ticks.length);
	});

	it('걸음 축의 모든 간격 경계에서 다음 상한은 25% 이내로 증가한다', () => {
		const boundaries = [1, 1.2, 1.5, 1.8, 2, 2.5, 3, 3.5, 4, 5, 6, 7.5, 8, 10];
		for (const power of [4, 5, 6, 9]) {
			for (const multiple of boundaries) {
				const boundary = multiple * 10 ** power;
				const before = healthSummaryAxis({ ...defaults.steps, values: [boundary], average: null });
				const after = healthSummaryAxis({
					...defaults.steps,
					values: [boundary + 1],
					average: null
				});
				expect(after.max).toBeGreaterThanOrEqual(boundary + 1);
				expect(after.max / before.max).toBeLessThanOrEqual(1.25);
			}
		}
	});

	it.each([
		[75, '1h 15m'],
		[150, '2h 30m'],
		[270, '4h 30m']
	] as const)('%s분은 소수 시간 대신 %s로 표시한다', (minutes, label) => {
		expect(formatHealthSummaryTick(minutes, '분')).toBe(label);
	});

	for (const unit of ['보', '분', 'bpm']) {
		for (const values of [
			[Number.MAX_VALUE],
			[-Number.MAX_VALUE],
			[-Number.MAX_VALUE, 0, Number.MAX_VALUE, null]
		]) {
			it(`${unit}: 극단 유한값과 반대 부호 범위에서도 눈금·SVG 좌표는 유한하다 (${values.length})`, () => {
				const input = { min: 0, max: 10000, unit, values, average: Number.MAX_VALUE };
				const original = [...values];
				const axis = healthSummaryAxis(input);
				expect(axis.ticks.every(Number.isFinite)).toBe(true);
				expect(axis.ticks[0]).toBeGreaterThan(axis.ticks[1]);
				expect(axis.ticks[1]).toBeGreaterThan(axis.ticks[2]);
				for (const value of [...axis.ticks, ...values, input.average]) {
					if (value === null) continue;
					expect(value).toBeGreaterThanOrEqual(axis.min);
					expect(value).toBeLessThanOrEqual(axis.max);
					const y = healthSummaryY(value, axis);
					expect(Number.isFinite(y)).toBe(true);
					expect(y).toBeGreaterThanOrEqual(24);
					expect(y).toBeLessThanOrEqual(96);
				}
				for (const tick of axis.ticks) {
					const label = formatHealthSummaryTick(tick, unit);
					expect(label).not.toMatch(/Infinity|NaN|∞/);
					// 극단값의 과학적 표기도 원래 눈금 값으로 정확하게 복원된다.
					expect(Number(label.replace(/m$/, '').replaceAll(',', ''))).toBe(tick);
				}
				expect(input.values).toEqual(original);
				expect(input.average).toBe(Number.MAX_VALUE);
			});
		}
	}

	for (const [metric, base] of Object.entries(defaults)) {
		for (const values of [[], [null, null], [0, 0], [75, 75], [null, 15001, null]]) {
			it(`${metric} ${JSON.stringify(values)}: 결측·동일 값에서도 축이 유효하고 입력은 보존한다`, () => {
				const original = [...values];
				Object.freeze(values);
				const input = Object.freeze({ ...base, values, average: null });
				const axis = healthSummaryAxis(input);
				expect(axis.max).toBeGreaterThan(axis.min);
				expect(axis.ticks.every(Number.isFinite)).toBe(true);
				expect(axis.min).toBeLessThanOrEqual(base.min);
				expect(axis.max).toBeGreaterThanOrEqual(base.max);
				for (const value of values) {
					if (value !== null) {
						expect(value).toBeGreaterThanOrEqual(axis.min);
						expect(value).toBeLessThanOrEqual(axis.max);
					}
				}
				expect(values).toEqual(original);
			});
		}
		it(`${metric}: 일별 최대보다 큰 서버 평균도 포함하며 원본 평균을 유지한다`, () => {
			const input = Object.freeze({ ...base, values: [1, null, 2], average: base.max + 0.1 });
			const axis = healthSummaryAxis(input);
			expect(axis.max).toBeGreaterThanOrEqual(input.average);
			expect(input.average).toBe(base.max + 0.1);
			expect(input.values).toEqual([1, null, 2]);
		});
	}
});
