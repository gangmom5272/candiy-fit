# 데이터 파이프라인 이미지 (S15P31A403-59). 기본으로 주기 실행(scheduler)을 띄운다.
# 빌드·실행: infra/docker-compose.yml의 pipeline 서비스 (infra/README.md "파이프라인 주기 실행")
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV TZ=Asia/Seoul \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# config.py가 저장소 루트(.env 위치)를 경로로 찾으므로 저장소와 같은 깊이에 둔다. 컨테이너에는 .env가 없고 환경 변수를 쓴다
WORKDIR /app/data/pipeline

# 의존성 먼저 설치해 코드만 바뀌면 이 단계는 캐시를 쓴다
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/data/pipeline/.venv/bin:$PATH"

# 비밀값·건강데이터를 다루므로 root로 돌리지 않는다
RUN useradd --create-home --uid 10001 pipeline && chown -R pipeline /app
USER pipeline

CMD ["python", "-m", "candiy_pipeline.scheduler"]
