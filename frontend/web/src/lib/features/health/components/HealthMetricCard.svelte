<script lang="ts">
	let { label, value, icon }: { label: string; value: string; icon: string } = $props();
</script>

<div class="min-w-0 rounded-2xl border border-border bg-card px-4 py-3 sm:px-6 sm:py-5">
	<dt class="flex items-center gap-2 text-base font-semibold">
		<span
			class="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-soft"
			aria-hidden="true"><img src={icon} alt="" /></span
		>
		{label}
	</dt>
	<dd class="mt-1 text-2xl leading-normal font-bold break-words sm:mt-2 sm:text-[28px]">
		{value}
	</dd>
</div>
