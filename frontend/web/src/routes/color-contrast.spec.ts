import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * 색상 역할 기준(S15P31A403-300, docs/design-system.md 색상 역할)의 대비를 전역 토큰에서 바로 확인한다.
 * 토큰 HEX 가 바뀌어 기준 아래로 내려가면 여기서 먼저 알 수 있다. WCAG 2.x 상대 휘도 공식을 직접 계산한다.
 */
const css = readFileSync(fileURLToPath(new URL('./layout.css', import.meta.url)), 'utf8');

function token(name: string) {
	const match = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`));
	if (!match) throw new Error(`--color-${name} 토큰이 없습니다.`);
	return match[1];
}

function luminance(hex: string) {
	const [r, g, b] = [1, 3, 5].map((i) => {
		const value = parseInt(hex.slice(i, i + 2), 16) / 255;
		return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
	});
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string) {
	const [light, dark] = [luminance(token(a)), luminance(token(b))].sort((x, y) => y - x);
	return (light + 0.05) / (dark + 0.05);
}

describe('색상 역할 대비', () => {
	it('배경은 Figma 원본 값(#FAFAFB)을 쓴다', () => {
		expect(token('background').toLowerCase()).toBe('#fafafb');
	});

	it.each([
		['primary', '버튼·Toast 기본'],
		['primary-hover', '버튼 hover']
	])('밝은 글자가 올라가는 %s(%s)는 4.5:1 이상이다', (background) => {
		expect(contrast('primary-foreground', background)).toBeGreaterThanOrEqual(4.5);
	});

	it.each(['card', 'background', 'primary-soft', 'surface'])(
		'작은 빨간 글자는 진한 레드(primary)로 %s 위에서 4.5:1 이상이다(메인 레드 brand 는 미달)',
		(background) => {
			expect(contrast('primary', background)).toBeGreaterThanOrEqual(4.5);
			expect(contrast('brand', background)).toBeLessThan(4.5);
		}
	);

	it('메인 레드(brand) 위 밝은 글자는 4.5:1 미만이라 글자 배경으로 쓰지 않는다', () => {
		expect(contrast('primary-foreground', 'brand')).toBeLessThan(4.5);
	});

	it.each(['background', 'card'])('보조 텍스트는 %s 위에서 4.5:1 이상이다', (background) => {
		expect(contrast('text-secondary', background)).toBeGreaterThanOrEqual(4.5);
	});

	it.each(['surface', 'primary-soft', 'coral-soft', 'coral-sub', 'border'])(
		'%s 위에서는 보조 텍스트가 4.5:1 미만이라 주요 텍스트를 쓴다',
		(background) => {
			expect(contrast('text-secondary', background)).toBeLessThan(4.5);
			expect(contrast('text-primary', background)).toBeGreaterThanOrEqual(4.5);
		}
	);
});
