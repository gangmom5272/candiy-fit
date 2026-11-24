import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';
import { playwright } from '@vitest/browser-playwright';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { readFileSync } from 'node:fs';

export default defineConfig(({ command, mode }) => ({
	// HTTP localhost 개발과 HTTPS 검증 모두 운영의 Secure 쿠키 정책을 유지한다.
	server:
		command === 'serve' && (mode === 'local-auth' || mode === 'local-auth-http')
			? {
					host: 'localhost',
					port: 5173,
					strictPort: true,
					// API CORS는 백엔드 정책을 따른다. Vite 기본 localhost 허용이 덧붙지 않게 한다.
					cors: false,
					https:
						mode === 'local-auth'
							? {
									cert: readFileSync(new URL('./.certs/localhost.pem', import.meta.url)),
									key: readFileSync(new URL('./.certs/localhost-key.pem', import.meta.url))
								}
							: undefined,
					proxy: {
						'/api': { target: 'http://127.0.0.1:8000', ws: true },
						'/healthz': { target: 'http://127.0.0.1:8000' }
					}
				}
			: undefined,
	plugins: [
		tailwindcss(),
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// 정적 SPA로 빌드해 nginx가 그대로 보낸다 (S15P31A403-99, infra/README.md "앞단 nginx·HTTPS").
			// SSR은 쓰지 않는다 (src/routes/+layout.ts). 없는 경로는 fallback(index.html)이 받아
			// /coaching/아이디, /home 등에서 새로고침해도 앱이 뜬다.
			adapter: adapter({ fallback: 'index.html' })
		})
	],
	test: {
		expect: { requireAssertions: true },
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'client',
					browser: {
						enabled: true,
						provider: playwright(),
						instances: [{ browser: 'chromium', headless: true }]
					},
					include: ['src/**/*.svelte.{test,spec}.{js,ts}'],
					exclude: ['src/lib/server/**']
				}
			},

			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.{test,spec}.{js,ts}'],
					exclude: ['src/**/*.svelte.{test,spec}.{js,ts}']
				}
			}
		]
	}
}));
