/** 고정 UI를 제외한 건강데이터 화면의 실제 가용 영역. */
export function healthViewportBounds() {
	const header = document.querySelector('header');
	const navigation = document.querySelector('nav[aria-label="주 메뉴"]');
	return {
		top:
			header && ['fixed', 'sticky'].includes(getComputedStyle(header).position)
				? Math.max(0, header.getBoundingClientRect().bottom)
				: 0,
		bottom:
			navigation && getComputedStyle(navigation).position === 'fixed'
				? Math.min(innerHeight, navigation.getBoundingClientRect().top)
				: innerHeight
	};
}

/** 이미 보이는 대상은 움직이지 않고, 가려진 부분만 드러낸다. */
export function revealHealthFocus(target: HTMLElement) {
	const { top, bottom } = healthViewportBounds();
	if (bottom <= top) return;
	const bounds = target.getBoundingClientRect();
	if (bounds.top >= top && bounds.bottom <= bottom) return;
	const gap = Math.max(0, Math.min(8, (bottom - top - bounds.height) / 2));
	const upper = top + gap;
	const lower = bottom - gap;
	const distance =
		bounds.height > lower - upper
			? (bounds.top + bounds.bottom - upper - lower) / 2
			: bounds.top < upper
				? bounds.top - upper
				: bounds.bottom > lower
					? bounds.bottom - lower
					: 0;
	if (distance) window.scrollBy({ top: distance, behavior: 'instant' });
}
