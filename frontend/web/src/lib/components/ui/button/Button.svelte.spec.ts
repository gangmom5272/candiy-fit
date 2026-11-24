import { createRawSnippet } from 'svelte';
import { expect, it, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import Button from './Button.svelte';

const label = (text: string) => createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));

it('기본은 type="button" 인 button 이고 누르면 onclick 이 불린다', async () => {
	const onclick = vi.fn();
	render(Button, { children: label('저장하기'), onclick });
	const button = page.getByRole('button', { name: '저장하기' });
	await expect.element(button).toHaveAttribute('type', 'button');
	await expect.element(button).toHaveAttribute('data-slot', 'button');
	await button.click();
	expect(onclick).toHaveBeenCalledOnce();
});

it('type · aria 속성 · class 를 그대로 넘기고 class 는 모양 뒤에 덧붙인다', async () => {
	render(Button, {
		children: label('가입하기'),
		type: 'submit',
		variant: 'outline',
		size: 'lg',
		class: 'w-full',
		'aria-busy': true
	});
	const button = page.getByRole('button', { name: '가입하기' });
	await expect.element(button).toHaveAttribute('type', 'submit');
	await expect.element(button).toHaveAttribute('aria-busy', 'true');
	await expect.element(button).toHaveClass('w-full');
	await expect.element(button).toHaveClass('border-border');
	await expect.element(button).toHaveClass('min-h-13');
});

it('disabled 면 누를 수 없다', async () => {
	const onclick = vi.fn();
	render(Button, { children: label('다시 시도'), disabled: true, onclick });
	const button = page.getByRole('button', { name: '다시 시도' });
	await expect.element(button).toBeDisabled();
	await button.click({ force: true });
	expect(onclick).not.toHaveBeenCalled();
});

it('href 를 주면 같은 모양의 링크가 된다', async () => {
	render(Button, { children: label('로그인 화면으로'), href: '/login', size: 'lg' });
	const link = page.getByRole('link', { name: '로그인 화면으로' });
	await expect.element(link).toHaveAttribute('href', '/login');
	await expect.element(link).toHaveAttribute('data-slot', 'button');
	await expect.element(link).toHaveClass('bg-primary');
	await expect.element(link).not.toHaveAttribute('type');
	await expect.element(page.getByRole('button')).not.toBeInTheDocument();
});

it('누를 수 없는 링크는 주소를 빼고 탭 순서에서 뺀다', async () => {
	render(Button, { children: label('로그인 화면으로'), href: '/login', disabled: true });
	const link = page.getByRole('link', { name: '로그인 화면으로' });
	await expect.element(link).not.toHaveAttribute('href');
	await expect.element(link).toHaveAttribute('aria-disabled', 'true');
	await expect.element(link).toHaveAttribute('tabindex', '-1');
});
