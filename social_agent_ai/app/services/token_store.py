"""Where OAuth credentials live between requests.

The store deals in *encrypted* rows and hands out decrypted
:class:`~app.services.base.OAuthToken` objects, so no caller has to remember
to decrypt. Swap :class:`InMemoryTokenStore` for the Supabase/Postgres
implementation in deployment; the pipeline only depends on the protocol.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from app.core.security import get_token_cipher
from app.models.schemas import Platform, PlatformConnection, utcnow
from app.services.base import OAuthToken

log = logging.getLogger(__name__)


class TokenStore(Protocol):
    async def get(self, user_id: str, platform: Platform) -> Optional[OAuthToken]: ...

    async def put(self, user_id: str, token: OAuthToken) -> None: ...

    async def connections(self, user_id: str) -> list[PlatformConnection]: ...

    async def delete(self, user_id: str, platform: Platform) -> None: ...


class InMemoryTokenStore:
    """Process-local store: tests, local development, and single-node demos."""

    def __init__(self) -> None:
        # (user_id, platform) -> encrypted payload
        self._rows: dict[tuple[str, Platform], dict[str, object]] = {}

    async def get(self, user_id: str, platform: Platform) -> Optional[OAuthToken]:
        row = self._rows.get((user_id, platform))
        if row is None:
            return None
        cipher = get_token_cipher()
        return OAuthToken(
            platform=platform,
            account_id=str(row["account_id"]),
            access_token=cipher.decrypt(str(row["access_token"])),
            refresh_token=(
                cipher.decrypt(str(row["refresh_token"]))
                if row.get("refresh_token")
                else None
            ),
            expires_at=row.get("expires_at"),  # type: ignore[arg-type]
            scopes=list(row.get("scopes") or []),  # type: ignore[arg-type]
        )

    async def put(self, user_id: str, token: OAuthToken) -> None:
        cipher = get_token_cipher()
        self._rows[(user_id, token.platform)] = {
            "account_id": token.account_id,
            "access_token": cipher.encrypt(token.access_token),
            "refresh_token": (
                cipher.encrypt(token.refresh_token) if token.refresh_token else None
            ),
            "expires_at": token.expires_at,
            "scopes": list(token.scopes),
            "updated_at": utcnow(),
        }

    async def connections(self, user_id: str) -> list[PlatformConnection]:
        out: list[PlatformConnection] = []
        for (owner, platform), row in self._rows.items():
            if owner != user_id:
                continue
            out.append(
                PlatformConnection(
                    platform=platform,
                    account_id=str(row["account_id"]),
                    scopes=list(row.get("scopes") or []),  # type: ignore[arg-type]
                    token_expires_at=row.get("expires_at"),  # type: ignore[arg-type]
                )
            )
        return out

    async def delete(self, user_id: str, platform: Platform) -> None:
        self._rows.pop((user_id, platform), None)
