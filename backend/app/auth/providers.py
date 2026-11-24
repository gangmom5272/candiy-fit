"""고정된 공식 HTTPS 엔드포인트에서만 자격을 검증한다. DB를 사용하지 않는다."""

import asyncio
import hashlib
import hmac
import re
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

import httpx
import jwt

from app.auth.contract import VerifiedSocialSubject
from app.auth.schemas import WebSocialLoginRequest
from app.core.config import Settings

GOOGLE_EXCHANGE_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
KAKAO_EXCHANGE_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_INFO_URL = "https://kapi.kakao.com/v1/user/access_token_info"
KAKAO_JWKS_URL = "https://kauth.kakao.com/.well-known/jwks.json"
JWKS_URLS = {"GOOGLE": GOOGLE_JWKS_URL, "KAKAO": KAKAO_JWKS_URL}


class SocialProviderError(Exception):
    def __init__(
        self, code: Literal["SOCIAL_CREDENTIAL_INVALID", "SOCIAL_PROVIDER_UNAVAILABLE"]
    ) -> None:
        super().__init__(code)
        self.code = code


class ProviderConfigurationError(Exception):
    """서버 설정 오류. 제공자 원문이나 비밀값을 예외에 넣지 않는다."""


def invalid_credential() -> SocialProviderError:
    return SocialProviderError("SOCIAL_CREDENTIAL_INVALID")


def provider_unavailable() -> SocialProviderError:
    return SocialProviderError("SOCIAL_PROVIDER_UNAVAILABLE")


@dataclass
class _KeyCache:
    keys: dict[str, jwt.PyJWK] = field(default_factory=dict)
    expires_at: float = 0.0
    last_attempt: float = float("-inf")
    retry_after: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SocialProviderClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self._key_caches = {provider: _KeyCache() for provider in JWKS_URLS}

    async def verify_web(self, payload: WebSocialLoginRequest) -> VerifiedSocialSubject:
        try:
            async with asyncio.timeout(15):
                if payload.social_type == "GOOGLE":
                    return await self._google(payload)
                return await self._kakao(payload)
        except TimeoutError:
            raise provider_unavailable() from None

    async def verify_android(
        self,
        social_type: Literal["GOOGLE", "KAKAO"],
        token: str,
        *,
        nonce_hash: str,
    ) -> VerifiedSocialSubject:
        """저장된 challenge의 nonce 해시를 받아 검증된 주체만 반환한다."""
        if social_type == "GOOGLE":
            audience = self.settings.google_client_id
            issuer = ["https://accounts.google.com", "accounts.google.com"]
        elif social_type == "KAKAO":
            audience = self.settings.kakao_native_app_key.get_secret_value()
            issuer = ["https://kauth.kakao.com"]
        else:
            raise ValueError("unsupported social provider")
        if not audience.strip():
            raise ProviderConfigurationError("android_provider_configuration_missing")
        try:
            async with asyncio.timeout(15):
                claims = await self._decode_id_token(
                    token, social_type, audience, issuer
                )
        except TimeoutError:
            raise provider_unavailable() from None
        if social_type == "GOOGLE":
            allowed_presenters = [audience, *self.settings.google_android_client_ids]
            if (
                "azp" in claims
                or (isinstance(claims["aud"], list) and len(claims["aud"]) > 1)
            ) and claims.get("azp") not in allowed_presenters:
                raise invalid_credential()
        elif (
            claims["aud"] != audience
            or re.fullmatch(r"[1-9][0-9]{0,254}", claims["sub"]) is None
        ):
            raise invalid_credential()
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not nonce:
            raise invalid_credential()
        try:
            matches = hmac.compare_digest(
                hashlib.sha256(nonce.encode("utf-8")).hexdigest(), nonce_hash
            )
        except (ValueError, TypeError):
            raise invalid_credential() from None
        if not matches:
            raise invalid_credential()
        return VerifiedSocialSubject(social_type, claims["sub"])

    async def _json(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict, httpx.Headers]:
        try:
            response = await self.http.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=httpx.Timeout(5, connect=3),
                follow_redirects=False,
            )
        except httpx.RequestError:
            raise provider_unavailable() from None
        if (
            response.status_code >= 500
            or response.status_code == 429
            or response.is_redirect
        ):
            raise provider_unavailable()
        try:
            body = response.json()
        except ValueError:
            raise provider_unavailable() from None
        if not isinstance(body, dict):
            raise provider_unavailable()
        if response.status_code != 200:
            # 공개키 조회 실패는 로그인 자격·클라이언트 설정 오류가 아니다.
            if url in JWKS_URLS.values():
                raise provider_unavailable()
            if any(
                name in body and type(body[name]) is not expected
                for name, expected in (
                    ("error", str),
                    ("error_code", str),
                    ("code", int),
                )
            ):
                raise provider_unavailable()
            # Kakao는 일시적 장애(-1)와 발급 한도(KOE237)도 400으로 반환한다.
            if body.get("code") == -1 or body.get("error_code") == "KOE237":
                raise provider_unavailable()
            # 다른 앱에 바인딩된 인가 코드는 서버 secret 오류와 구분한다.
            if body.get("error_code") == "KOE114":
                raise invalid_credential()
            if body.get("error_code") in {"KOE010", "KOE310"} or body.get("error") in {
                "invalid_client",
                "unauthorized_client",
                "unsupported_grant_type",
            }:
                raise ProviderConfigurationError("oauth_configuration_invalid")
            if body.get("error") == "invalid_grant" or body.get("code") == -401:
                raise invalid_credential()
            raise provider_unavailable()
        return body, response.headers

    async def _google(self, payload: WebSocialLoginRequest) -> VerifiedSocialSubject:
        secret = self.settings.google_client_secret.get_secret_value()
        if not self.settings.google_client_id or not secret:
            raise ProviderConfigurationError("google_configuration_missing")
        body, _ = await self._json(
            "POST",
            GOOGLE_EXCHANGE_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": self.settings.google_client_id,
                "client_secret": secret,
                "code": payload.authorization_code,
                "code_verifier": payload.code_verifier,
                "redirect_uri": payload.redirect_uri,
            },
        )
        token = body.get("id_token")
        if not isinstance(token, str) or not token:
            raise provider_unavailable()
        return await self.verify_google_id_token(token)

    async def _public_key(self, social_type: str, kid: str) -> jwt.PyJWK:
        cache = self._key_caches[social_type]
        async with cache.lock:
            now = monotonic()
            if now < cache.retry_after and (
                now >= cache.expires_at or kid not in cache.keys
            ):
                raise provider_unavailable()
            if now >= cache.expires_at or (
                kid not in cache.keys and now - cache.last_attempt >= 30
            ):
                cache.last_attempt = now
                # 실패·취소로 키 목록을 확인하지 못한 경우에도 재시도 간격을 지킨다.
                cache.retry_after = now + 30
                body, headers = await self._json("GET", JWKS_URLS[social_type])
                raw_keys = body.get("keys")
                if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= 100:
                    raise provider_unavailable()
                keys = {}
                try:
                    for raw in raw_keys:
                        if (
                            not isinstance(raw, dict)
                            or raw.get("kty") != "RSA"
                            or raw.get("alg") != "RS256"
                            or raw.get("use") != "sig"
                            or not isinstance(raw.get("kid"), str)
                            or not raw["kid"]
                            or raw["kid"] in keys
                        ):
                            raise ValueError("invalid key set")
                        keys[raw["kid"]] = jwt.PyJWK.from_dict(raw, algorithm="RS256")
                except (ValueError, TypeError, KeyError, jwt.PyJWTError):
                    raise provider_unavailable() from None
                cache_control = headers.get("cache-control", "")
                age = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control)
                ttl = min(int(age[1]), 3600) if age else 300
                if "no-store" in cache_control or "no-cache" in cache_control:
                    ttl = 0
                cache.keys = keys
                cache.expires_at = monotonic() + ttl
                cache.retry_after = 0.0
            key = cache.keys.get(kid)
            if key is None:
                raise invalid_credential()
            return key

    async def verify_google_id_token(self, token: str) -> VerifiedSocialSubject:
        """WEB client 전용 audience·azp 정책을 유지한다."""
        if not self.settings.google_client_id:
            raise ProviderConfigurationError("google_configuration_missing")
        claims = await self._decode_id_token(
            token,
            "GOOGLE",
            self.settings.google_client_id,
            ["https://accounts.google.com", "accounts.google.com"],
        )
        audience = claims["aud"]
        if (
            "azp" in claims or (isinstance(audience, list) and len(audience) > 1)
        ) and claims.get("azp") != self.settings.google_client_id:
            raise invalid_credential()
        return VerifiedSocialSubject("GOOGLE", claims["sub"])

    async def _decode_id_token(
        self, token: str, social_type: str, audience: str, issuer: list[str]
    ) -> dict:
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (
                header.get("alg") != "RS256"
                or not isinstance(kid, str)
                or not 1 <= len(kid) <= 256
            ):
                raise invalid_credential()
        except (jwt.PyJWTError, ValueError, TypeError):
            raise invalid_credential() from None
        key = await self._public_key(social_type, kid)
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=issuer,
                options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            )
            subject = claims["sub"]
            if (
                not isinstance(subject, str)
                or not subject
                or len(subject) > 255
                or not subject.isascii()
            ):
                raise invalid_credential()
            if type(claims["exp"]) is not int or type(claims["iat"]) is not int:
                raise invalid_credential()
        except (jwt.PyJWTError, ValueError, TypeError):
            raise invalid_credential() from None
        return claims

    async def _kakao(self, payload: WebSocialLoginRequest) -> VerifiedSocialSubject:
        key = self.settings.kakao_rest_api_key.get_secret_value()
        secret = self.settings.kakao_client_secret.get_secret_value()
        if not key or self.settings.kakao_app_id is None:
            raise ProviderConfigurationError("kakao_configuration_missing")
        data = {
            "grant_type": "authorization_code",
            "client_id": key,
            "code": payload.authorization_code,
            "code_verifier": payload.code_verifier,
            "redirect_uri": payload.redirect_uri,
        }
        # 콘솔에서 secret을 비활성화한 기존 앱도 지원한다. 활성화한 앱은 반드시 주입한다.
        if secret:
            data["client_secret"] = secret
        body, _ = await self._json("POST", KAKAO_EXCHANGE_URL, data=data)
        token = body.get("access_token")
        if (
            not isinstance(token, str)
            or not token
            or not isinstance(body.get("token_type"), str)
            or body["token_type"].lower() != "bearer"
        ):
            raise provider_unavailable()
        info, _ = await self._json(
            "GET", KAKAO_INFO_URL, headers={"Authorization": f"Bearer {token}"}
        )
        if any(
            type(info.get(name)) is not int for name in ("id", "app_id", "expires_in")
        ):
            raise provider_unavailable()
        if (
            info["app_id"] != self.settings.kakao_app_id
            or info["expires_in"] <= 0
            or info["id"] <= 0
        ):
            raise invalid_credential()
        return VerifiedSocialSubject("KAKAO", str(info["id"]))
