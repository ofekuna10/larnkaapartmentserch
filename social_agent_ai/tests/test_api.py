"""HTTP surface: auth, run lifecycle and the human approval round trip."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.llm import EchoLLMClient, set_llm_client
from app.models.schemas import ExecutionStatus, PublishStatus

DEV = {"X-Dev-User-Id": "u1"}


@pytest.fixture
def client(echo_llm: EchoLLMClient) -> TestClient:
    from main import create_app

    echo_llm.register(
        "validation.judge", {"safety_score": 0.98, "brand_voice_score": 0.95}
    )
    set_llm_client(echo_llm)
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health_and_readiness(client: TestClient):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["checks"]["graph"] == "compiled"

    ready = client.get("/health/ready").json()
    # No database is running in tests, so readiness reports degraded, not 500.
    assert ready["checks"]["database"] == "unavailable"
    assert ready["status"] == "degraded"


def test_run_requires_authentication(client: TestClient):
    response = client.post("/api/v1/pipeline/run", json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_run_rejects_a_request_with_no_platforms(client: TestClient):
    response = client.post("/api/v1/pipeline/run", json={}, headers=DEV)
    assert response.status_code == 400
    assert "no connected platforms" in response.json()["detail"]


def test_synchronous_run_returns_the_finished_snapshot(client: TestClient):
    response = client.post(
        "/api/v1/pipeline/run",
        json={
            "platforms": ["instagram"],
            "wait": True,
            "goals": {"posts_per_platform": 1, "auto_publish": True},
        },
        headers=DEV,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["execution_status"] == ExecutionStatus.COMPLETED.value
    assert len(body["generated_content"]) == 1
    assert body["validation_results"][0]["is_valid"] is True

    run_id = body["run_id"]
    fetched = client.get(f"/api/v1/pipeline/runs/{run_id}", headers=DEV)
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == run_id

    drafts = client.get(f"/api/v1/pipeline/runs/{run_id}/drafts", headers=DEV)
    assert len(drafts.json()["drafts"]) == 1

    listing = client.get("/api/v1/pipeline/runs", headers=DEV)
    assert [item["run_id"] for item in listing.json()] == [run_id]


def test_asynchronous_run_returns_202_and_is_pollable(client: TestClient):
    response = client.post(
        "/api/v1/pipeline/run",
        json={"platforms": ["tiktok"], "goals": {"posts_per_platform": 1}},
        headers=DEV,
    )
    assert response.status_code == 202
    body = response.json()
    run_id = body["run_id"]
    assert body["poll_url"].endswith(run_id)

    polled = client.get(body["poll_url"], headers=DEV)
    assert polled.status_code == 200
    assert polled.json()["run_id"] == run_id


def test_unknown_run_is_404(client: TestClient):
    assert client.get("/api/v1/pipeline/runs/run_missing", headers=DEV).status_code == 404


def test_graph_endpoint_exposes_the_retry_edge(client: TestClient):
    body = client.get("/api/v1/pipeline/graph").json()
    assert body["format"] == "mermaid"
    assert "validation_node" in body["diagram"]
    assert "content_creator_node" in body["diagram"]


def test_approval_round_trip_publishes_edited_copy(client: TestClient):
    run = client.post(
        "/api/v1/pipeline/run",
        json={
            "platforms": ["instagram"],
            "wait": True,
            "goals": {"posts_per_platform": 1, "auto_publish": False},
        },
        headers=DEV,
    ).json()
    assert run["execution_status"] == ExecutionStatus.AWAITING_APPROVAL.value

    pending = client.get("/api/v1/posts/pending", headers=DEV).json()["pending"]
    assert len(pending) == 1
    draft_id = pending[0]["draft_id"]

    decision = client.post(
        f"/api/v1/posts/{run['run_id']}/drafts/{draft_id}/decision",
        json={"approve": True, "edited_caption": "Reviewer rewrote this line."},
        headers=DEV,
    )
    assert decision.status_code == 200
    body = decision.json()
    assert body["approved"] is True
    assert body["publish_result"]["status"] in (
        PublishStatus.PUBLISHED.value,
        PublishStatus.SCHEDULED.value,
    )

    # The queue is drained once a decision is recorded.
    assert client.get("/api/v1/posts/pending", headers=DEV).json()["pending"] == []


def test_rejecting_a_draft_records_a_skip(client: TestClient):
    run = client.post(
        "/api/v1/pipeline/run",
        json={
            "platforms": ["instagram"],
            "wait": True,
            "goals": {"posts_per_platform": 1, "auto_publish": False},
        },
        headers=DEV,
    ).json()
    draft_id = run["generated_content"][0]["draft_id"]

    body = client.post(
        f"/api/v1/posts/{run['run_id']}/drafts/{draft_id}/decision",
        json={"approve": False, "note": "off message this quarter"},
        headers=DEV,
    ).json()

    assert body["approved"] is False
    assert body["publish_result"]["status"] == PublishStatus.SKIPPED.value
    assert body["publish_result"]["error"] == "off message this quarter"


def test_brand_voice_upsert(client: TestClient):
    body = client.post(
        "/api/v1/auth/brand-voice",
        json={"snippets": ["Never use exclamation marks."], "seed_defaults": True},
        headers=DEV,
    ).json()
    assert len(body["doc_ids"]) > 1


def test_meta_webhook_verification_rejects_a_bad_token(client: TestClient):
    response = client.get(
        "/api/v1/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.challenge": "1234",
            "hub.verify_token": "wrong",
        },
    )
    assert response.status_code == 403


def test_meta_webhook_rejects_an_unsigned_payload(client: TestClient):
    response = client.post("/api/v1/webhooks/meta", json={"object": "instagram"})
    assert response.status_code == 401


def test_oauth_start_reports_missing_configuration(client: TestClient):
    # No Meta app is configured in tests, so the flow cannot be started.
    response = client.get("/api/v1/auth/instagram/start", headers=DEV)
    assert response.status_code == 503
