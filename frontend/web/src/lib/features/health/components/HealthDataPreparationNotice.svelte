<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.js';

	let {
		connected = false,
		waitingForFirstRecord = false,
		rechecking = false,
		onRecheck
	}: {
		connected?: boolean;
		waitingForFirstRecord?: boolean;
		rechecking?: boolean;
		onRecheck: () => void;
	} = $props();

	function handleRecheck() {
		if (!rechecking) onRecheck();
	}
</script>

<section class="rounded-2xl border border-border bg-muted p-4 text-card-foreground sm:p-6">
	<div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
		<div role="status" aria-live="polite" class="min-w-0">
			<h2 class="text-lg font-bold">
				{connected && waitingForFirstRecord
					? '앱 연동을 완료했어요'
					: '건강 기록을 준비하고 있어요'}
			</h2>
			<p class="mt-2 text-sm leading-relaxed text-card-foreground">
				{connected && waitingForFirstRecord
					? '첫 건강데이터가 도착하기를 기다리고 있어요.'
					: connected
						? '앱 연동은 완료됐어요. 건강 기록을 화면에 표시할 준비를 하고 있어요.'
						: '건강 기록을 화면에 표시할 준비를 하고 있어요.'}
			</p>
			<p class="mt-1 text-sm leading-relaxed text-card-foreground">
				{connected
					? '앱에서 건강 기록 수집 상태를 확인하고 잠시 후 다시 확인해 주세요.'
					: '잠시 후 다시 확인해 주세요.'}
			</p>
		</div>
		<Button
			variant="outline"
			size="sm"
			class="shrink-0 self-start"
			aria-label="건강데이터 다시 확인"
			aria-busy={rechecking}
			disabled={rechecking}
			onclick={handleRecheck}>{rechecking ? '확인하는 중' : '건강데이터 다시 확인'}</Button
		>
	</div>
</section>
