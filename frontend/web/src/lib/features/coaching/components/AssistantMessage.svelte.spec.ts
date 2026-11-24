import { page } from 'vitest/browser';
import { expect, it } from 'vitest';
import { render } from 'vitest-browser-svelte';
import type { CoachingMessage } from '../types';
import AssistantMessage from './AssistantMessage.svelte';

/** 합성 답변. 실제 대화가 아니다 */
function message(content: string, requestStatus: CoachingMessage['requestStatus'] = 'completed') {
	return {
		id: 'a1',
		requestId: 'r1',
		role: 'assistant',
		sequenceNo: 2,
		content,
		requestStatus,
		errorCode: null,
		createdAt: '2026-10-29T01:00:00Z',
		evidence: [],
		errorContext: null
	} satisfies CoachingMessage;
}

const article = () => document.querySelector('article')!;
// 컴포넌트 테스트에는 화면 CSS가 없어 글자 크기 대신 요약 클래스로 확인한다
const headlines = () => article().querySelectorAll('p.text-\\[17px\\]');

it('지금 형식(줄바꿈만 있는 글)은 첫 줄 요약 + 줄바꿈 본문으로 그대로 보인다', async () => {
	render(AssistantMessage, { message: message('요약 문장\n첫째 설명\n둘째 설명') });
	await expect.element(page.getByText('요약 문장')).toBeVisible();
	expect([...headlines()].map((element) => element.textContent)).toEqual(['요약 문장']);
	expect(article().querySelectorAll('br')).toHaveLength(1);
	expect(article().textContent).toContain('첫째 설명');
});

it('목록과 굵게를 그리고, 목록으로 시작하면 큰 요약을 두지 않는다', async () => {
	render(AssistantMessage, { message: message('- 수면 **6시간**\n- 걸음 비슷') });
	await expect.element(page.getByRole('listitem').first()).toBeVisible();
	expect(article().querySelectorAll('li')).toHaveLength(2);
	expect(article().querySelector('strong')?.textContent).toBe('6시간');
	expect(headlines()).toHaveLength(0);
});

it('HTML·위험한 링크·이미지는 요소로 만들지 않고 글자로만 보인다', async () => {
	const unsafe = [
		'안내드릴게요',
		'<img src=x onerror="window.__xss = 1">',
		'<script>window.__xss = 1</script>',
		'[누르기](javascript:window.__xss=1) ![사진](https://x.test/a.png)'
	].join('\n\n');
	render(AssistantMessage, { message: message(unsafe) });
	await expect.element(page.getByText('안내드릴게요')).toBeVisible();
	expect(article().querySelectorAll('script, a, iframe').length).toBe(0);
	// 캐릭터 이미지 하나만 있다
	expect(article().querySelectorAll('img')).toHaveLength(1);
	expect(article().textContent).toContain('<script>window.__xss = 1</script>');
	expect(article().textContent).toContain('누르기');
	expect(article().innerHTML).not.toContain('javascript:');
	expect((window as { __xss?: number }).__xss).toBeUndefined();
});

const weekTable = [
	'최근 7일 활동량이에요.',
	'',
	'| 날짜 | 걸음 | 운동 | 수면 |',
	'| --- | --- | --- | --- |',
	'| 10월 24일 (토) | 11,650걸음 | 43분 | 7시간 12분 |',
	'| 10월 25일 (일) | 5,394걸음 | 0분 | 8시간 5분 |',
	'',
	'일요일만 걸음이 크게 줄었어요.'
].join('\n');

it('표를 실제 표로 그리고 첫 열은 행 머리로 둔다 (S15P31A403-236)', async () => {
	render(AssistantMessage, { message: message(weekTable) });
	const table = page.getByRole('table');
	await expect.element(table).toBeVisible();
	expect(
		page
			.getByRole('columnheader')
			.elements()
			.map((cell) => cell.textContent?.trim())
	).toEqual(['날짜', '걸음', '운동', '수면']);
	expect(
		page
			.getByRole('rowheader')
			.elements()
			.map((cell) => cell.textContent?.trim())
	).toEqual(['10월 24일 (토)', '10월 25일 (일)']);
	expect(article().textContent).not.toContain(' · ');
	expect(article().textContent).not.toContain('|');
	// 표는 가로로 밀어 볼 수 있는 상자 안에 있다. 밀기·키보드 포커스는 화면 CSS가 있는 E2E에서 본다
	await expect.element(page.getByRole('region', { name: '비교 표' })).toBeInTheDocument();
	expect([...headlines()].map((element) => element.textContent)).toEqual([
		'최근 7일 활동량이에요.'
	]);
});

it('표로 시작하는 답변은 큰 요약 줄을 두지 않는다', async () => {
	render(AssistantMessage, { message: message(weekTable.split('\n').slice(2).join('\n')) });
	await expect.element(page.getByRole('table')).toBeVisible();
	expect(headlines()).toHaveLength(0);
});

it('스트리밍 중 반쯤 들어온 표 줄은 그리지 않고, 다 들어온 줄만 표로 그린다', async () => {
	const screen = render(AssistantMessage, {
		message: message('| 날짜 | 걸음 |\n| --- | --- |\n| 10월 24일 | 11,6', 'generating')
	});
	await expect.element(page.getByRole('table')).toBeVisible();
	expect(page.getByRole('row').elements()).toHaveLength(1);
	expect(article().textContent).not.toContain('11,6');
	expect(headlines()).toHaveLength(0);

	await screen.rerender({
		message: message('| 날짜 | 걸음 |\n| --- | --- |\n| 10월 24일 | 11,650걸음 |\n', 'generating')
	});
	await expect.element(page.getByRole('rowheader')).toHaveTextContent('10월 24일');
	expect(article().textContent).toContain('11,650걸음');
});

it('스트리밍 중 머리줄만 들어온 표는 기호째 보이지 않는다', async () => {
	render(AssistantMessage, { message: message('| 날짜 | 걸음 |\n| --', 'generating') });
	await expect.element(page.getByRole('status')).toBeVisible();
	expect(article().textContent).not.toContain('날짜');
	expect(headlines()).toHaveLength(0);
});

it('### 소제목은 본문 사이 소제목으로 그리고, 소제목으로 시작하면 요약 줄을 두지 않는다 (S15P31A403-271)', async () => {
	const screen = render(AssistantMessage, {
		message: message('수면이 짧았어요.\n\n### 수면\n- 평균 6시간\n\n### 걸음\n평소와 비슷해요.')
	});
	await expect.element(page.getByRole('heading', { level: 3, name: '수면' })).toBeVisible();
	expect(
		page
			.getByRole('heading', { level: 3 })
			.elements()
			.map((element) => element.textContent)
	).toEqual(['수면', '걸음']);
	expect(article().textContent).not.toContain('#');
	expect([...headlines()].map((element) => element.textContent)).toEqual(['수면이 짧았어요.']);

	await screen.rerender({ message: message('### 수면\n평균 6시간이에요.') });
	await expect.element(page.getByRole('heading', { level: 3, name: '수면' })).toBeVisible();
	expect(headlines()).toHaveLength(0);
});

it('스트리밍 중 기호만 들어온 소제목 줄은 빈 제목으로 보이지 않는다', async () => {
	const screen = render(AssistantMessage, { message: message('요약\n\n### ', 'generating') });
	await expect.element(page.getByText('요약')).toBeVisible();
	expect(page.getByRole('heading', { level: 3 }).elements()).toHaveLength(0);
	expect(article().textContent).not.toContain('#');

	await screen.rerender({ message: message('요약\n\n### 수', 'generating') });
	await expect.element(page.getByRole('heading', { level: 3 })).toHaveTextContent(/^수/);
});

it('스트리밍 중에는 덜 쓴 ** 기호를 감추고 끝에 입력 표시를 붙인다', async () => {
	render(AssistantMessage, { message: message('요약\n수면은 **6시', 'generating') });
	await expect.element(page.getByText(/수면은 6시/)).toBeVisible();
	expect(article().textContent).not.toContain('**');
	expect(article().textContent?.trim().endsWith('|')).toBe(true);
});
