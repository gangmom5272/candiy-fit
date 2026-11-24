import type { StorybookConfig } from '@storybook/sveltekit';
import { fileURLToPath } from 'node:url';
import { mergeConfig } from 'vite';

const config: StorybookConfig = {
	stories: ['../src/**/*.mdx', '../src/**/*.stories.@(js|ts|svelte)'],
	addons: ['@storybook/addon-svelte-csf', '@storybook/addon-a11y', '@storybook/addon-docs'],
	framework: '@storybook/sveltekit',
	viteFinal: (config) =>
		mergeConfig(config, {
			plugins: [
				{
					name: 'storybook:app-paths',
					config: () => ({
						resolve: {
							alias: { '$app/paths': fileURLToPath(new URL('./paths.mock.ts', import.meta.url)) }
						}
					})
				}
			]
		}),
	core: {
		// 익명 사용 통계 전송과 새 소식 알림을 끈다
		disableTelemetry: true,
		disableWhatsNewNotifications: true
	}
};

export default config;
