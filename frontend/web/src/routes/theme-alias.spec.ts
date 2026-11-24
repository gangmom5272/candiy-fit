import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * shadcn/ui 테마 이름(S15P31A403-339)이 기존 토큰을 참조하는지 확인한다.
 * 별칭에 HEX 를 직접 적으면 같은 색이 두 곳에서 관리되므로, 반드시 기존 토큰을 var() 로 가리키게 한다.
 */
const css = readFileSync(fileURLToPath(new URL('./layout.css', import.meta.url)), 'utf8');

function declaration(name: string) {
	const match = css.match(new RegExp(`--color-${name}:\\s*([^;]+);`));
	if (!match) throw new Error(`--color-${name} 토큰이 없습니다.`);
	return match[1].trim();
}

/** shadcn 이름 → 가리키는 기존 토큰 */
const ALIASES: [string, string][] = [
	['foreground', 'text-primary'],
	['card-foreground', 'text-primary'],
	['popover', 'card'],
	['popover-foreground', 'text-primary'],
	['secondary', 'surface'],
	['secondary-foreground', 'text-primary'],
	['muted', 'surface'],
	['muted-foreground', 'text-secondary'],
	['accent', 'primary-soft'],
	['accent-foreground', 'primary'],
	['destructive', 'danger'],
	['input', 'border'],
	['ring', 'brand']
];

describe('shadcn 테마 이름', () => {
	it.each(ALIASES)('%s 는 기존 토큰 %s 를 참조한다', (alias, source) => {
		expect(declaration(alias)).toBe(`var(--color-${source})`);
		expect(declaration(source)).toMatch(/^#[0-9a-f]{6}$/);
	});

	it('이름과 뜻이 이미 같은 background·card·border 는 값을 그대로 둔다', () => {
		expect(declaration('background')).toBe('#fafafb');
		expect(declaration('card')).toBe('#fefefe');
		expect(declaration('border')).toBe('#e8edf5');
	});

	it('brand 는 primary 를 참조하지 않고 메인 레드 값을 직접 가진다', () => {
		// 장식·포커스 테두리(메인 레드)와 주요 면(진한 레드)은 서로 다른 색이다
		expect(declaration('brand')).toBe('#f85a5a');
	});

	it('전역 CSS 의 포커스 테두리·스크롤바는 primary 를 참조하지 않는다(포커스 테두리는 ring, 장식은 brand)', () => {
		expect(
			css.match(/(?:outline(?:-color)?|background):[^;]*var\(\s*--color-primary\s*\)/g)
		).toBeNull();
	});

	it('primary 는 글자가 올라가는 주요 면(진한 레드)이고 그 위 글자는 primary-foreground 다', () => {
		expect(declaration('primary')).toBe('#cc383e');
		expect(declaration('primary-hover')).toBe('#b83137');
		expect(declaration('primary-foreground')).toBe('#fcf9f6');
	});

	it('합쳐진 예전 토큰 primary-strong·text-on-primary 는 정의하지 않는다', () => {
		expect(css).not.toMatch(/--color-primary-strong(?:-hover)?\s*:/);
		expect(css).not.toMatch(/--color-text-on-primary\s*:/);
	});
});

/**
 * primary 이름 정리(S15P31A403-350·353·358).
 * - 350·353: 장식용 메인 레드를 brand, 포커스 테두리를 ring 으로 옮겼다.
 * - 358: primary 를 shadcn 의 뜻(글자가 올라가는 주요 면, 진한 레드)으로 바꾸고 primary-strong·text-on-primary 를 합쳤다.
 * 합쳐진 이름이 다시 들어오거나, 포커스 테두리에 ring 대신 primary 를 쓰면 여기서 막는다.
 */
const RETIRED_TOKEN_USAGE =
	/(?<![\w-])(?:[a-z-]+:)*[a-z-]*primary-strong(?:-hover)?(?![\w-])|(?<![\w])[a-z:-]*text-on-primary(?![\w-])/g;
const FOCUS_WITH_PRIMARY = /(?<![\w-])(?:[a-z-]+:)*(?:outline|ring)-primary(?![\w-])/g;

function sourceFiles(directory: URL): string[] {
	return readdirSync(directory, { withFileTypes: true, recursive: true })
		.filter((entry) => entry.isFile() && /\.(svelte|ts)$/.test(entry.name))
		.map((entry) => `${entry.parentPath}/${entry.name}`.replaceAll('\\', '/'))
		.filter((path) => !/\.(spec|e2e)\.ts$/.test(path));
}

function findUsage(pattern: RegExp) {
	return sourceFiles(new URL('..', import.meta.url)).flatMap((path) =>
		(readFileSync(path, 'utf8').match(pattern) ?? []).map(
			(name) => `${path.slice(path.indexOf('/src/') + 1)}: ${name}`
		)
	);
}

describe('primary 이름 정리', () => {
	it.each([
		'bg-primary-strong',
		'hover:bg-primary-strong-hover',
		'text-primary-strong',
		'var(--color-primary-strong)',
		'text-text-on-primary',
		'var(--color-text-on-primary)'
	])('합쳐진 예전 이름 %s 를 검출한다', (source) => {
		expect(source.match(RETIRED_TOKEN_USAGE)).not.toBeNull();
	});

	it.each([
		'bg-primary',
		'hover:bg-primary-hover',
		'text-primary',
		'text-primary-foreground',
		'text-text-primary',
		'bg-primary-soft',
		'var(--color-primary)'
	])('지금 쓰는 이름 %s 는 검출하지 않는다', (source) => {
		expect(source.match(RETIRED_TOKEN_USAGE)).toBeNull();
		expect(source.match(FOCUS_WITH_PRIMARY)).toBeNull();
	});

	it('primary-strong·text-on-primary 가 남아 있지 않다(주요 면은 primary, 그 위 글자는 primary-foreground)', () => {
		expect(findUsage(RETIRED_TOKEN_USAGE)).toEqual([]);
	});

	it('포커스 테두리에는 primary 가 아니라 ring 을 쓴다', () => {
		expect('focus-visible:outline-primary'.match(FOCUS_WITH_PRIMARY)).not.toBeNull();
		expect(findUsage(FOCUS_WITH_PRIMARY)).toEqual([]);
	});
});
