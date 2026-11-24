import { expect, test } from '@playwright/test';

test('Paperlogy 9개 굵기를 WOFF2로 한 번씩 받아 정상 디코딩한다', async ({ page }) => {
	const requests: { url: string; status: number }[] = [];
	page.on('response', (response) => {
		if (response.request().resourceType() === 'font')
			requests.push({ url: response.url(), status: response.status() });
	});
	await page.goto('/login');
	const faces = await page.evaluate(async () => {
		const loaded = [];
		for (let weight = 100; weight <= 900; weight += 100) {
			const fonts = await document.fonts.load(`${weight} 16px Paperlogy`, '건강 기록 ABC 0123');
			loaded.push(
				fonts.map((font) => ({ family: font.family, weight: font.weight, status: font.status }))
			);
		}
		return loaded;
	});
	expect(faces).toEqual(
		Array.from({ length: 9 }, (_, index) => [
			{ family: 'Paperlogy', weight: String((index + 1) * 100), status: 'loaded' }
		])
	);
	expect(requests).toHaveLength(9);
	expect(new Set(requests.map((request) => request.url)).size).toBe(9);
	expect(
		requests.every(
			(request) => request.status === 200 && new URL(request.url).pathname.endsWith('.woff2')
		)
	).toBe(true);
});
