<script lang="ts">
	import type { ExerciseSession } from '../activity';

	let { sessions }: { sessions: readonly ExerciseSession[] } = $props();
	const detailsId = $props.id();
	let isOpen = $state(false);
</script>

{#if sessions.length}
	<div class="mt-3">
		<button
			type="button"
			aria-expanded={isOpen}
			aria-controls={detailsId}
			onclick={() => (isOpen = !isOpen)}
			class="inline-flex max-w-full cursor-pointer items-start gap-1.5 rounded-sm py-1 text-left text-xs leading-5 text-text-secondary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info"
		>
			<span aria-hidden="true" class="shrink-0 text-info">ⓘ</span>
			<span>운동 시간에 포함되지 않은 걷기 {sessions.length}건</span>
		</button>
		<div id={detailsId} hidden={!isOpen} class="mt-2 border-t border-border pt-2">
			<ul aria-label="운동 시간에 포함되지 않은 걷기" class="space-y-2">
				{#each sessions as session (session.id)}
					<li
						class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-3 text-xs leading-5 text-text-secondary sm:gap-x-4"
					>
						<time datetime={session.date}>{session.date.slice(5).replace('-', '/')}</time>
						<span
							>걷기{session.avgSpeedMps === null
								? ''
								: ` ${(session.avgSpeedMps * 3.6).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}km/h`}</span
						>
						<span class="text-right">{session.durationMinutes}분</span>
					</li>
				{/each}
			</ul>
			<p class="mt-3 text-xs leading-5 text-text-secondary">
				평균 시속 4km 미만의 걷기는 운동 시간·횟수·활동 점수에 포함되지 않아요.
			</p>
		</div>
	</div>
{/if}
