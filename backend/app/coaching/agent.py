"""공유 LLM 모델과 요청별 건강 포트로 코칭 에이전트를 호출한다."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from functools import partial

import httpx
from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.coaching.connection import log_failure
from app.coaching.health_port import HealthQueryPort
from app.coaching.history import HistorySnapshot, validate_snapshot
from app.coaching.summary_adapter import to_ai_summary, to_ai_turn
from app.core.config import Settings
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import EventSink
from coach_ai.agent.graph import CoachResult, run_coach
from coach_ai.config import get_settings as get_ai_settings
from coach_ai.llm.client import create_chat_model, make_http_client
from coach_ai.ports import HealthDataPort
from coach_ai.security.audit import AuditSink

RunCoach = Callable[..., Awaitable[CoachResult]]
HealthPortFactory = Callable[[str, datetime], HealthDataPort]
ModelFactory = Callable[[], BaseChatModel]
logger = logging.getLogger(__name__)


class CoachAgent:
    """앱에서 모델 하나를 공유하고 요청마다 건강 포트를 만들어 코칭을 실행한다."""

    def __init__(
        self,
        *,
        model_factory: ModelFactory,
        port_factory: HealthPortFactory | None,
        run: RunCoach = run_coach,
        close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """지연 모델 생성·건강 조회·실행·자원 정리 함수를 보관한다."""
        self._model_factory = model_factory
        self._port_factory = port_factory
        self._run = run
        self._close = close
        self._model: BaseChatModel | None = None

    def ensure_ready(self) -> bool:
        """포트가 준비됐을 때만 I/O 없이 모델을 한 번 만들고 준비 여부를 반환한다."""
        if self._port_factory is None:
            return False
        if self._model is None:
            try:
                self._model = self._model_factory()
            except Exception as error:
                # 설정·모델 생성 경계에서 모든 일반 오류를 안전하게 기록하고 재시도를 허용한다.
                log_failure(logger, "chat model", error)
                return False
        return True

    def get_model(self) -> BaseChatModel:
        """ensure_ready로 준비한 답변 모델만 반환해 요약에서도 같은 객체를 쓰게 한다."""
        if self._model is None:
            raise RuntimeError("coaching agent is not ready")
        return self._model

    async def run(
        self,
        *,
        ctx: CoachContext,
        question: str,
        sink: EventSink,
        now: datetime,
        message_id: str,
        audit: AuditSink | None = None,
    ) -> CoachResult:
        """준비된 모델·새 건강 포트에 Context의 요약과 완료 쌍만 전달한다.

        audit가 없으면 AI 패키지의 기본 메모리 기록기에 도구 호출이 기록된다.
        """
        if self._model is None or self._port_factory is None:
            raise RuntimeError("coaching agent is not ready")
        return await self._run(
            ctx,
            question,
            port=self._port_factory(ctx.user_id, now),
            model=self._model,
            sink=sink,
            now=now,
            message_id=message_id,
            audit=audit,
        )

    async def run_with_history(
        self,
        *,
        ctx: CoachContext,
        question: str,
        history: HistorySnapshot,
        sink: EventSink,
        now: datetime,
        message_id: str,
        audit: AuditSink | None = None,
    ) -> CoachResult:
        """완료 쌍·요약을 실제 AI Context에만 넣고 마지막 사용량을 포함한 결과를 보존한다."""
        validate_snapshot(history)
        context = replace(
            ctx,
            summary=to_ai_summary(history.summary),
            turns=tuple(to_ai_turn(turn) for turn in history.turns),
        )
        return await self.run(
            ctx=context,
            question=question,
            sink=sink,
            now=now,
            message_id=message_id,
            audit=audit,
        )

    async def aclose(self) -> None:
        """소유한 자원 정리 함수를 최대 한 번 await한다."""
        close, self._close = self._close, None
        if close is not None:
            await close()


def default_agent(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    engine: AsyncEngine | None = None,
) -> CoachAgent:
    """AI 설정을 읽지 않고 환경별 건강 포트와 지연 모델 생성을 구성한다.

    local·test는 메모리 합성 데이터, dev·prod는 앱 공통 세션 팩토리를 쓰는 DB 건강 포트를 쓴다.
    포트를 만들 때 DB에 접속하지 않는다.
    """
    clients: list[httpx.AsyncClient] = []

    def model_factory() -> BaseChatModel:
        """첫 질문 때 요약과 답변용 슬롯을 최소 두 개 확보한 공유 모델을 만든다."""
        ai_settings = get_ai_settings()
        # 요약 하나가 답변까지 직렬화하지 않게 명시값 1도 2로 올린다.
        # 캐시된 AI 설정은 바꾸지 않으며 상한 3은 AI 설정 검증을 따른다.
        if ai_settings.llm_max_concurrency < 2:
            ai_settings = ai_settings.model_copy(update={"llm_max_concurrency": 2})
        client = make_http_client(ai_settings)
        # 모델 생성이 실패한 시도의 클라이언트도 종료 때 함께 닫는다.
        clients.append(client)
        return create_chat_model(ai_settings, http_client=client)

    async def close() -> None:
        """모델 생성 시 보관한 HTTP 클라이언트를 닫는다."""
        for client in clients:
            await client.aclose()

    port_factory: HealthPortFactory
    if settings.app_env in {"local", "test"}:
        port_factory = partial(sample_health_data, include_exercise_activity=True)
    else:
        if engine is None:
            raise RuntimeError("dev·prod 코칭에는 앱 엔진이 필요하다")
        health_port = HealthQueryPort(session_factory, engine=engine)

        def db_port(user_id: str, now: datetime) -> HealthDataPort:
            """요청 회원·시각과 관계없이 앱 수명 동안 공유하는 DB 건강 포트를 반환한다."""
            return health_port

        port_factory = db_port
    return CoachAgent(
        model_factory=model_factory, port_factory=port_factory, close=close
    )
