"""Orchestrator Agent — the pipeline's entry point and traffic controller.

It does not call an LLM. Its job is to establish that the run *can* proceed
(a user, at least one live platform connection, a coherent set of goals), to
normalise the goals against what is actually connected, and to name the next
node so the routing decision is visible in the state rather than implied by
the graph's shape.
"""

from __future__ import annotations

from typing import Any

from app.agents.nodes._common import agent_node
from app.agents.state import AgentState, NodeName, active_platforms
from app.core.config import get_settings
from app.models.schemas import ExecutionStatus, PipelineError, UserGoals


@agent_node(NodeName.ORCHESTRATOR, ExecutionStatus.RUNNING)
async def orchestrator_node(state: AgentState) -> dict[str, Any]:
    settings = get_settings()
    goals: UserGoals = state.get("goals") or UserGoals()
    platforms = active_platforms(state)

    if not state.get("user_id"):
        return {
            "execution_status": ExecutionStatus.FAILED,
            "next_node": None,
            "errors": [
                PipelineError(
                    node=NodeName.ORCHESTRATOR,
                    message="run has no user_id",
                    recoverable=False,
                )
            ],
        }

    if not platforms:
        requested = [p.value for p in goals.target_platforms]
        detail = (
            f"none of the requested platforms are connected: {requested}"
            if requested
            else "the account has no active, unexpired platform connections"
        )
        return {
            "execution_status": ExecutionStatus.FAILED,
            "next_node": None,
            "errors": [
                PipelineError(
                    node=NodeName.ORCHESTRATOR, message=detail, recoverable=False
                )
            ],
        }

    # Pin the goals to what we can actually act on, so downstream nodes never
    # plan for a platform this run cannot touch.
    resolved_goals = goals.model_copy(update={"target_platforms": platforms})
    auto_publish = (
        goals.auto_publish
        if goals.auto_publish is not None
        else settings.auto_publish_enabled
    )

    return {
        "goals": resolved_goals,
        "auto_publish": auto_publish,
        "max_retries": state.get("max_retries", settings.max_validation_retries),
        "next_node": NodeName.ANALYTICS,
        "_detail": f"{len(platforms)} platform(s): "
        + ", ".join(p.value for p in platforms),
    }


def route_from_orchestrator(state: AgentState) -> str:
    """Stop the run here when preconditions failed; otherwise start analytics."""
    if state.get("execution_status") is ExecutionStatus.FAILED:
        return "abort"
    return NodeName.ANALYTICS
