interface Bounds {
	left: number;
	top: number;
	width: number;
	height: number;
}

interface Label {
	index: number;
	x: number;
	y: number;
	width: number;
	height: number;
}

/** 점 오른쪽을 우선하고, 같은 높이의 양쪽 모두 자리가 없으면 숫자만 숨긴다. */
export function placeHeartLabels(
	labels: readonly Label[],
	marks: readonly Bounds[],
	plotWidth: number,
	plotHeight: number
) {
	const gap = 1;
	// 카드 안의 범례·날짜 간격은 최소 8px이며, 16px 숫자는 축 끝에서 절반만큼 펼쳐진다.
	const verticalAllowance = 8;
	const overlaps = (a: Bounds, b: Bounds) =>
		a.left < b.left + b.width + gap &&
		a.left + a.width + gap > b.left &&
		a.top < b.top + b.height + gap &&
		a.top + a.height + gap > b.top;
	const placed: (Bounds & { index: number; hidden: boolean })[] = [];
	for (const label of labels) {
		// 점 반지름 7px에서 숫자까지 5px를 띄우며 세로 범위선의 끝도 피한다.
		const candidates = [label.x + 12, label.x - 12 - label.width].map((left) => ({
			left,
			top: label.y - label.height / 2,
			width: label.width,
			height: label.height
		}));
		const position = candidates.find(
			(candidate) =>
				candidate.width > 0 &&
				candidate.height > 0 &&
				candidate.left >= gap &&
				candidate.left + candidate.width <= plotWidth - gap &&
				candidate.top >= -verticalAllowance &&
				candidate.top + candidate.height <= plotHeight + verticalAllowance &&
				!marks.some((mark) => overlaps(candidate, mark)) &&
				!placed.some((other) => !other.hidden && overlaps(candidate, other))
		);
		placed.push({
			index: label.index,
			...(position ?? { left: 0, top: 0, width: label.width, height: label.height }),
			hidden: position === undefined
		});
	}
	return placed;
}
