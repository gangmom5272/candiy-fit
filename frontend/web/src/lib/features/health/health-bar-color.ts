/** 같은 조회 기간의 유효한 측정값만 색상 기준에 포함한다. 축 상한·평균과는 별개다. */
export function barMaximum(values: readonly (number | null | undefined)[]): number {
	return values.reduce<number>(
		(maximum, value) =>
			typeof value === 'number' && Number.isFinite(value) && value >= 0
				? Math.max(maximum, value)
				: maximum,
		0
	);
}

/** 기존 평균 걸음 수 차트의 진하기를 공유하며, 실제 0과 결측을 구분한다. */
export function barOpacity(value: number | null | undefined, maximum: number): number {
	if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return 0;
	return maximum > 0 && Number.isFinite(maximum) ? 0.35 + 0.65 * Math.min(1, value / maximum) : 1;
}
