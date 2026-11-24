<script module lang="ts">
	/**
	 * 질문을 보낸 시각. 첫 화면에서 질문하면 대화 화면으로 넘어가며 입력창이 새로 생기는데,
	 * 그 사이 다른 곳을 누르지 않았다면 새 입력창도 커서를 이어받게 한다.
	 * 접수가 늦거나 거절되어 넘어가지 않은 경우가 나중 화면에 영향을 주지 않게 잠깐만 유효하다
	 */
	let submittedAt = 0;
	const CARRY_FOCUS_MS = 10_000;

	function markSubmitted() {
		submittedAt = Date.now();
	}

	/** 방금 질문을 보내고 넘어왔는지. 한 번 확인하면 지운다 */
	function takeCarryFocus(): boolean {
		const carry = Date.now() - submittedAt < CARRY_FOCUS_MS;
		submittedAt = 0;
		return carry;
	}
</script>

<script lang="ts">
	type Props = {
		value?: string;
		/** `large`: 첫 화면, `default`: 대화 화면 */
		size?: 'large' | 'default';
		isGenerating?: boolean;
		onsubmit?: (content: string) => void;
	};

	/** 명세 7.3: 앞뒤 공백 제거 후 1~2,000 코드 포인트 */
	const MAX_LENGTH = 2000;
	/** 안내 문구. 칸에 다 들어가지 않으면 짧은 문구를 쓴다(휴대폰 대화 화면은 칸이 180px 안팎) */
	const PLACEHOLDER = '건강 기록에 대해 궁금한 점을 입력해 보세요.';
	const PLACEHOLDER_SHORT = '궁금한 점을 물어보세요.';

	let { value = $bindable(''), size = 'default', isGenerating = false, onsubmit }: Props = $props();

	const inputId = $props.id();
	const content = $derived(value.trim());
	const length = $derived(Array.from(content).length);
	const isTooLong = $derived(length > MAX_LENGTH);
	const canSubmit = $derived(!isGenerating && length > 0 && !isTooLong);

	let input: HTMLInputElement | undefined = $state();

	// 직전 화면에서 질문을 보내고 넘어왔으면 커서를 이어받는다(한 번만). SvelteKit 은 화면을 옮긴 뒤
	// 포커스를 처음으로 되돌리는데, autofocus 가 붙은 요소가 있으면 그 요소로 옮긴다
	const carryFocus = takeCarryFocus();
	let longFits = $state(true);
	const placeholder = $derived(
		isGenerating ? '답변을 작성하고 있어요' : longFits ? PLACEHOLDER : PLACEHOLDER_SHORT
	);

	/** 긴 안내 문구가 칸에 들어가는지 잰다. 입력 칸은 한 줄이라 넘치면 문구 끝이 잘린다 */
	function measure() {
		if (!input) return;
		const style = getComputedStyle(input);
		const context = document.createElement('canvas').getContext('2d');
		if (!context) return;
		context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
		longFits = context.measureText(PLACEHOLDER).width <= input.clientWidth;
	}

	// 화면 폭이 바뀌면 칸 너비가 달라지고, 웹폰트가 늦게 적용되면 글자 폭이 달라져 다시 잰다
	$effect(() => {
		if (!input) return;
		let active = true;
		const observer = new ResizeObserver(measure);
		observer.observe(input);
		document.fonts?.ready.then(() => active && measure());
		return () => {
			active = false;
			observer.disconnect();
		};
	});

	function handleSubmit(event: SubmitEvent) {
		event.preventDefault();
		// 전송 처리가 연결되지 않은 화면에서는 입력한 질문을 지우지 않는다
		if (!canSubmit || !onsubmit) return;
		onsubmit(content);
		value = '';
		// 보내기 버튼을 눌렀어도 커서는 입력창에 둔다. 답변을 기다리는 동안에도 잠긴 채 커서가 남아 바로 다음 질문을 쓸 수 있다
		input?.focus();
		markSubmitted();
	}
</script>

<!--
	좁은 화면(lg 미만)은 대화 상자가 작아 입력창을 낮게 둔다(안쪽 여백·보내기 버튼을 줄임). 글자 크기 16px은 유지한다
	(iOS는 16px보다 작은 입력칸에 커서를 두면 화면을 확대한다). 넓은 화면은 Figma 값 그대로다.
	입력칸에 포커스가 가면 틀의 선 색을 포커스 색으로 바꾼다(S15P31A403-439). 입력칸은 테두리를 없앤 채(outline-none) 대신 넣은
	표시가 없어, Tab 으로 지나갈 때 양옆 버튼에는 테두리가 뜨는데 입력칸에서만 표시가 사라졌다. 틀 밖에 테두리를 한 겹 더 두는
	모양 대신 선 색만 바꿔 크기가 달라지지 않게 했다. 보내기 버튼에 포커스가 있을 때는 버튼의 테두리만 보이게 입력칸일 때만 바꾼다
-->
<form
	class={[
		'flex items-center gap-2 rounded-2xl border border-border bg-card has-[input:focus-visible]:border-ring lg:gap-3',
		size === 'large'
			? 'py-2 pr-2 pl-5 lg:py-3 lg:pr-3 lg:pl-6'
			: 'py-1.5 pr-2 pl-4 lg:py-2.5 lg:pr-3 lg:pl-5'
	]}
	onsubmit={handleSubmit}
>
	<label for={inputId} class="sr-only">질문 입력</label>
	<!-- 짧은 문구도 안 들어가는 아주 좁은 칸(320px 화면)에서는 끝을 "…"으로 줄인다.
	     답변을 쓰는 동안은 disabled 대신 readonly 로 잠근다. disabled 는 커서를 빼 버려 답변 뒤 다시 눌러야 한다 -->
	<!-- svelte-ignore a11y_autofocus (사용자가 방금 질문을 보낸 입력창이라 커서를 옮기지 않고 이어 둔다) -->
	<input
		bind:this={input}
		id={inputId}
		class={[
			'min-w-0 flex-1 bg-transparent text-ellipsis text-text-primary outline-none placeholder:text-text-secondary',
			size === 'large' ? 'text-[15px]' : 'text-base'
		]}
		bind:value
		readonly={isGenerating}
		aria-disabled={isGenerating}
		autofocus={carryFocus}
		{placeholder}
		aria-invalid={isTooLong}
		autocomplete="off"
	/>
	{#if isTooLong}
		<p class="shrink-0 text-xs text-danger" aria-live="polite">
			{length.toLocaleString('ko-KR')}/{MAX_LENGTH.toLocaleString('ko-KR')}자
		</p>
	{/if}
	<button
		type="submit"
		class={[
			'grid shrink-0 place-items-center rounded-full text-primary-foreground transition-colors',
			size === 'large' ? 'size-10 lg:size-13' : 'size-9 lg:size-11',
			isGenerating
				? 'bg-primary-light'
				: 'bg-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring'
		]}
		disabled={isGenerating}
		aria-label="질문 보내기"
	>
		<svg class="size-5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
			<path
				d="M21.4 2.6a1 1 0 0 0-1.05-.23l-17 6.5a1 1 0 0 0 .02 1.88l7.1 2.5 2.5 7.1a1 1 0 0 0 1.88.02l6.5-17a1 1 0 0 0-.25-1.07Z"
			/>
		</svg>
	</button>
</form>
