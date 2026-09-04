"""Account connection: OAuth start, callback, list and disconnect."""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser
from app.core.security import SecurityError, sign_oauth_state, verify_oauth_state
from app.models.api import (
    BrandVoiceUpsertRequest,
    BrandVoiceUpsertResponse,
    ConnectionListResponse,
    OAuthCallbackResponse,
    OAuthStartResponse,
)
from app.models.schemas import Platform
from app.services.base import SocialAPIError
from app.services.oauth import authorization_url, exchange_code
from app.services.registry import get_token_store
from app.services.vector_store import get_brand_voice_store, seed_default_brand_voice

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/{platform}/start",
    response_model=OAuthStartResponse,
    summary="Begin the OAuth flow for a platform",
)
async def start_oauth(platform: Platform, user_id: CurrentUser) -> OAuthStartResponse:
    state = sign_oauth_state(user_id, platform.value, secrets.token_urlsafe(16))
    try:
        url = authorization_url(platform, state)
    except SocialAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return OAuthStartResponse(platform=platform, authorization_url=url, state=state)


@router.get(
    "/{platform}/callback",
    response_model=OAuthCallbackResponse,
    summary="OAuth redirect target",
)
async def oauth_callback(
    platform: Platform,
    code: str = Query(...),
    state: str = Query(...),
) -> OAuthCallbackResponse:
    """Complete the flow.

    The caller is identified by the signed ``state`` rather than by a bearer
    token: the browser arriving here carries the platform's redirect, not our
    session.
    """
    try:
        user_id, state_platform = verify_oauth_state(state)
    except SecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if state_platform != platform.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="state does not match the callback platform",
        )

    try:
        token = await exchange_code(platform, code)
    except SocialAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    await get_token_store().put(user_id, token)
    # A new account with no brand voice would get generic copy, so seed one.
    await seed_default_brand_voice(user_id)
    log.info("connected %s for user %s", platform.value, user_id)
    return OAuthCallbackResponse(platform=platform, account_id=token.account_id)


@router.get(
    "/connections",
    response_model=ConnectionListResponse,
    summary="List connected accounts",
)
async def list_connections(user_id: CurrentUser) -> ConnectionListResponse:
    return ConnectionListResponse(
        connections=await get_token_store().connections(user_id)
    )


@router.delete(
    "/connections/{platform}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disconnect an account",
)
async def disconnect(platform: Platform, user_id: CurrentUser) -> None:
    await get_token_store().delete(user_id, platform)


@router.post(
    "/brand-voice",
    response_model=BrandVoiceUpsertResponse,
    summary="Add brand voice guidance",
)
async def upsert_brand_voice(
    body: BrandVoiceUpsertRequest, user_id: CurrentUser
) -> BrandVoiceUpsertResponse:
    """Store tone-of-voice snippets that every draft will be written against."""
    doc_ids: list[str] = []
    if body.seed_defaults:
        doc_ids += await seed_default_brand_voice(user_id)
    doc_ids += await get_brand_voice_store().upsert(user_id, body.snippets)
    return BrandVoiceUpsertResponse(doc_ids=doc_ids)
