<script lang="ts">
	import { tick } from 'svelte';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import candy from '#lib/assets/images/candy.webp';
	import homeIcon from '#lib/assets/icons/icon-home.svg';
	import healthIcon from '#lib/assets/icons/icon-health.svg';
	import coachIcon from '#lib/assets/icons/icon-coach.svg';
	import userIcon from '#lib/assets/icons/icon-mypage.svg';

	/** 없으면 로그아웃 메뉴를 비활성으로 둔다. 인증 처리는 호출하는 layout이 맡는다. */
	let { onLogout }: { onLogout?: () => void } = $props();

	const menuId = $props.id();
	let userMenuState = $state<'closed' | 'hover' | 'click'>('closed');
	let isUserMenuOpen = $derived(userMenuState !== 'closed');
	let userArea = $state<HTMLDivElement>();
	let userButton = $state<HTMLButtonElement>();
	let userMenu = $state<HTMLDivElement>();
	let pathname = $derived(page.url.pathname);

	function currentRoute(path: string): 'page' | 'true' | undefined {
		// SSR에서 반환되는 상대 경로도 현재 URL 기준 pathname으로 비교한다.
		const target = new URL(`${resolve('/')}${path.slice(1)}`, page.url.href).pathname;
		if (pathname === target) return 'page';
		if (pathname.startsWith(`${target}/`)) return 'true';
		return undefined;
	}

	function isActive(path: string) {
		return currentRoute(path) !== undefined;
	}

	/**
	 * 주 메뉴 상태 구분(멘토링 피드백). 기본은 회색 글자, 선택은 연분홍 배경 + 진한 굵은 글자,
	 * 마우스를 올리면 옅은 회색 배경, 키보드 포커스는 브랜드색 테두리. 선택 칸에는 호버 배경을 겹치지 않는다.
	 */
	function navState(path: string) {
		return isActive(path)
			? 'bg-primary-soft font-bold text-text-primary'
			: 'text-text-secondary hover:bg-surface hover:text-text-primary';
	}

	function closeUserMenu(restoreFocus = false) {
		userMenuState = 'closed';
		if (restoreFocus) userButton?.focus();
	}

	async function toggleUserMenu(event: MouseEvent) {
		if (userMenuState === 'click') {
			closeUserMenu(true);
			return;
		}
		userMenuState = 'click';
		if (event.detail === 0) {
			await tick();
			userMenu?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
		}
	}

	function handleUserPointerEnter(event: PointerEvent) {
		if (event.pointerType === 'mouse' && userMenuState === 'closed') userMenuState = 'hover';
	}

	function handleUserPointerLeave(event: PointerEvent) {
		if (event.pointerType === 'mouse' && userMenuState === 'hover') closeUserMenu();
	}

	function handleOutsidePointer(event: PointerEvent) {
		if (event.target instanceof Node && !userArea?.contains(event.target)) closeUserMenu();
	}

	function handleUserFocusOut(event: FocusEvent) {
		if (!(event.relatedTarget instanceof Node) || !userArea?.contains(event.relatedTarget)) {
			closeUserMenu();
		}
	}

	function handleUserKeydown(event: KeyboardEvent) {
		if (!isUserMenuOpen) return;
		if (event.key === 'Escape') {
			event.preventDefault();
			closeUserMenu(true);
			return;
		}
		if (!(event.target instanceof Node) || !userArea?.contains(event.target)) return;
		if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;

		event.preventDefault();
		userMenuState = 'click';
		const items = Array.from(userMenu?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
		const index = items.findIndex((item) => item === event.target);
		const nextIndex =
			event.key === 'Home'
				? 0
				: event.key === 'End'
					? items.length - 1
					: (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
		items[nextIndex]?.focus();
	}
</script>

<svelte:window onpointerdown={handleOutsidePointer} onkeydown={handleUserKeydown} />

<header
	class="sticky top-0 z-50 shrink-0 rounded-b-[2.5rem] bg-card"
	class:mobile-navigation={page.status < 400 || page.status === 404}
>
	<div
		class="mx-auto grid max-w-[1792px] grid-cols-2 items-center gap-y-4 px-5 py-5 sm:px-8 md:h-16 md:grid-cols-[auto_minmax(0,1fr)_auto] md:gap-x-2 md:gap-y-0 md:py-0 lg:grid-cols-[1fr_minmax(0,auto)_1fr] lg:gap-x-0 2xl:h-20"
	>
		<!-- 로고·사용자 메뉴는 키보드 포커스를 테두리 대신 배경으로 보여 준다(#308 팀 결정) -->
		<a
			href={resolve('/(app)/home')}
			aria-label="CANDiY-FiT 홈"
			class="flex w-fit items-center gap-2 rounded-lg text-xl leading-7 font-bold whitespace-nowrap text-brand focus-visible:bg-primary-soft focus-visible:text-primary focus-visible:outline-none"
		>
			<img src={candy} alt="" width="32" height="18" class="h-auto w-8 shrink-0" />
			<span>CANDiY-FiT</span>
		</a>
		<nav
			aria-label="주 메뉴"
			class="app-navigation order-3 col-span-2 flex items-center justify-center gap-2 sm:gap-8 md:order-none md:col-span-1"
		>
			<a
				href={resolve('/(app)/home')}
				aria-current={currentRoute('/home')}
				class={[
					navState('/home'),
					'grid h-11 flex-1 cursor-pointer grid-cols-[2.5rem_auto] items-center justify-center gap-2 rounded-xl px-4 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:w-[130px] sm:flex-initial sm:gap-3 sm:text-base'
				]}
			>
				<span
					class={isActive('/home')
						? 'contents'
						: 'flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-soft'}
				>
					<img src={homeIcon} alt="" width="24" height="24" class="justify-self-center" />
				</span>
				<span>홈</span>
			</a>
			<a
				href={resolve('/(app)/health')}
				aria-current={currentRoute('/health')}
				class={[
					navState('/health'),
					'flex h-11 flex-1 cursor-pointer items-center justify-center gap-2 rounded-xl text-sm whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:w-[140px] sm:flex-initial sm:gap-3 sm:text-base'
				]}
			>
				<span
					class="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-soft"
				>
					<img src={healthIcon} alt="" width="24" height="24" />
				</span>
				건강 데이터
			</a>
			<a
				href={resolve('/(app)/coaching')}
				aria-current={currentRoute('/coaching')}
				class={[
					navState('/coaching'),
					'flex h-11 flex-1 cursor-pointer items-center justify-center gap-2 rounded-xl text-sm whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:w-[140px] sm:flex-initial sm:gap-3 sm:text-base'
				]}
			>
				<span
					class="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-soft"
				>
					<img src={coachIcon} alt="" width="24" height="24" />
				</span>
				AI 코치
			</a>
		</nav>
		<div
			bind:this={userArea}
			role="group"
			aria-label="사용자 계정"
			onfocusout={handleUserFocusOut}
			onpointerenter={handleUserPointerEnter}
			onpointerleave={handleUserPointerLeave}
			class="relative z-50 justify-self-end lg:mr-1"
		>
			<button
				bind:this={userButton}
				id={`${menuId}-trigger`}
				type="button"
				aria-label="사용자 메뉴"
				aria-haspopup="menu"
				aria-expanded={isUserMenuOpen}
				aria-controls={menuId}
				onclick={toggleUserMenu}
				class="flex size-10 cursor-pointer items-center justify-center rounded-full bg-primary-soft focus-visible:bg-coral-soft focus-visible:outline-none"
			>
				<img src={userIcon} alt="" width="36" height="36" />
			</button>
			{#if isUserMenuOpen}
				<div class="absolute top-full right-0 w-[214px] pt-5">
					<div
						bind:this={userMenu}
						id={menuId}
						role="menu"
						aria-labelledby={`${menuId}-trigger`}
						class="rounded-2xl border border-border bg-card p-3 text-sm font-bold"
					>
						<a
							href={resolve('/(app)/my')}
							role="menuitem"
							tabindex="-1"
							onclick={() => closeUserMenu()}
							class="block w-full rounded-lg px-4 py-2 text-left leading-[18px] text-primary hover:bg-primary-soft focus-visible:bg-primary-soft"
						>
							마이페이지
						</a>
						<div role="separator" class="mx-1.5 my-2.5 h-px bg-border"></div>
						<button
							type="button"
							role="menuitem"
							tabindex="-1"
							aria-disabled={onLogout ? undefined : 'true'}
							onclick={() => {
								closeUserMenu();
								onLogout?.();
							}}
							class="block w-full rounded-lg px-4 py-2 text-left leading-[18px] text-primary hover:bg-primary-soft focus-visible:bg-primary-soft"
						>
							로그아웃
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
</header>

<style>
	@media (width < 768px) {
		.mobile-navigation > div {
			row-gap: 0;
			padding-block: 0.75rem;
		}

		.mobile-navigation .app-navigation {
			position: fixed;
			inset: auto 0 0;
			display: grid;
			grid-template-columns: repeat(3, minmax(0, 1fr));
			gap: 0.5rem;
			height: var(--app-bottom-nav-height, calc(5rem + env(safe-area-inset-bottom, 0px)));
			padding: 0.5rem max(1rem, env(safe-area-inset-right, 0px))
				calc(0.5rem + env(safe-area-inset-bottom, 0px)) max(1rem, env(safe-area-inset-left, 0px));
			border-top: 1px solid var(--color-border);
			background: var(--color-card);
		}

		.mobile-navigation .app-navigation > a {
			display: flex;
			width: auto;
			min-width: 0;
			height: 100%;
			flex-direction: column;
			gap: 0;
			padding: 0;
			font-size: 0.75rem;
			white-space: nowrap;
		}

		.mobile-navigation .app-navigation > a > span:first-child {
			display: flex;
			width: 2.5rem;
			height: 2.5rem;
			align-items: center;
			justify-content: center;
		}
	}
</style>
