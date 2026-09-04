"""Shared FastAPI dependencies."""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import SecurityError, decode_access_token

log = logging.getLogger(__name__)

DEV_USER_HEADER = "x-dev-user-id"


async def current_user_id(
    authorization: Annotated[Optional[str], Header()] = None,
    x_dev_user_id: Annotated[Optional[str], Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> str:
    """Resolve the caller.

    Production requires a bearer JWT. Outside production an ``X-Dev-User-Id``
    header is accepted so the pipeline can be driven from curl without an auth
    provider standing up first.
    """
    settings = settings or get_settings()

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(token)
        except SecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        subject = claims.get("sub")
        if not subject:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="token has no subject"
            )
        return str(subject)

    if x_dev_user_id and not settings.is_production:
        return x_dev_user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUser = Annotated[str, Depends(current_user_id)]
AppSettings = Annotated[Settings, Depends(get_settings)]
