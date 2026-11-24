import { defineEnvVars } from '@sveltejs/kit/env';

/** 값이 없거나 빈 문자열이면 undefined. 해당 기능을 끄는 쪽으로 처리한다. */
const optional = (value: string | undefined) => value || undefined;

// 정적 SPA라 실행 시점 환경이 없으므로 빌드 때 값을 번들에 넣는다(static).
export const variables = defineEnvVars({
	PUBLIC_APP_CONNECTION_GUIDE: {
		public: true,
		static: true,
		schema: (value) => value === 'true',
		description:
			'#363 마이그레이션·API·Android 완료 보고 배포 확인 후에만 true. 기본 false로 Home·Health·AI 코치의 연동 분기와 상태 조회를 함께 끈다'
	},
	PUBLIC_GOOGLE_CLIENT_ID: {
		public: true,
		static: true,
		schema: optional,
		description: 'Google 인가 요청의 client_id. 백엔드 GOOGLE_CLIENT_ID와 같은 값'
	},
	PUBLIC_KAKAO_REST_API_KEY: {
		public: true,
		static: true,
		schema: optional,
		description: 'Kakao 인가 요청의 client_id(REST API 키). 백엔드 KAKAO_REST_API_KEY와 같은 값'
	},
	PUBLIC_SKIP_AUTH_GUARD: {
		public: true,
		static: true,
		schema: (value) => value === 'true',
		description:
			'true면 vite 개발 서버에서만 로그인 확인 없이 (app) 화면을 연다. 빌드 결과물에는 적용되지 않는다'
	}
});
