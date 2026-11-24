import { describe, expect, it } from 'vitest';
import { BUTTON_SIZES, BUTTON_VARIANTS, buttonVariants } from './button-variants';

describe('buttonVariants', () => {
	it('기본값은 주요 동작(default) · 기본 크기(default)다', () => {
		expect(buttonVariants()).toBe(buttonVariants({ variant: 'default', size: 'default' }));
		expect(buttonVariants()).toContain(BUTTON_VARIANTS.default);
		expect(buttonVariants()).toContain(BUTTON_SIZES.default);
	});

	it('주요 동작은 글자가 올라가는 primary 면과 primary-foreground 글자를 쓴다', () => {
		const classes = buttonVariants({ variant: 'default' }).split(' ');
		expect(classes).toEqual(
			expect.arrayContaining(['bg-primary', 'text-primary-foreground', 'hover:bg-primary-hover'])
		);
	});

	it.each(Object.keys(BUTTON_VARIANTS) as (keyof typeof BUTTON_VARIANTS)[])(
		'%s 는 테두리 색을 정해 모양이 달라도 크기가 같다',
		(variant) => {
			expect(buttonVariants({ variant })).toMatch(/(^| )border( |$)/);
			expect(BUTTON_VARIANTS[variant]).toMatch(/(^| )border-(transparent|border)( |$)/);
		}
	);

	it('ghost 는 글자색을 정하지 않아 호출하는 쪽의 색을 따른다', () => {
		expect(BUTTON_VARIANTS.ghost).not.toMatch(/(^| )text-/);
		expect(BUTTON_VARIANTS.default).toContain('text-primary-foreground');
	});

	it('큰 버튼은 lg 하나다(예전 xl 은 lg 로 합쳤다)', () => {
		expect(Object.keys(BUTTON_SIZES)).toEqual(['sm', 'default', 'lg', 'icon']);
		expect(BUTTON_SIZES.lg).toContain('min-h-13');
	});

	it('icon 크기는 가로세로가 같은 원이다', () => {
		expect(BUTTON_SIZES.icon.split(' ')).toEqual(['size-10', 'rounded-full']);
	});

	it('포커스 테두리는 primary 가 아니라 ring 을 쓴다', () => {
		expect(buttonVariants()).toContain('focus-visible:outline-ring');
		expect(buttonVariants()).not.toContain('outline-primary');
	});

	it('예전 이름(primary-strong · text-on-primary · text-text-*)을 쓰지 않는다', () => {
		for (const variant of Object.keys(BUTTON_VARIANTS) as (keyof typeof BUTTON_VARIANTS)[])
			expect(buttonVariants({ variant })).not.toMatch(/primary-strong|text-on-primary|text-text-/);
	});
});
