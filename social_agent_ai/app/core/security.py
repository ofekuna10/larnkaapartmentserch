"""Token encryption and API authentication.

OAuth credentials are the most sensitive data this service holds: they grant
posting rights on someone's business account. They are encrypted with Fernet
before they touch the database and only ever decrypted in-process, right
before a connector call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)


class SecurityError(RuntimeError):
    """Raised for any credential handling failure."""


class TokenCipher:
    """Symmetric encryption for OAuth tokens at rest.

    In production ``TOKEN_ENCRYPTION_KEY`` must be set to a Fernet key. Outside
    production a key is derived from ``SECRET_KEY`` so a developer can run the
    stack without extra setup — that derivation is refused in production.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from cryptography.fernet import Fernet  # lazy: keeps import cost off startup

        settings = settings or get_settings()
        key = settings.token_encryption_key
        if not key:
            if settings.is_production:
                raise SecurityError(
                    "TOKEN_ENCRYPTION_KEY is required when APP_ENV=production"
                )
            digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
            key = base64.urlsafe_b64encode(digest).decode("ascii")
            log.warning("Deriving token encryption key from SECRET_KEY (dev only)")
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecurityError("stored credential could not be decrypted") from exc


@lru_cache(maxsize=1)
def get_token_cipher() -> TokenCipher:
    return TokenCipher()


# ---------------------------------------------------------------------------
# Session JWTs
# ---------------------------------------------------------------------------
def create_access_token(
    subject: str, *, extra_claims: Optional[dict[str, Any]] = None
) -> str:
    import jwt

    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()
        ),
        "iss": settings.app_name,
    }
    payload.update(extra_claims or {})
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    import jwt

    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise SecurityError(f"invalid access token: {exc}") from exc


# ---------------------------------------------------------------------------
# OAuth state & webhook signatures
# ---------------------------------------------------------------------------
def sign_oauth_state(user_id: str, platform: str, nonce: str) -> str:
    """Bind an OAuth ``state`` value to the user, so callbacks cannot be replayed."""
    settings = get_settings()
    message = f"{user_id}:{platform}:{nonce}".encode("utf-8")
    digest = hmac.new(settings.secret_key.encode("utf-8"), message, hashlib.sha256)
    return f"{user_id}.{platform}.{nonce}.{digest.hexdigest()}"


def verify_oauth_state(state: str) -> tuple[str, str]:
    """Return ``(user_id, platform)`` for a state we signed; raise otherwise."""
    try:
        user_id, platform, nonce, signature = state.split(".", 3)
    except ValueError as exc:
        raise SecurityError("malformed oauth state") from exc
    expected = sign_oauth_state(user_id, platform, nonce)
    if not hmac.compare_digest(expected.rsplit(".", 1)[1], signature):
        raise SecurityError("oauth state signature mismatch")
    return user_id, platform


def verify_meta_signature(body: bytes, header: str) -> bool:
    """Validate the ``X-Hub-Signature-256`` header on a Meta webhook."""
    settings = get_settings()
    if not settings.meta_app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.meta_app_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))
