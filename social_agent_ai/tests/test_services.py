"""Services: credential handling, connector parsing and retrieval."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.security import (
    SecurityError,
    create_access_token,
    decode_access_token,
    get_token_cipher,
    sign_oauth_state,
    verify_meta_signature,
    verify_oauth_state,
)
from app.models.schemas import Platform, utcnow
from app.services.base import OAuthToken
from app.services.meta import _parse_meta_time
from app.services.sandbox import SandboxConnector, sandbox_token
from app.services.token_store import InMemoryTokenStore
from app.services.vector_store import InMemoryBrandVoiceStore, seed_default_brand_voice
from app.services.youtube import parse_iso_duration


# --- Credentials -----------------------------------------------------------
def test_token_cipher_round_trip():
    cipher = get_token_cipher()
    secret = "EAAG-a-real-looking-token"
    encrypted = cipher.encrypt(secret)

    assert encrypted != secret
    assert cipher.decrypt(encrypted) == secret


def test_token_cipher_rejects_tampered_ciphertext():
    cipher = get_token_cipher()
    encrypted = cipher.encrypt("secret")
    with pytest.raises(SecurityError):
        cipher.decrypt(encrypted[:-4] + "AAAA")


def test_access_token_round_trip():
    token = create_access_token("u1", extra_claims={"role": "owner"})
    claims = decode_access_token(token)
    assert claims["sub"] == "u1"
    assert claims["role"] == "owner"


def test_decode_rejects_garbage():
    with pytest.raises(SecurityError):
        decode_access_token("not-a-jwt")


def test_oauth_state_is_bound_to_user_and_platform():
    state = sign_oauth_state("u1", "instagram", "nonce123")
    assert verify_oauth_state(state) == ("u1", "instagram")

    with pytest.raises(SecurityError):
        verify_oauth_state(state[:-1] + ("0" if state[-1] != "0" else "1"))
    with pytest.raises(SecurityError):
        verify_oauth_state("garbage")


def test_meta_signature_requires_the_app_secret(settings):
    # No app secret configured in tests, so nothing can be verified.
    assert verify_meta_signature(b"{}", "sha256=deadbeef") is False


async def test_token_store_never_holds_plaintext():
    store = InMemoryTokenStore()
    token = OAuthToken(
        platform=Platform.INSTAGRAM,
        account_id="ig-1",
        access_token="plaintext-secret",
        refresh_token="refresh-secret",
        expires_at=utcnow() + timedelta(days=30),
        scopes=["instagram_basic"],
    )
    await store.put("u1", token)

    stored = store._rows[("u1", Platform.INSTAGRAM)]
    assert "plaintext-secret" not in str(stored["access_token"])
    assert "refresh-secret" not in str(stored["refresh_token"])

    loaded = await store.get("u1", Platform.INSTAGRAM)
    assert loaded is not None
    assert loaded.access_token == "plaintext-secret"
    assert loaded.refresh_token == "refresh-secret"

    connections = await store.connections("u1")
    assert [c.platform for c in connections] == [Platform.INSTAGRAM]

    await store.delete("u1", Platform.INSTAGRAM)
    assert await store.get("u1", Platform.INSTAGRAM) is None


def test_oauth_token_expiry_has_a_safety_margin():
    almost = OAuthToken(
        platform=Platform.TIKTOK,
        account_id="tt",
        access_token="x",
        expires_at=utcnow() + timedelta(seconds=30),
    )
    # Expiring inside a minute counts as expired; the request would race.
    assert almost.is_expired is True

    fine = almost.model_copy(update={"expires_at": utcnow() + timedelta(hours=2)})
    assert fine.is_expired is False

    assert almost.model_copy(update={"expires_at": None}).is_expired is False


# --- Connector parsing -----------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT1M35S", 95.0),
        ("PT45S", 45.0),
        ("PT2H3M4S", 7384.0),
        ("P1DT2H", 93600.0),
        (None, None),
        ("nonsense", None),
    ],
)
def test_parse_iso_duration(value, expected):
    assert parse_iso_duration(value) == expected


def test_parse_meta_time_handles_both_shapes():
    assert _parse_meta_time("2026-05-01T12:00:00+0000") is not None
    assert _parse_meta_time("2026-05-01T12:00:00Z") is not None
    assert _parse_meta_time("") is None
    assert _parse_meta_time("not-a-date") is None


async def test_sandbox_connector_is_deterministic():
    connector = SandboxConnector(Platform.INSTAGRAM)
    token = sandbox_token(Platform.INSTAGRAM, "u1")

    first = await connector.fetch_recent_posts(token, lookback_days=90)
    second = await connector.fetch_recent_posts(token, lookback_days=90)

    assert [p.impressions for p in first] == [p.impressions for p in second]
    assert all(p.engagement_rate is not None for p in first)


async def test_sandbox_respects_the_limit():
    connector = SandboxConnector(Platform.TIKTOK)
    posts = await connector.fetch_recent_posts(
        sandbox_token(Platform.TIKTOK, "u1"), lookback_days=30, limit=5
    )
    assert len(posts) == 5


# --- Retrieval -------------------------------------------------------------
async def test_brand_voice_search_ranks_by_overlap():
    store = InMemoryBrandVoiceStore()
    await store.upsert(
        "u1",
        [
            "Always lead with the concrete outcome for the viewer.",
            "Never mention competitors by name.",
        ],
    )

    hits = await store.search("u1", "concrete outcome for the viewer", limit=2)
    assert hits[0].text.startswith("Always lead")
    assert hits[0].score > 0


async def test_brand_voice_search_is_scoped_per_user():
    store = InMemoryBrandVoiceStore()
    await store.upsert("u1", ["Our voice is dry and specific."])
    assert await store.search("u2", "voice") == []


async def test_seed_default_brand_voice_is_not_empty():
    ids = await seed_default_brand_voice("u1")
    assert len(ids) >= 5
