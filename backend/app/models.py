"""모든 도메인 모델을 Base.metadata에 등록한다.

Alembic(alembic/env.py)은 이 모듈만 import한다. 새 도메인에 models.py를 만들면 여기에 추가한다.
빠뜨리면 autogenerate가 그 테이블을 "모델에 없음"으로 보고 DROP 초안을 만든다.
"""

from app.auth import models as auth  # noqa: F401
from app.coaching import models as coaching  # noqa: F401 - Register tables for Alembic.
from app.core.db import Base
from app.health import models as health  # noqa: F401
from app.users import models as users  # noqa: F401

metadata = Base.metadata
