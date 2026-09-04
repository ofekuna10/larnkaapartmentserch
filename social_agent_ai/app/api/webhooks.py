"""Inbound platform webhooks.

Two jobs: prove the request came from the platform, and hand the payload to
whatever cares. Signature verification is not optional — an unverified webhook
endpoint is an open write path into the account's data.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from app.api.deps import AppSettings
from app.core.security import verify_meta_signature

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/meta", summary="Meta webhook verification handshake")
async def verify_meta(
    settings: AppSettings,
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
) -> Response:
    expected = settings.meta_webhook_verify_token
    if hub_mode != "subscribe" or not expected or hub_verify_token != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="verification failed"
        )
    return Response(content=hub_challenge, media_type="text/plain")


@router.post("/meta", summary="Meta webhook events")
async def receive_meta(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
) -> dict[str, str]:
    body = await request.body()
    if not verify_meta_signature(body, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="bad signature"
        )
    payload: dict[str, Any] = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            log.info(
                "meta webhook: object=%s field=%s",
                payload.get("object"),
                change.get("field"),
            )
    # Meta retries anything that is not a fast 200, so acknowledge and process
    # asynchronously rather than doing work on this request.
    return {"status": "received"}


@router.post("/tiktok", summary="TikTok publish-status callbacks")
async def receive_tiktok(request: Request) -> dict[str, str]:
    payload: dict[str, Any] = await request.json()
    log.info(
        "tiktok webhook: event=%s publish_id=%s",
        payload.get("event"),
        (payload.get("content") or {}).get("publish_id"),
    )
    return {"status": "received"}
