import { expect, it } from 'vitest';
import { placeHeartLabels } from './heart-labels';

it('숫자를 점 오른쪽 5px 간격에 두고 각 점의 세로 중심과 원본 좌표를 유지한다', () => {
	const labels = [90, 91, 40, 140, 100, 100, 85].map((y, index) => ({
		index,
		x: (index + 0.5) * 120,
		y,
		width: 28,
		height: 16
	}));
	const marks = labels.map((label) => ({ left: label.x - 9, top: 20, width: 18, height: 160 }));
	const original = structuredClone({ labels, marks });
	const placed = placeHeartLabels(labels, marks, 840, 200);
	expect(placed).toHaveLength(7);
	for (const [index, position] of placed.entries()) {
		expect(position).toEqual({
			index,
			left: labels[index].x + 12,
			top: labels[index].y - 8,
			width: 28,
			height: 16,
			hidden: false
		});
	}
	expect({ labels, marks }).toEqual(original);
});

it('오른쪽 경계가 부족하면 같은 점의 왼쪽에 두고 세로 위치를 바꾸지 않는다', () => {
	expect(
		placeHeartLabels([{ index: 0, x: 170, y: 80, width: 30, height: 16 }], [], 200, 160)
	).toEqual([{ index: 0, left: 128, top: 72, width: 30, height: 16, hidden: false }]);
});

it('인접 날짜의 점·범위선 또는 먼저 배치한 숫자와 겹치면 반대쪽을 시도한다', () => {
	const labels = [{ index: 0, x: 100, y: 80, width: 30, height: 16 }];
	for (const mark of [
		{ left: 120, top: 73, width: 14, height: 14 },
		{ left: 120, top: 20, width: 18, height: 140 }
	]) {
		expect(placeHeartLabels(labels, [mark], 200, 160)[0]).toMatchObject({
			left: 58,
			top: 72,
			hidden: false
		});
	}
	const placed = placeHeartLabels(
		[
			{ index: 0, x: 100, y: 80, width: 30, height: 16 },
			{ index: 1, x: 80, y: 80, width: 30, height: 16 }
		],
		[],
		200,
		160
	);
	expect(placed[1]).toMatchObject({ left: 38, top: 72, hidden: false });
});

it('양쪽 모두 막히면 숫자만 숨기고 숨긴 숫자는 다음 배치를 막지 않는다', () => {
	const labels = [
		{ index: 0, x: 50, y: 80, width: 500, height: 16 },
		{ index: 1, x: 100, y: 80, width: 30, height: 16 }
	];
	const placed = placeHeartLabels(
		labels,
		[
			{ left: 58, top: 72, width: 30, height: 16 },
			{ left: 112, top: 72, width: 30, height: 16 }
		],
		200,
		160
	);
	expect(placed.map((position) => position.hidden)).toEqual([true, true]);
	expect(placed.every((position) => position.left === 0 && position.top === 0)).toBe(true);
	expect(placeHeartLabels(labels, [], 200, 160)[1].hidden).toBe(false);
});

it('첫 날짜·소수 너비 경계에서도 plot 안쪽을 지키며 글자 너비 변경을 반영한다', () => {
	expect(
		placeHeartLabels([{ index: 0, x: 5, y: 80, width: 20, height: 16 }], [], 200, 160)[0]
	).toMatchObject({ left: 17, top: 72, hidden: false });
	const label = { index: 0, x: 70.25, y: 50, width: 17.2, height: 16 };
	expect(placeHeartLabels([label], [], 100.4, 100)[0]).toMatchObject({
		left: 41.05,
		top: 42,
		hidden: false
	});
	expect(placeHeartLabels([{ ...label, width: 17 }], [], 100.4, 100)[0]).toMatchObject({
		left: 82.25,
		top: 42,
		hidden: false
	});
});

it.each([0, 200])('plot 끝 y=%i에서도 점 중심에 맞춘 숫자 7개를 표시한다', (y) => {
	const labels = [60, 180, 300, 420, 540, 660, 780].map((x, index) => ({
		index,
		x,
		y,
		width: 24,
		height: 16
	}));
	const marks = labels.map((label) => ({ left: label.x - 9, top: y - 7, width: 18, height: 14 }));
	const placed = placeHeartLabels(labels, marks, 840, 200);
	expect(placed.map((label) => label.hidden)).toEqual(Array(7).fill(false));
	expect(placed.map((label) => label.left)).toEqual([72, 192, 312, 432, 552, 672, 792]);
	expect(placed.map((label) => label.top)).toEqual(Array(7).fill(y === 0 ? -8 : 192));
});

it('상하 여유가 있어도 좌우 공간이나 충돌 때문에 배치할 수 없으면 숨긴다', () => {
	const label = { index: 0, x: 50, y: 0, width: 30, height: 16 };
	const marks = [
		{ left: 62, top: -8, width: 30, height: 16 },
		{ left: 8, top: -8, width: 30, height: 16 }
	];
	expect(placeHeartLabels([label], marks, 200, 100)[0].hidden).toBe(true);
	expect(placeHeartLabels([{ ...label, width: 200 }], [], 200, 100)[0].hidden).toBe(true);
	expect(placeHeartLabels([{ ...label, height: 18 }], [], 200, 100)[0].hidden).toBe(true);
});

it.each([
	{ x: 50, y: -0.01, width: 20, height: 16 },
	{ x: 50, y: 100.01, width: 20, height: 16 },
	{ x: 50, y: 50, width: 120, height: 16 },
	{ x: 50, y: 50, width: 0, height: 0 },
	{ x: Number.NaN, y: 50, width: 20, height: 16 },
	{ x: 50, y: Number.POSITIVE_INFINITY, width: 20, height: 16 }
])('상하 경계·미측정·배치 불가능한 라벨은 차트 밖에 표시하지 않는다: %j', (label) => {
	expect(placeHeartLabels([{ index: 0, ...label }], [], 100, 100)[0]).toMatchObject({
		left: 0,
		top: 0,
		hidden: true
	});
});
