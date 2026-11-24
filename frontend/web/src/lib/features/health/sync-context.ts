import { getContext, setContext } from 'svelte';
import { api, type ApiClient } from '#lib/api/client.js';

export interface HealthSyncContext {
	readonly revision: number;
	readonly rechecking: boolean;
	recheck(): Promise<void>;
	read<T>(
		signal: AbortSignal,
		request: (signal: AbortSignal, client: ApiClient) => Promise<T>
	): Promise<T>;
}
const key = Symbol('health-sync');
export function provideHealthSync(context: HealthSyncContext) {
	setContext(key, context);
}
export function healthSyncContext(): HealthSyncContext {
	return (
		getContext<HealthSyncContext | undefined>(key) ?? {
			revision: 0,
			rechecking: false,
			recheck: async () => {},
			read: (signal, request) => request(signal, api)
		}
	);
}
