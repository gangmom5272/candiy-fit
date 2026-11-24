import { expect, it } from 'vitest';
import { barMaximum, barOpacity } from './health-bar-color';

it('유효한 값의 최댓값만 사용하고 입력 순서나 결측에 영향받지 않는다', () => {
	const values = [300, null, 900, undefined, 0, 300, Number.NaN, Infinity, -10];
	expect(barMaximum(values)).toBe(900);
	expect(barMaximum([...values].reverse())).toBe(900);
	expect(barMaximum([])).toBe(0);
});

it('값의 순서대로 진해지고 같은 값은 같은 진하기를 유지한다', () => {
	const values = [750, 0, 250, 250, 1000];
	const opacity = values.map((value) => barOpacity(value, barMaximum(values)));
	expect(opacity[1]).toBeGreaterThan(0);
	expect(opacity[1]).toBeLessThan(opacity[2]);
	expect(opacity[2]).toBe(opacity[3]);
	expect(opacity[3]).toBeLessThan(opacity[0]);
	expect(opacity[0]).toBeLessThan(opacity[4]);
	expect(opacity[4]).toBe(1);
});

it.each([0, 0.25, 420, Number.MAX_VALUE])(
	'같은 값 %s만 있거나 하나뿐이어도 유한한 진하기다',
	(value) => {
		for (const values of [[value], [value, null, value]]) {
			const maximum = barMaximum(values);
			expect(
				values.filter((item) => item !== null).map((item) => barOpacity(item, maximum))
			).toEqual(values.filter((item) => item !== null).map(() => 1));
		}
	}
);

it.each([null, undefined, Number.NaN, Infinity, -Infinity, -1])(
	'결측·유효하지 않은 값 %s는 실제 0과 구분한다',
	(value) => {
		expect(barOpacity(value, 0)).toBe(0);
		expect(barOpacity(0, 0)).toBe(1);
	}
);

it('소수와 극단값에서도 표시 범위를 벗어나지 않고 큰 값이 더 연해지지 않는다', () => {
	for (const values of [
		[0, 0.1, 0.5],
		[0, Number.MIN_VALUE, Number.MAX_VALUE]
	]) {
		const opacities = values.map((value) => barOpacity(value, barMaximum(values)));
		for (const [index, opacity] of opacities.entries()) {
			expect(Number.isFinite(opacity)).toBe(true);
			expect(opacity).toBeGreaterThan(0);
			expect(opacity).toBeLessThanOrEqual(1);
			if (index > 0) expect(opacity).toBeGreaterThanOrEqual(opacities[index - 1]);
		}
	}
});
