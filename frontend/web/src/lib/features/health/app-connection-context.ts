import { getContext, setContext } from 'svelte';
import type { AppConnectionState } from './app-connection.svelte';

const key = Symbol('app-connection');

export function provideAppConnection(state: AppConnectionState): void {
	setContext(key, state);
}

export function appConnectionContext(): AppConnectionState | undefined {
	return getContext<AppConnectionState | undefined>(key);
}
