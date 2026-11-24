"""Alembic 실행 환경. 모델 기준은 app.models.metadata, DB 주소는 .env (또는 -x url=...)."""

import asyncio

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import Settings
from app.models import metadata

config = context.config
if config.config_file_name is not None and config.attributes.get(
    "configure_logger", True
):
    from logging.config import fileConfig

    fileConfig(config.config_file_name)


def database_url() -> str:
    return (
        context.get_x_argument(as_dictionary=True).get("url") or Settings().database_url
    )


def configure(**kwargs) -> None:
    context.configure(
        target_metadata=metadata,
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """DB 없이 SQL만 출력한다: uv run alembic upgrade head --sql"""
    configure(
        url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
