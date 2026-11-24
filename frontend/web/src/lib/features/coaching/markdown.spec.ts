import { describe, expect, it } from 'vitest';
import {
	hideIncompleteTail,
	parseMarkdown,
	splitAnswer,
	toPlainText,
	withCursor
} from './markdown';

const text = (value: string) => ({ type: 'text', text: value });

describe('parseMarkdown', () => {
	it('줄바꿈 하나를 줄바꿈으로, 빈 줄을 문단으로 나눈다 (breaks)', () => {
		expect(parseMarkdown('첫 줄\n둘째 줄\n\n새 문단')).toEqual([
			{ type: 'paragraph', children: [text('첫 줄'), { type: 'br' }, text('둘째 줄')] },
			{ type: 'paragraph', children: [text('새 문단')] }
		]);
	});

	it('목록과 굵게를 남긴다', () => {
		expect(parseMarkdown('- 수면 **6시간**\n- 걸음')).toEqual([
			{
				type: 'list',
				ordered: false,
				start: 1,
				items: [
					[
						{
							type: 'paragraph',
							children: [text('수면 '), { type: 'strong', children: [text('6시간')] }]
						}
					],
					[{ type: 'paragraph', children: [text('걸음')] }]
				]
			}
		]);
		expect(parseMarkdown('3. 셋째')[0]).toMatchObject({ type: 'list', ordered: true, start: 3 });
	});

	it('HTML 태그는 그리지 않고 쓴 글자 그대로 둔다', () => {
		const json = JSON.stringify(parseMarkdown('<script>alert(1)</script>\n\n안녕 <b>굵게</b>'));
		expect(json).toContain('<script>alert(1)</script>');
		expect(json).not.toMatch(/"type":"(html|script)"/);
		expect(parseMarkdown('<img src=x onerror=alert(1)>')).toEqual([
			{ type: 'paragraph', children: [text('<img src=x onerror=alert(1)>')] }
		]);
	});

	it('링크는 주소를 버리고 글자만, 이미지는 대체 글만 남긴다', () => {
		const blocks = parseMarkdown('[누르기](javascript:alert(1)) ![사진](https://x.test/a.png)');
		expect(JSON.stringify(blocks)).not.toContain('javascript');
		expect(JSON.stringify(blocks)).not.toContain('https://');
		expect(toPlainText('[누르기](javascript:alert(1)) ![사진](x.png)')).toBe('누르기 사진');
	});

	it('큰 제목(#·##)은 굵은 문단으로, ### 이하는 소제목으로, 코드는 글자로 바꾼다', () => {
		expect(parseMarkdown('## 요약')).toEqual([
			{ type: 'paragraph', children: [{ type: 'strong', children: [text('요약')] }] }
		]);
		expect(parseMarkdown('### 수면\n#### 걸음')).toEqual([
			{ type: 'heading', children: [text('수면')] },
			{ type: 'heading', children: [text('걸음')] }
		]);
		expect(parseMarkdown('### ')).toEqual([]);
		expect(toPlainText('### 수면\n평균 6시간')).toBe('수면 평균 6시간');
		expect(parseMarkdown('```\na\nb\n```')).toEqual([
			{ type: 'paragraph', children: [text('a'), { type: 'br' }, text('b')] }
		]);
	});

	it('표는 머리줄·행·정렬을 남기고 칸 안의 굵게도 그린다 (S15P31A403-236)', () => {
		const blocks = parseMarkdown(
			[
				'최근 7일 활동량이에요.',
				'',
				'| 날짜 | 걸음 | 운동 |',
				'| --- | ---: | --- |',
				'| 10월 24일 (토) | **11,650걸음** | 43분 |',
				'| 10월 25일 (일) | 5,394걸음 | 0분 |',
				'',
				'일요일만 걸음이 크게 줄었어요.'
			].join('\n')
		);
		expect(blocks.map((block) => block.type)).toEqual(['paragraph', 'table', 'paragraph']);
		expect(blocks[1]).toEqual({
			type: 'table',
			align: [null, 'right', null],
			header: [[text('날짜')], [text('걸음')], [text('운동')]],
			rows: [
				[
					[text('10월 24일 (토)')],
					[{ type: 'strong', children: [text('11,650걸음')] }],
					[text('43분')]
				],
				[[text('10월 25일 (일)')], [text('5,394걸음')], [text('0분')]]
			]
		});
	});

	it('표 칸의 HTML·링크도 요소가 아니라 글자로 남긴다', () => {
		const json = JSON.stringify(
			parseMarkdown('| 항목 | 값 |\n|---|---|\n| <b>걸음</b> | [보기](javascript:alert(1)) |')
		);
		expect(json).toContain('<b>');
		expect(json).not.toContain('javascript');
	});

	it('잘못된 문법도 오류 없이 글자로 남긴다', () => {
		for (const broken of ['**닫히지 않음', '- ', '[링크(', '***', '<div', '> > >', '1.']) {
			expect(() => parseMarkdown(broken)).not.toThrow();
		}
		expect(toPlainText('**닫히지 않음')).toBe('**닫히지 않음');
	});
});

describe('스트리밍', () => {
	it('마지막 줄의 짝 없는 **는 감추고, 짝이 맞으면 그대로 둔다', () => {
		expect(hideIncompleteTail('수면은 **6시')).toBe('수면은 6시');
		expect(hideIncompleteTail('수면은 **6시간** 이에요')).toBe('수면은 **6시간** 이에요');
		// 앞 줄은 건드리지 않는다
		expect(hideIncompleteTail('**완성**\n다음 **')).toBe('**완성**\n다음 ');
	});

	it('기호만 있는 목록 줄은 내용이 올 때까지 뺀다', () => {
		expect(hideIncompleteTail('정리하면\n- ')).toBe('정리하면\n');
		expect(hideIncompleteTail('정리하면\n2.')).toBe('정리하면\n');
		expect(hideIncompleteTail('정리하면\n- 수면')).toBe('정리하면\n- 수면');
		expect(hideIncompleteTail('정리하면\n\n### ')).toBe('정리하면\n\n');
		expect(hideIncompleteTail('정리하면\n\n###')).toBe('정리하면\n\n');
		expect(hideIncompleteTail('정리하면\n\n### 수')).toBe('정리하면\n\n### 수');
	});

	it('들어오는 중인 표 줄은 줄이 다 올 때까지 뺀다 (S15P31A403-236)', () => {
		const header = '최근 7일이에요.\n\n| 날짜 | 걸음 |\n';
		// 칸이 덜 찬 행
		expect(hideIncompleteTail(header + '| --- | --- |\n| 10월 24일 | 11,6')).toBe(
			header + '| --- | --- |\n'
		);
		// 구분선이 오기 전에는 표 전체를 뺀다 (문단으로 읽혀 `|`가 보이므로)
		expect(hideIncompleteTail(header)).toBe('최근 7일이에요.\n\n');
		expect(hideIncompleteTail(header + '| --- | -')).toBe('최근 7일이에요.\n\n');
		expect(hideIncompleteTail('| 날짜 | 걸')).toBe('');
		// 줄이 다 온 행은 그대로
		const done = header + '| --- | --- |\n| 10월 24일 | 11,650걸음 |\n';
		expect(hideIncompleteTail(done)).toBe(done);
		// 표가 아닌 글은 건드리지 않는다
		expect(hideIncompleteTail('걸음은 3 | 4 정도')).toBe('걸음은 3 | 4 정도');
	});

	it('표로 시작하는 답변도 머리줄이 문단(요약)으로 보이지 않는다', () => {
		for (const partial of ['| 날짜 | 걸음 |', '| 날짜 | 걸음 |\n', '| 날짜 | 걸음 |\n| --- |']) {
			expect(splitAnswer(parseMarkdown(partial, { streaming: true }))).toEqual({
				headline: [],
				body: []
			});
		}
		const started = parseMarkdown('| 날짜 | 걸음 |\n| --- | --- |\n', { streaming: true });
		expect(splitAnswer(started).headline).toEqual([]);
		expect(started[0]).toMatchObject({ type: 'table', rows: [] });
	});

	it('streaming이면 감춘 뒤 나눈다', () => {
		expect(toPlainText('**굵')).toBe('**굵');
		expect(JSON.stringify(parseMarkdown('**굵', { streaming: true }))).not.toContain('**');
	});
});

describe('splitAnswer', () => {
	it('첫 블록이 문단이면 첫 줄을 요약으로 뗀다', () => {
		const { headline, body } = splitAnswer(parseMarkdown('요약 문장\n자세한 설명\n\n- 목록'));
		expect(headline).toEqual([text('요약 문장')]);
		expect(body.map((block) => block.type)).toEqual(['paragraph', 'list']);
	});

	it('한 줄뿐이면 전체가 요약이다 (기존과 같다)', () => {
		expect(splitAnswer(parseMarkdown('한 줄 답변'))).toEqual({
			headline: [text('한 줄 답변')],
			body: []
		});
	});

	it('목록으로 시작하면 요약 없이 본문만 둔다', () => {
		const blocks = parseMarkdown('- 수면\n- 걸음');
		expect(splitAnswer(blocks)).toEqual({ headline: [], body: blocks });
	});
});

describe('toPlainText', () => {
	it('대화 요약처럼 한 줄로 보여 줄 글의 기호를 지운다', () => {
		expect(toPlainText('**수면**이 늘었어요.\n- 걸음은 비슷해요')).toBe(
			'수면이 늘었어요. 걸음은 비슷해요'
		);
		expect(toPlainText('지난주 걸음 기록 일수를 확인했어요.')).toBe(
			'지난주 걸음 기록 일수를 확인했어요.'
		);
		// 한 줄 요약에서는 표를 칸 사이 " · "로 이어 보여 준다
		expect(toPlainText('| 날짜 | 걸음 |\n|---|---|\n| 10.27 | 8240 |')).toBe(
			'날짜 · 걸음 10.27 · 8240'
		);
	});
});

describe('withCursor', () => {
	it('표로 끝나면 입력 표시를 표 아래 줄에 둔다', () => {
		const { body } = withCursor(
			splitAnswer(parseMarkdown('| a | b |\n|---|---|\n| 1 | 2 |\n', { streaming: true }))
		);
		expect(body.map((block) => block.type)).toEqual(['table', 'paragraph']);
		expect(body[1]).toEqual({ type: 'paragraph', children: [{ type: 'cursor' }] });
	});
});
