import { expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { render } from 'vitest-browser-svelte';
import loginIcon from '#lib/assets/icons/icon-app-login.svg';
import healthSyncIcon from '#lib/assets/icons/icon-app-health-sync.svg';
import webReturnIcon from '#lib/assets/icons/icon-web-return.svg';
import AppHeader from '#lib/components/layout/AppHeader.svelte';
import AppLinkGuide from './AppLinkGuide.svelte';
import '../../../../routes/layout.css';

vi.mock('$app/state', () => ({ page: { url: new URL('http://localhost/home'), status: 200 } }));

it('안내 제목과 순서가 있는 비대화형 3단계를 제공한다', async () => {
	render(AppLinkGuide);
	await expect
		.element(page.getByRole('heading', { level: 1 }))
		.toHaveTextContent('건강 기록을 보려면 먼저 앱 연동이 필요해요');
	await expect
		.element(
			page.getByText(
				'모바일 앱에서 워치와 건강 데이터를 연동하면 웹에서도 건강 기록을 확인할 수 있어요.',
				{ exact: true }
			)
		)
		.toBeVisible();
	const steps = page.getByRole('list', { name: '앱 연동 3단계' });
	await expect.element(steps.getByRole('listitem')).toHaveLength(3);
	for (const [index, { label, icon }] of [
		{ label: 'STEP 01', icon: loginIcon },
		{ label: 'STEP 02', icon: healthSyncIcon },
		{ label: 'STEP 03', icon: webReturnIcon }
	].entries()) {
		const step = steps.getByRole('listitem').nth(index);
		await expect.element(step.getByText(label, { exact: true })).toBeVisible();
		await expect.element(step.element().querySelector('img')).toHaveAttribute('src', icon);
	}
	for (const title of ['앱 설치 및 로그인', '건강 데이터 연동', '웹으로 돌아오기']) {
		await expect.element(steps.getByRole('heading', { name: title })).toBeVisible();
	}
	for (const description of [
		'웹에서 사용한 계정으로 앱에 로그인해요.',
		'앱에서 워치와 건강 데이터를 연결해요.',
		'연동을 마쳤다면 웹으로 돌아와 새로고침하면 돼요.'
	]) {
		await expect.element(steps.getByText(description, { exact: true })).toBeVisible();
	}
	await expect.element(page.getByText('앱 연동이 필요해요', { exact: true })).toBeVisible();
	expect(document.querySelectorAll('main a, main button, main [tabindex]')).toHaveLength(0);
	expect(document.querySelectorAll('main img[alt=""]')).toHaveLength(4);
	const note = page.getByText('첫 건강 기록이 표시되기까지는 시간이 조금 걸릴 수 있어요.', {
		exact: true
	});
	await expect.element(note).toBeVisible();
	expect(note.element().closest('ol')).toBeNull();
});

it.each([360, 390, 768])(
	'%ipx: 안내 문구와 왼쪽 아이콘·오른쪽 설명으로 읽기 흐름을 유지한다',
	async (width) => {
		const previous = { width: innerWidth, height: innerHeight };
		await page.viewport(width, 800);
		await vi.waitFor(() => {
			expect(innerWidth).toBe(width);
			expect(innerHeight).toBe(800);
		});
		const guide = render(AppLinkGuide);
		try {
			await document.fonts.ready;
			await Promise.all(
				Array.from(guide.container.querySelectorAll('img')).map((img) => img.decode())
			);
			const title = page.getByRole('heading', { level: 1 }).element();
			expect(getComputedStyle(title).fontSize).toBe('30px');
			expect(getComputedStyle(title).lineHeight).toBe('39px');
			const illustration = guide.container.querySelector('section > img');
			expect(illustration).not.toBeNull();
			expect(illustration?.getBoundingClientRect().width).toBeLessThanOrEqual(
				width < 768 ? 320 : 440
			);
			const cards = Array.from(guide.container.querySelectorAll('li'));
			expect(cards).toHaveLength(3);
			for (const card of cards) {
				const icon = card.querySelector('img');
				const heading = card.querySelector('h2');
				expect(icon).not.toBeNull();
				expect(heading).not.toBeNull();
				if (!icon || !heading) continue;
				expect(icon.getBoundingClientRect().right).toBeLessThanOrEqual(
					heading.getBoundingClientRect().left
				);
				for (const text of card.querySelectorAll('h2, p')) {
					expect(text.scrollWidth).toBeLessThanOrEqual(text.clientWidth);
					expect(text.scrollHeight).toBeLessThanOrEqual(text.clientHeight);
				}
			}
			const bounds = cards.map((card) => card.getBoundingClientRect());
			expect(bounds[0].bottom).toBeLessThan(bounds[1].top);
			expect(bounds[1].bottom).toBeLessThan(bounds[2].top);
			expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(innerWidth);
		} finally {
			await guide.unmount();
			await page.viewport(previous.width, previous.height);
		}
	}
);

for (const width of [360, 390, 640, 768, 1024, 1366, 1920]) {
	it(`${width}px: 가로 넘침 없이 원본 이미지 비율과 단계 배치를 유지한다`, async () => {
		const previous = { width: innerWidth, height: innerHeight };
		await page.viewport(width, 1080);
		await vi.waitFor(() => {
			expect(innerWidth).toBe(width);
			expect(innerHeight).toBe(1080);
		});
		const header = render(AppHeader);
		const { container, unmount } = render(AppLinkGuide);
		try {
			await document.fonts.ready;
			const images = Array.from(container.querySelectorAll('img'));
			await Promise.all(images.map((img) => img.decode()));
			expect(images).toHaveLength(4);
			for (const img of images) {
				expect(img.naturalWidth).toBeGreaterThan(0);
				const rect = img.getBoundingClientRect();
				expect(rect.width / rect.height).toBeCloseTo(img.naturalWidth / img.naturalHeight, 2);
				expect(rect.left).toBeGreaterThanOrEqual(0);
				expect(rect.right).toBeLessThanOrEqual(innerWidth);
			}
			expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(innerWidth);
			const cards = Array.from(container.querySelectorAll('li')).map((el) =>
				el.getBoundingClientRect()
			);
			if (width >= 1024) {
				expect(cards[0].top).toBe(cards[2].top);
				expect(cards[0].right).toBeLessThan(cards[1].left);
				const headings = Array.from(container.querySelectorAll('li h2')).map((el) =>
					el.getBoundingClientRect()
				);
				expect(headings).toHaveLength(3);
				expect(headings[0].top).toBe(headings[1].top);
				expect(headings[1].top).toBe(headings[2].top);
			} else {
				expect(cards[0].bottom).toBeLessThan(cards[1].top);
				expect(cards[1].bottom).toBeLessThan(cards[2].top);
			}
			await page.screenshot({
				path: `../../../../../test-results/app-link-guide/${width}.png`
			});
		} finally {
			await unmount();
			await header.unmount();
			await page.viewport(previous.width, previous.height);
		}
	});
}

it.each([
	[1024, 768],
	[1280, 720],
	[1366, 768],
	[1440, 900],
	[1536, 864],
	[1920, 1080]
])('%i×%i: 읽기 크기를 유지하면서 안내를 세로 스크롤 없이 표시한다', async (width, height) => {
	const previous = { width: innerWidth, height: innerHeight };
	await page.viewport(width, height);
	await vi.waitFor(() => {
		expect(innerWidth).toBe(width);
		expect(innerHeight).toBe(height);
	});
	const header = render(AppHeader);
	const guide = render(AppLinkGuide);
	try {
		await document.fonts.ready;
		await vi.waitFor(() => {
			expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(innerHeight);
		});
		const panel = guide.container.querySelector('section');
		expect(panel).not.toBeNull();
		if (!panel) return;
		const bounds = panel.getBoundingClientRect();
		expect(getComputedStyle(panel).zoom).toBe('1');
		expect(getComputedStyle(panel).transform).toBe('none');
		expect(bounds.width / panel.offsetWidth).toBeCloseTo(1, 2);
		expect(bounds.height / panel.offsetHeight).toBeCloseTo(1, 2);
		const title = panel.querySelector('h1');
		expect(title).not.toBeNull();
		if (title) {
			expect(getComputedStyle(title).fontSize).toBe('30px');
			expect(getComputedStyle(title).lineHeight).toBe('39px');
		}
		const description = panel.querySelector('h1 + p');
		expect(description).not.toBeNull();
		if (description) {
			expect(getComputedStyle(description).fontSize).toBe('16px');
			expect(getComputedStyle(description).lineHeight).toBe('26px');
		}
		for (const step of panel.querySelectorAll('li')) {
			const heading = step.querySelector('h2');
			expect(heading).not.toBeNull();
			if (heading) expect(getComputedStyle(heading).fontSize).toBe('18px');
			for (const text of step.querySelectorAll('p')) {
				expect(getComputedStyle(text).fontSize).toBe('14px');
			}
		}
		expect(bounds.bottom).toBeLessThanOrEqual(innerHeight);
		const finalStep = guide.container.querySelector('li:last-child');
		expect(finalStep?.getBoundingClientRect().bottom).toBeLessThanOrEqual(innerHeight);
		expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(innerWidth);
	} finally {
		await guide.unmount();
		await header.unmount();
		await page.viewport(previous.width, previous.height);
	}
});

it('화면 높이 변경 시 글자와 단계 카드의 크기를 유지하고 일러스트만 줄인다', async () => {
	const previous = { width: innerWidth, height: innerHeight };
	await page.viewport(1366, 1080);
	await vi.waitFor(() => {
		expect(innerWidth).toBe(1366);
		expect(innerHeight).toBe(1080);
	});
	const header = render(AppHeader);
	const guide = render(AppLinkGuide);
	try {
		await document.fonts.ready;
		const text = Array.from(guide.container.querySelectorAll('h1, h2, p'));
		expect(text.length).toBeGreaterThan(0);
		const beforeTypography = text.map((el) => ({
			size: getComputedStyle(el).fontSize,
			lineHeight: getComputedStyle(el).lineHeight
		}));
		const cards = Array.from(guide.container.querySelectorAll('li'));
		expect(cards).toHaveLength(3);
		const beforeCards = cards.map((el) => el.getBoundingClientRect());
		const illustration = guide.container.querySelector('section > img');
		expect(illustration).not.toBeNull();
		const beforeImage = illustration?.getBoundingClientRect();
		await page.viewport(1366, 768);
		await vi.waitFor(() => {
			expect(innerWidth).toBe(1366);
			expect(innerHeight).toBe(768);
		});
		await vi.waitFor(() => {
			expect(document.documentElement.scrollHeight).toBeLessThanOrEqual(innerHeight);
		});
		const afterTypography = text.map((el) => ({
			size: getComputedStyle(el).fontSize,
			lineHeight: getComputedStyle(el).lineHeight
		}));
		expect(afterTypography).toEqual(beforeTypography);
		const afterCards = cards.map((el) => el.getBoundingClientRect());
		for (let index = 0; index < cards.length; index++) {
			expect(afterCards[index].width).toBeCloseTo(beforeCards[index].width, 2);
			expect(afterCards[index].height).toBeCloseTo(beforeCards[index].height, 2);
		}
		expect(illustration?.getBoundingClientRect().width).toBeLessThan(beforeImage?.width ?? 0);
		expect(illustration?.getBoundingClientRect().height).toBeLessThan(beforeImage?.height ?? 0);
	} finally {
		await guide.unmount();
		await header.unmount();
		await page.viewport(previous.width, previous.height);
	}
});

it('높이가 특히 작은 화면에서는 글자를 축소하지 않고 모든 내용을 스크롤로 읽을 수 있다', async () => {
	const previous = { width: innerWidth, height: innerHeight };
	await page.viewport(1366, 480);
	await vi.waitFor(() => {
		expect(innerWidth).toBe(1366);
		expect(innerHeight).toBe(480);
	});
	const header = render(AppHeader);
	const guide = render(AppLinkGuide);
	try {
		await document.fonts.ready;
		expect(document.documentElement.scrollHeight).toBeGreaterThan(innerHeight);
		const title = page.getByRole('heading', { level: 1 }).element();
		expect(getComputedStyle(title).fontSize).toBe('30px');
		const panel = guide.container.querySelector('section');
		expect(panel).not.toBeNull();
		if (panel) expect(getComputedStyle(panel).zoom).toBe('1');
		const note = page.getByText('첫 건강 기록이 표시되기까지는 시간이 조금 걸릴 수 있어요.', {
			exact: true
		});
		note.element().scrollIntoView({ block: 'center' });
		await vi.waitFor(() => {
			expect(note.element().getBoundingClientRect().bottom).toBeLessThanOrEqual(innerHeight);
			expect(note.element().getBoundingClientRect().top).toBeGreaterThanOrEqual(
				header.container.querySelector('header')?.getBoundingClientRect().bottom ?? Infinity
			);
		});
		await expect.element(note).toBeVisible();
		for (const text of guide.container.querySelectorAll('h1, h2, p')) {
			expect(text.scrollHeight).toBeLessThanOrEqual(text.clientHeight);
		}
	} finally {
		await guide.unmount();
		await header.unmount();
		await page.viewport(previous.width, previous.height);
	}
});

it('공통 Header와 200% CSS 확대에서도 제목과 단계가 겹치지 않고 키보드 탐색을 유지한다', async () => {
	const previous = { width: innerWidth, height: innerHeight };
	await page.viewport(1366, 768);
	const header = render(AppHeader);
	const guide = render(AppLinkGuide);
	const originalZoom = document.documentElement.style.zoom;
	try {
		document.documentElement.style.zoom = '2';
		await document.fonts.ready;
		const panel = guide.container.querySelector('section');
		expect(panel).not.toBeNull();
		if (panel) expect(getComputedStyle(panel).zoom).toBe('1');
		await expect.element(page.getByRole('heading', { level: 1 })).toBeVisible();
		const main = guide.container.querySelector('main');
		const headerElement = header.container.querySelector('header');
		expect(main?.getBoundingClientRect().top).toBeGreaterThanOrEqual(
			headerElement?.getBoundingClientRect().bottom ?? Infinity
		);
		expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(innerWidth);
		for (const el of guide.container.querySelectorAll('h1, h2, li, p')) {
			const bounds = el.getBoundingClientRect();
			expect(bounds.left).toBeGreaterThanOrEqual(0);
			expect(bounds.right).toBeLessThanOrEqual(innerWidth);
			expect(el.scrollHeight).toBeLessThanOrEqual(el.clientHeight);
		}
		const homeLink = header.container.querySelector<HTMLAnchorElement>('a[href="/home"]');
		expect(homeLink).not.toBeNull();
		homeLink?.focus();
		await userEvent.tab();
		expect(header.container.contains(document.activeElement)).toBe(true);
		await page.screenshot({ path: '../../../../../test-results/app-link-guide/zoom-200.png' });
	} finally {
		document.documentElement.style.zoom = originalZoom;
		await header.unmount();
		await guide.unmount();
		await page.viewport(previous.width, previous.height);
	}
});
