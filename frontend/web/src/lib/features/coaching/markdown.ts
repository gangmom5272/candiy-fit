import { Lexer, type Token, type Tokens } from 'marked';

/**
 * AI 답변을 화면에 그릴 수 있는 작은 구조로 바꾼다 (S15P31A403-191).
 *
 * AI는 문단·목록·`###` 소제목만 쓰도록 안내받지만 지키지 않을 수 있다. 그래서 marked는 문법을 나누는 데만 쓰고
 * HTML 문자열은 만들지 않는다. 아래 종류만 남기고 나머지는 글자로 바꿔, 화면은 Svelte가 글자로만 출력한다.
 * HTML 태그·링크 주소·이미지는 그려지지 않으므로 HTML 삽입과 위험한 URL이 화면에 닿지 않는다.
 */
export type MarkdownInline =
	| { type: 'text'; text: string }
	| { type: 'strong' | 'em'; children: MarkdownInline[] }
	| { type: 'br' }
	/** 화면 전용: 스트리밍 중 글 끝에 붙는 입력 표시. Markdown에서 나오지 않는다 */
	| { type: 'cursor' };

export type TableAlign = 'left' | 'center' | 'right' | null;

export type MarkdownBlock =
	| { type: 'paragraph'; children: MarkdownInline[] }
	/** `###` 이하 소제목 (S15P31A403-271). `#`·`##`는 쓰지 않기로 해서 굵은 문단으로 둔다 */
	| { type: 'heading'; children: MarkdownInline[] }
	| { type: 'list'; ordered: boolean; start: number; items: MarkdownBlock[][] }
	/** 여러 날·여러 지표 비교용 표 (S15P31A403-236). 칸 안은 굵게 등 인라인만 */
	| { type: 'table'; align: TableAlign[]; header: MarkdownInline[][]; rows: MarkdownInline[][][] };

// 줄바꿈 하나를 줄바꿈으로 본다. AI는 지금 줄바꿈 하나로 문장을 나눈다
const lexer = () => new Lexer({ breaks: true, gfm: true });

/** 여러 줄 글자(코드 블록 등)는 줄마다 줄바꿈을 넣은 글자로 */
function textLines(text: string): MarkdownInline[] {
	return text
		.split('\n')
		.flatMap((line, index): MarkdownInline[] => [
			...(index > 0 ? [{ type: 'br' as const }] : []),
			...(line ? [{ type: 'text' as const, text: line }] : [])
		]);
}

function toInline(tokens: Token[] = []): MarkdownInline[] {
	return tokens.flatMap((token): MarkdownInline[] => {
		switch (token.type) {
			case 'strong':
			case 'em':
				return [{ type: token.type, children: toInline((token as Tokens.Strong).tokens) }];
			case 'br':
				return [{ type: 'br' }];
			case 'text':
			case 'link':
			case 'del':
				// 링크는 주소를 버리고 글자만 남긴다
				return 'tokens' in token && token.tokens?.length
					? toInline(token.tokens)
					: textLines((token as Tokens.Text).text);
			case 'image':
				return textLines((token as Tokens.Image).text);
			case 'codespan':
			case 'escape':
				return textLines((token as Tokens.Codespan).text);
			default:
				// HTML 태그 등 예상 밖 요소는 쓴 글자 그대로 보여 준다
				return textLines(token.raw);
		}
	});
}

function paragraph(children: MarkdownInline[]): MarkdownBlock[] {
	return children.length ? [{ type: 'paragraph', children }] : [];
}

function toBlocks(tokens: Token[]): MarkdownBlock[] {
	return tokens.flatMap((token): MarkdownBlock[] => {
		switch (token.type) {
			case 'paragraph':
			case 'text':
				return paragraph(
					'tokens' in token && token.tokens ? toInline(token.tokens) : textLines(token.raw)
				);
			case 'heading': {
				const heading = token as Tokens.Heading;
				const children = toInline(heading.tokens);
				if (!children.length) return [];
				// 큰 제목(`#`·`##`)은 쓰지 않기로 했다. 크기를 키우지 않고 굵은 문단으로만 보여 준다
				return heading.depth >= 3
					? [{ type: 'heading', children }]
					: paragraph([{ type: 'strong', children }]);
			}
			case 'list': {
				const list = token as Tokens.List;
				return [
					{
						type: 'list',
						ordered: list.ordered,
						start: typeof list.start === 'number' ? list.start : 1,
						items: list.items.map((item) => toBlocks(item.tokens))
					}
				];
			}
			case 'blockquote':
				return toBlocks((token as Tokens.Blockquote).tokens);
			case 'table': {
				const table = token as Tokens.Table;
				const cells = (row: Tokens.TableCell[]) => row.map((cell) => toInline(cell.tokens));
				return [
					{
						type: 'table',
						align: table.align,
						header: cells(table.header),
						rows: table.rows.map(cells)
					}
				];
			}
			case 'space':
			case 'hr':
			case 'def':
				return [];
			default:
				// 코드 블록·HTML 블록 등은 쓴 글자 그대로
				return paragraph(textLines('text' in token ? String(token.text) : token.raw));
		}
	});
}

const TABLE_LINE = /^\s*\|/;
const TABLE_DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

/**
 * 글 끝에서 들어오는 중인 표 줄을 뺀다 (S15P31A403-236). 표는 줄이 다 들어온 뒤에만 그린다.
 * - 줄바꿈이 아직 안 온 마지막 표 줄은 칸이 덜 찼을 수 있어 뺀다(`| 10월 24일 | 11,6`)
 * - 머리줄과 구분선(`| --- |`)이 다 오기 전에는 marked가 표가 아니라 문단으로 읽는다.
 *   그대로 두면 `|` 기호째 보이고, 답변 첫 줄이면 요약처럼 크게 보이므로 표 전체를 뺀다
 */
function hideIncompleteTable(content: string): string {
	const lines = content.split('\n');
	// 줄바꿈이 아직 안 온 마지막 줄. 빈 문자열이면 줄이 다 들어온 상태다
	const tail = lines.pop() ?? '';
	if (tail !== '' && !TABLE_LINE.test(tail)) return content;
	let start = lines.length;
	while (start > 0 && TABLE_LINE.test(lines[start - 1])) start--;
	const table = lines.slice(start);
	const kept = table.length >= 2 && TABLE_DELIMITER.test(table[1]) ? lines : lines.slice(0, start);
	return kept.length ? kept.join('\n') + '\n' : '';
}

/**
 * 스트리밍 중 마지막 줄의 덜 쓴 문법을 감춘다. 다 들어오면 원래대로 그려진다.
 * - 짝이 없는 `**`는 지워 `**굵`이 기호째 보였다가 굵게 바뀌며 깜빡이지 않게 한다
 * - 기호만 있는 목록 줄(`-`, `1.`)과 소제목 줄(`###`)은 내용이 올 때까지 빼 둔다
 * - 들어오는 중인 표 줄은 줄이 다 올 때까지 빼 둔다 (hideIncompleteTable)
 */
export function hideIncompleteTail(source: string): string {
	const content = hideIncompleteTable(source);
	const cut = content.lastIndexOf('\n') + 1;
	let last = content.slice(cut);
	if (/^\s*([-*+]|\d+[.)]|#{1,6})\s*$/.test(last)) last = '';
	const bold = last.split('**').length - 1;
	if (bold % 2 === 1) {
		const at = last.lastIndexOf('**');
		last = last.slice(0, at) + last.slice(at + 2);
	}
	return content.slice(0, cut) + last;
}

export function parseMarkdown(content: string, { streaming = false } = {}): MarkdownBlock[] {
	return toBlocks(lexer().lex(streaming ? hideIncompleteTail(content) : content));
}

export type AnswerParts = { headline: MarkdownInline[]; body: MarkdownBlock[] };

/**
 * 첫 블록이 일반 문단이면 그 첫 줄을 답변 요약(큰 글자)으로 뗀다. 목록 등으로 시작하면 요약 없이 본문만이다.
 * 기존 화면(첫 줄 = 요약)과 같은 모양을 유지하면서, 목록 기호가 섞인 큰 제목이 생기지 않게 한다.
 */
export function splitAnswer(blocks: MarkdownBlock[]): AnswerParts {
	const [first, ...rest] = blocks;
	if (first?.type !== 'paragraph') return { headline: [], body: blocks };
	const lineEnd = first.children.findIndex((child) => child.type === 'br');
	if (lineEnd === -1) return { headline: first.children, body: rest };
	const remaining = first.children.slice(lineEnd + 1);
	return {
		headline: first.children.slice(0, lineEnd),
		body: [...paragraph(remaining), ...rest]
	};
}

function inlineText(children: MarkdownInline[]): string {
	return children
		.map((child) =>
			child.type === 'text'
				? child.text
				: child.type === 'strong' || child.type === 'em'
					? inlineText(child.children)
					: child.type === 'br'
						? ' '
						: ''
		)
		.join('');
}

/** 본문 마지막 글자 뒤에 입력 표시를 붙인다. 기존 화면처럼 요약 줄에는 붙이지 않는다 */
export function withCursor({ headline, body }: AnswerParts): AnswerParts {
	const cursor: MarkdownInline = { type: 'cursor' };
	const appendTo = (blocks: MarkdownBlock[]): MarkdownBlock[] | null => {
		const last = blocks.at(-1);
		if (!last) return null;
		if (last.type === 'paragraph' || last.type === 'heading')
			return [...blocks.slice(0, -1), { ...last, children: [...last.children, cursor] }];
		// 표 칸에 붙이면 칸 너비가 흔들리므로 표 아래 줄에 둔다
		if (last.type === 'table') return [...blocks, { type: 'paragraph', children: [cursor] }];
		const lastItem = appendTo(last.items.at(-1) ?? []);
		return lastItem
			? [...blocks.slice(0, -1), { ...last, items: [...last.items.slice(0, -1), lastItem] }]
			: null;
	};
	return { headline, body: appendTo(body) ?? body };
}

function blockText(block: MarkdownBlock): string {
	if (block.type === 'paragraph' || block.type === 'heading') return inlineText(block.children);
	if (block.type === 'table')
		return [block.header, ...block.rows].map((row) => row.map(inlineText).join(' · ')).join(' ');
	return block.items.map((item) => item.map(blockText).join(' ')).join(' ');
}

/** 한 줄 요약처럼 기호 없이 보여야 하는 곳에 쓴다(대화 요약 등). `**`·`- ` 같은 기호를 지운다 */
export function toPlainText(content: string): string {
	return parseMarkdown(content).map(blockText).join(' ').replace(/\s+/g, ' ').trim();
}
