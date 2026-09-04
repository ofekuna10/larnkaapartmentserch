"""The conditional edges — especially the validation retry gate."""

from __future__ import annotations

from app.agents.graph import (
    build_graph,
    route_after_analytics,
    route_after_orchestrator,
    route_after_validation,
)
from app.agents.state import NodeName, initial_state
from app.models.schemas import ExecutionStatus, ValidationResult


def _state(connections, draft, *, valid: bool, retry_count: int, max_retries: int = 2,
           auto_publish: bool = True, flagged: bool = False):
    state = initial_state(
        user_id="u1",
        connected_platforms=connections,
        max_retries=max_retries,
        auto_publish=auto_publish,
    )
    state["generated_content"] = [draft]
    state["retry_count"] = retry_count
    state["validation_results"] = [
        ValidationResult(
            draft_id=draft.draft_id, is_valid=valid, requires_human_approval=flagged
        )
    ]
    return state


def test_invalid_with_retries_left_loops_back(connections, draft):
    state = _state(connections, draft, valid=False, retry_count=0)
    assert route_after_validation(state) == NodeName.CONTENT_CREATOR

    state = _state(connections, draft, valid=False, retry_count=1)
    assert route_after_validation(state) == NodeName.CONTENT_CREATOR


def test_invalid_with_retries_exhausted_goes_to_human(connections, draft):
    state = _state(connections, draft, valid=False, retry_count=2)
    assert route_after_validation(state) == NodeName.HUMAN_APPROVAL


def test_valid_and_auto_publish_goes_to_publisher(connections, draft):
    state = _state(connections, draft, valid=True, retry_count=0)
    assert route_after_validation(state) == NodeName.PUBLISHER


def test_valid_but_flagged_goes_to_human(connections, draft):
    state = _state(connections, draft, valid=True, retry_count=0, flagged=True)
    assert route_after_validation(state) == NodeName.HUMAN_APPROVAL


def test_valid_but_auto_publish_off_goes_to_human(connections, draft):
    state = _state(connections, draft, valid=True, retry_count=0, auto_publish=False)
    assert route_after_validation(state) == NodeName.HUMAN_APPROVAL


def test_zero_retry_budget_never_loops(connections, draft):
    state = _state(connections, draft, valid=False, retry_count=0, max_retries=0)
    assert route_after_validation(state) == NodeName.HUMAN_APPROVAL


def test_failed_preconditions_end_the_run(connections):
    state = initial_state(user_id="u1", connected_platforms=connections)
    state["execution_status"] = ExecutionStatus.FAILED
    assert route_after_orchestrator(state) == "__end__"
    assert route_after_analytics(state) == "__end__"

    state["execution_status"] = ExecutionStatus.RUNNING
    assert route_after_orchestrator(state) == NodeName.ANALYTICS
    assert route_after_analytics(state) == NodeName.STRATEGY


def test_graph_wires_all_seven_nodes():
    compiled = build_graph().compile()
    nodes = set(compiled.get_graph().nodes)
    assert {
        NodeName.ORCHESTRATOR,
        NodeName.ANALYTICS,
        NodeName.STRATEGY,
        NodeName.CONTENT_CREATOR,
        NodeName.VALIDATION,
        NodeName.PUBLISHER,
        NodeName.HUMAN_APPROVAL,
    } <= nodes

    # The retry edge must exist: validation -> content_creator.
    edges = {
        (edge.source, edge.target) for edge in compiled.get_graph().edges
    }
    assert (NodeName.VALIDATION, NodeName.CONTENT_CREATOR) in edges
    assert (NodeName.VALIDATION, NodeName.PUBLISHER) in edges
    assert (NodeName.VALIDATION, NodeName.HUMAN_APPROVAL) in edges
