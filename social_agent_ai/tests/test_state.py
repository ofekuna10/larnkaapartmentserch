"""AgentState construction, read helpers and the retry accounting."""

from __future__ import annotations

from datetime import timedelta

from app.agents.state import (
    active_platforms,
    current_validations,
    failed_drafts,
    initial_state,
    is_valid,
    latest_validation_for,
    needs_human_approval,
    retries_left,
)
from app.models.schemas import (
    ExecutionStatus,
    Platform,
    PlatformConnection,
    UserGoals,
    ValidationResult,
    utcnow,
)


def test_initial_state_seeds_every_key(connections):
    state = initial_state(user_id="u1", connected_platforms=connections)

    assert state["user_id"] == "u1"
    assert state["execution_status"] is ExecutionStatus.PENDING
    assert state["retry_count"] == 0
    assert state["raw_analytics"] is None
    # Collections are seeded so nodes never need `.get(..., [])`.
    for key in ("generated_content", "validation_results", "errors", "node_trace"):
        assert state[key] == []


def test_active_platforms_filters_expired_and_unrequested():
    state = initial_state(
        user_id="u1",
        connected_platforms=[
            PlatformConnection(platform=Platform.INSTAGRAM, account_id="ig"),
            PlatformConnection(
                platform=Platform.TIKTOK,
                account_id="tt",
                token_expires_at=utcnow() - timedelta(hours=1),
            ),
            PlatformConnection(
                platform=Platform.FACEBOOK, account_id="fb", is_active=False
            ),
            PlatformConnection(platform=Platform.YOUTUBE, account_id="yt"),
        ],
        goals=UserGoals(target_platforms=[Platform.INSTAGRAM, Platform.TIKTOK]),
    )

    # TikTok's token has expired, so it drops out even though it was requested.
    assert active_platforms(state) == [Platform.INSTAGRAM]


def test_latest_validation_wins_over_earlier_attempts(connections, draft):
    state = initial_state(user_id="u1", connected_platforms=connections)
    state["generated_content"] = [draft]
    state["validation_results"] = [
        ValidationResult(draft_id=draft.draft_id, is_valid=False),
        ValidationResult(draft_id=draft.draft_id, is_valid=True),
    ]

    assert latest_validation_for(state, draft.draft_id).is_valid is True
    assert len(current_validations(state)) == 1
    assert is_valid(state) is True
    assert failed_drafts(state) == []


def test_is_valid_requires_a_verdict_for_every_draft(connections, draft):
    state = initial_state(user_id="u1", connected_platforms=connections)
    other = draft.model_copy(update={"draft_id": "draft_other"})
    state["generated_content"] = [draft, other]
    state["validation_results"] = [
        ValidationResult(draft_id=draft.draft_id, is_valid=True)
    ]

    # One draft was never judged; the run is not valid yet.
    assert is_valid(state) is False


def test_retries_left_and_approval_gate(connections, draft):
    state = initial_state(
        user_id="u1", connected_platforms=connections, max_retries=2, auto_publish=True
    )
    state["generated_content"] = [draft]
    state["validation_results"] = [
        ValidationResult(draft_id=draft.draft_id, is_valid=True)
    ]
    assert retries_left(state) == 2
    assert needs_human_approval(state) is False

    state["retry_count"] = 2
    assert retries_left(state) == 0

    # A flagged verdict pulls a human in even when the draft passed.
    state["validation_results"] = [
        ValidationResult(
            draft_id=draft.draft_id, is_valid=True, requires_human_approval=True
        )
    ]
    assert needs_human_approval(state) is True


def test_auto_publish_disabled_always_needs_approval(connections, draft):
    state = initial_state(
        user_id="u1", connected_platforms=connections, auto_publish=False
    )
    state["generated_content"] = [draft]
    state["validation_results"] = [
        ValidationResult(draft_id=draft.draft_id, is_valid=True)
    ]
    assert needs_human_approval(state) is True
