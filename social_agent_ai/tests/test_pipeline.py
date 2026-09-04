"""End-to-end graph execution, including the bounded retry loop."""

from __future__ import annotations

from collections import Counter

from app.agents.graph import run_pipeline
from app.agents.state import NodeName
from app.models.schemas import (
    ExecutionStatus,
    Platform,
    PlatformConnection,
    PublishStatus,
    UserGoals,
)
from app.services.vector_store import seed_default_brand_voice


def _visits(snapshot) -> Counter[str]:
    return Counter(visit.node for visit in snapshot.node_trace)


async def test_happy_path_publishes(connections, echo_llm):
    """A passing judgement plus auto-publish reaches the publisher."""
    echo_llm.register(
        "validation.judge", {"safety_score": 0.98, "brand_voice_score": 0.95}
    )
    await seed_default_brand_voice("u1")

    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=connections,
        goals=UserGoals(posts_per_platform=1, auto_publish=True),
    )

    assert snapshot.execution_status is ExecutionStatus.COMPLETED
    assert snapshot.current_node == NodeName.PUBLISHER
    assert len(snapshot.generated_content) == 2
    assert snapshot.retry_count == 0
    # Drafts carry a scheduled slot from the strategy plan, so the connector
    # schedules rather than posts immediately.
    assert snapshot.publish_results
    assert all(
        result.status in (PublishStatus.PUBLISHED, PublishStatus.SCHEDULED)
        for result in snapshot.publish_results
    )
    assert all(result.platform_post_id for result in snapshot.publish_results)
    assert _visits(snapshot)[NodeName.CONTENT_CREATOR] == 1
    assert snapshot.errors == []


async def test_failing_validation_retries_twice_then_asks_a_human(
    connections, echo_llm
):
    """The gate loops back at most `max_retries` times, then escalates."""
    echo_llm.register(
        # A brand-voice score under the floor is a blocker on every attempt.
        "validation.judge",
        {
            "safety_score": 0.99,
            "brand_voice_score": 0.1,
            "issues": [
                {
                    "code": "off_brand",
                    "message": "Reads like an ad, not like the brand.",
                    "severity": "blocker",
                    "suggestion": "Drop the superlatives.",
                }
            ],
        },
    )
    await seed_default_brand_voice("u1")

    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=[connections[0]],
        goals=UserGoals(posts_per_platform=1, auto_publish=True),
        max_retries=2,
    )

    visits = _visits(snapshot)
    assert snapshot.retry_count == 2
    # First pass plus two revisions.
    assert visits[NodeName.CONTENT_CREATOR] == 3
    assert visits[NodeName.VALIDATION] == 3
    assert snapshot.execution_status is ExecutionStatus.AWAITING_APPROVAL
    assert visits[NodeName.PUBLISHER] == 0

    # Every attempt is kept, so the retry history is auditable.
    assert len(snapshot.validation_results) == 3
    assert all(result.is_valid is False for result in snapshot.validation_results)

    assert len(snapshot.approval_requests) == 1
    assert "after 2 revision" in snapshot.approval_requests[0].reason
    assert snapshot.publish_results[0].status is PublishStatus.QUEUED_FOR_APPROVAL

    # The revised draft carries the revision counter and keeps its identity.
    draft = snapshot.generated_content[0]
    assert draft.revision == 2


async def test_zero_retry_budget_goes_straight_to_approval(connections, echo_llm):
    echo_llm.register(
        "validation.judge", {"safety_score": 0.99, "brand_voice_score": 0.1}
    )
    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=[connections[0]],
        goals=UserGoals(posts_per_platform=1, auto_publish=True),
        max_retries=0,
    )
    assert snapshot.retry_count == 0
    assert _visits(snapshot)[NodeName.CONTENT_CREATOR] == 1
    assert snapshot.execution_status is ExecutionStatus.AWAITING_APPROVAL


async def test_auto_publish_off_queues_valid_content_for_review(
    connections, echo_llm
):
    echo_llm.register(
        "validation.judge", {"safety_score": 0.99, "brand_voice_score": 0.95}
    )
    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=[connections[0]],
        goals=UserGoals(posts_per_platform=1, auto_publish=False),
    )

    assert snapshot.execution_status is ExecutionStatus.AWAITING_APPROVAL
    assert snapshot.validation_results[0].is_valid is True
    assert "Auto-publishing is disabled" in snapshot.approval_requests[0].reason


async def test_run_aborts_without_a_live_connection(echo_llm):
    snapshot = await run_pipeline(user_id="u1", connected_platforms=[])

    assert snapshot.execution_status is ExecutionStatus.FAILED
    assert snapshot.current_node == NodeName.ORCHESTRATOR
    assert snapshot.generated_content == []
    assert any("no active" in error.message for error in snapshot.errors)


async def test_goals_are_narrowed_to_connected_platforms(echo_llm):
    """Asking for TikTok while only Instagram is connected must not plan TikTok."""
    echo_llm.register(
        "validation.judge", {"safety_score": 0.99, "brand_voice_score": 0.95}
    )
    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=[
            PlatformConnection(platform=Platform.INSTAGRAM, account_id="ig-1")
        ],
        goals=UserGoals(
            target_platforms=[Platform.INSTAGRAM, Platform.TIKTOK],
            posts_per_platform=1,
            auto_publish=True,
        ),
    )

    platforms = {draft.platform for draft in snapshot.generated_content}
    assert platforms == {Platform.INSTAGRAM}


async def test_every_draft_respects_its_platform_limits(connections, echo_llm):
    """The offline generator must itself pass the deterministic format gate."""
    from app.agents.nodes.validation import check_format

    echo_llm.register(
        "validation.judge", {"safety_score": 0.99, "brand_voice_score": 0.95}
    )
    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=connections,
        goals=UserGoals(posts_per_platform=2, auto_publish=True),
    )

    for draft in snapshot.generated_content:
        blockers = [
            issue for issue in check_format(draft) if issue.severity.value == "blocker"
        ]
        assert blockers == [], f"{draft.platform}: {blockers}"


async def test_trace_records_every_node_in_order(connections, echo_llm):
    echo_llm.register(
        "validation.judge", {"safety_score": 0.99, "brand_voice_score": 0.95}
    )
    snapshot = await run_pipeline(
        user_id="u1",
        connected_platforms=[connections[0]],
        goals=UserGoals(posts_per_platform=1, auto_publish=True),
    )

    assert [visit.node for visit in snapshot.node_trace] == [
        NodeName.ORCHESTRATOR,
        NodeName.ANALYTICS,
        NodeName.STRATEGY,
        NodeName.CONTENT_CREATOR,
        NodeName.VALIDATION,
        NodeName.PUBLISHER,
    ]
    assert all(visit.duration_ms is not None for visit in snapshot.node_trace)
    assert all(visit.status == "ok" for visit in snapshot.node_trace)
