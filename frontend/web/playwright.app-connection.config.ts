import { defineConfig } from '@playwright/test';

export default defineConfig({
	webServer: {
		command: 'npm run build && npm run preview -- --port 4174 --strictPort',
		port: 4174,
		env: {
			PUBLIC_APP_CONNECTION_GUIDE: 'true',
			PUBLIC_GOOGLE_CLIENT_ID: 'e2e-google-client',
			PUBLIC_KAKAO_REST_API_KEY: 'e2e-kakao-key'
		}
	},
	use: { baseURL: 'http://localhost:4174' },
	testMatch: '**/app-connection.svelte.e2e.ts'
});
