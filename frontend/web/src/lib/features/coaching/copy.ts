/**
 * 대화를 드래그해 복사할 때 꾸밈 요소를 뺀다(S15P31A403-438).
 * 코치 캐릭터 이미지가 함께 복사되어, 붙여 넣는 곳(메신저·노트)이 `![AI 코치](…character-coach.webp)` 처럼
 * 이미지 마크다운으로 바꿔 답 글 앞에 붙었다. `data-copy-skip` 을 단 요소는 복사 내용(HTML)에서 지운다.
 * 꾸밈 요소가 선택에 없으면 브라우저 기본 복사를 그대로 둔다.
 * 글(text/plain)은 브라우저가 고른 선택 글을 그대로 쓴다. 이미지는 글에 들어가지 않는다
 */
export function copyWithoutDecoration(event: ClipboardEvent) {
	const selection = window.getSelection();
	if (!selection || selection.isCollapsed || selection.rangeCount === 0 || !event.clipboardData)
		return;
	const copied = document.createElement('div');
	for (let index = 0; index < selection.rangeCount; index += 1)
		copied.append(selection.getRangeAt(index).cloneContents());
	const decorations = copied.querySelectorAll('[data-copy-skip]');
	if (decorations.length === 0) return;
	for (const decoration of decorations) decoration.remove();
	event.clipboardData.setData('text/html', copied.innerHTML);
	event.clipboardData.setData('text/plain', selection.toString());
	event.preventDefault();
}
