# 앞단 nginx 이미지 (S15P31A403-98, -99). 웹(SvelteKit, adapter-static)을 빌드해 정적 파일로 넣는다.
# 웹 코드가 dev에 머지되면 배포 때 이 이미지가 다시 만들어져 새 화면이 올라간다.
# 빌드 컨텍스트는 저장소 루트다. 들어가는 파일은 Dockerfile.dockerignore에 적은 것뿐이다.

# 1단계: 웹 빌드. MR 검사(web job, Playwright 이미지)와 같은 Node·npm을 쓴다 (다르면 npm ci가 잠금 파일을 거부할 수 있다)
FROM node:24.22.1-alpine AS web
WORKDIR /web
# 의존성 먼저 설치해 웹 코드만 바뀌면 이 단계는 캐시를 쓴다
COPY frontend/web/package.json frontend/web/package-lock.json frontend/web/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY frontend/web ./
# 소셜 로그인 공개값(client_id)은 빌드 때 번들에 들어간다 (S15P31A403-148). compose build args로 받고, 비면 그 로그인만 꺼진다
# 이 단계에만 있어 최종 nginx 이미지에는 남지 않는다. Client Secret은 넘기지 않는다
ARG PUBLIC_GOOGLE_CLIENT_ID=""
ARG PUBLIC_KAKAO_REST_API_KEY=""
# 앱 연동 안내(S15P31A403-395) 켜기. 소문자 true 일 때만 켜지고 그 밖의 값은 꺼짐 (S15P31A403-421)
ARG PUBLIC_APP_CONNECTION_GUIDE="false"
RUN PUBLIC_GOOGLE_CLIENT_ID="$PUBLIC_GOOGLE_CLIENT_ID" PUBLIC_KAKAO_REST_API_KEY="$PUBLIC_KAKAO_REST_API_KEY" \
    PUBLIC_APP_CONNECTION_GUIDE="$PUBLIC_APP_CONNECTION_GUIDE" npm run build

# 2단계: nginx + 웹 정적 파일 + 설정
FROM nginx:1.28.0-alpine
COPY infra/nginx/templates /etc/nginx/templates
COPY --from=web /web/build /usr/share/nginx/html
