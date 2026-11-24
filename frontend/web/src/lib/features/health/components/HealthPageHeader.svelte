<script lang="ts">
	import HealthTabs from './HealthTabs.svelte';
</script>

<header>
	<div class="flex flex-col gap-2 pb-1.5 sm:flex-row sm:items-center sm:gap-6">
		<h1 class="text-page-title">건강 데이터</h1>
		<p class="text-[15px] leading-[normal] text-text-secondary">
			기록을 둘러보고, 궁금한 점은 AI 코치에게 물어보세요.
		</p>
	</div>
	<div class="border-b border-border"><HealthTabs /></div>
</header>
