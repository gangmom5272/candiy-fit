"""REST·WS와 로그인 서비스가 공유하는 내부 인증 계약."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID


class AuthenticationError(Exception):
    def __init__(self, code: Literal["AUTH_REQUIRED", "AUTH_EXPIRED"]) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: UUID


@dataclass(frozen=True, slots=True)
class IssuedAccessToken:
    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedLoginSession:
    session_id: UUID
    access: IssuedAccessToken
    refresh_token: str = field(repr=False)
    refresh_expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedWebSession:
    session_id: UUID
    token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedSocialSubject:
    """제공자 검증을 마친 뒤에만 생성한다. 요청 입력 모델로 사용하지 않는다."""

    social_type: Literal["GOOGLE", "KAKAO"]
    social_user_id: str = field(repr=False)
