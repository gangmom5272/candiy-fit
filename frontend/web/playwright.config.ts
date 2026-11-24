import { defineConfig } from '@playwright/test';

export default defineConfig({
	webServer: {
		command: 'npm run build && npm run preview',
		port: 4173,
		// 로그인 e2e용 가짜 client ID. 제공자 화면 요청은 테스트에서 가로챈다.
		env: {
			PUBLIC_APP_CONNECTION_GUIDE: 'false',
			PUBLIC_GOOGLE_CLIENT_ID: 'e2e-google-client',
			PUBLIC_KAKAO_REST_API_KEY: 'e2e-kakao-key'
		}
	},
	use: { baseURL: 'http://localhost:4173' },
	testMatch: '**/*.e2e.{ts,js}',
	testIgnore: '**/app-connection.svelte.e2e.ts'
});
