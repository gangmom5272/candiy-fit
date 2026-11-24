# 백엔드 이미지 (S15P31A403-97). 기본으로 API 서버를 띄우고, 같은 이미지로 DB 마이그레이션도 한다.
#   서버:         CMD 그대로 (infra/docker-compose.yml의 backend 서비스)
#   마이그레이션: command를 alembic upgrade head로 바꿔 실행 (migrate 서비스)
# 빌드 컨텍스트는 저장소 루트다. 백엔드가 coach-ai(../ai)를 경로 의존성으로 쓰기 때문이다.
# 들어가는 파일은 Dockerfile.dockerignore에 적은 것뿐이다 (.env·건강데이터·테스트가 이미지에 들어가지 않게).
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 저장소와 같은 구조로 둔다. config.py가 저장소 루트(.env 위치)를 경로로 찾는다. 컨테이너에는 .env가 없고 환경 변수를 쓴다
WORKDIR /app/backend

# 의존성 먼저 설치해 코드만 바뀌면 이 단계는 캐시를 쓴다
COPY ai /app/ai
COPY data/pipeline /app/data/pipeline
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app

ENV PATH="/app/backend/.venv/bin:$PATH"

# 비밀값·건강데이터를 다루므로 root로 돌리지 않는다
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000

# --no-access-log: uvicorn 기본 접근 로그는 회원 ID가 든 실제 경로·쿼리를 남긴다 (infra/data-export-checklist.md 2-2)
# --proxy-headers: 앞단 nginx(S15P31A403-98)가 넘겨 준 원래 요청 정보를 쓴다. 이 서버는 compose 내부 네트워크에서만 받는다
# --workers 1 (S15P31A403-118): LLM 동시 요청 제한(LLM_MAX_CONCURRENCY)을 프로세스마다 따로 세고,
#   시작 시 진행 중 코칭 요청을 정리(S15P31A403-93)하므로 워커가 여럿이면 제한이 배로 풀리고 다른 워커의 요청까지 끊는다
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
