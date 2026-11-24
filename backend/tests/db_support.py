"""전용 DB fixture의 연결 준비. 테스트 SQL이나 운영 연결은 재시도하지 않는다."""

import asyncio
import logging


async def connect_test_database(engine):
    for attempt in range(3):
        try:
            connection = await engine.connect()
        except OSError as exc:
            if getattr(exc, "winerror", None) != 121 or attempt == 2:
                raise
            logging.getLogger(__name__).warning(
                "Test DB initial connect WinError121; retry %d/2", attempt + 1
            )
            await engine.dispose()
            await asyncio.sleep(0.1)
        else:
            await connection.close()
            return
