"""Provider-agnostic LLM access for the agent nodes.

Every node talks to Claude through :class:`LLMClient` and asks for a *typed*
answer: it hands over a Pydantic model and gets an instance back. That keeps
prompt plumbing (and provider quirks) out of the agent logic.

Two things make the pipeline safe to run in CI and in a degraded production:

* ``LLMRequest.fallback`` — a deterministic, heuristic version of the answer.
  It is used by the ``echo`` provider and whenever a live call fails, so a
  node degrades instead of taking the whole run down.
* ``EchoLLMClient`` — no network at all; the test suite and ``LLM_PROVIDER=echo``
  exercise the full graph offline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, TypeVar

from pydantic import BaseModel

from app.core.config import LLMProvider, Settings, get_settings

log = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a provider call fails and no fallback was supplied."""


@dataclass(slots=True)
class LLMRequest:
    """One unit of work for the LLM.

    ``intent`` is a stable label (``"analytics.summarise"``, ...) used for
    logging, cost attribution and echo-provider fixtures.
    """

    intent: str
    prompt: str
    system: str = ""
    max_tokens: Optional[int] = None
    fallback: Optional[Callable[[], Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    """The surface every provider adapter implements."""

    async def complete(self, request: LLMRequest) -> str: ...

    async def parse(
        self, request: LLMRequest, output_model: type[TModel]
    ) -> TModel: ...


def _fallback_or_raise(
    request: LLMRequest, output_model: Optional[type[TModel]], exc: Exception
) -> Any:
    if request.fallback is None:
        raise LLMError(f"{request.intent} failed and has no fallback: {exc}") from exc
    log.warning("llm.fallback intent=%s reason=%s", request.intent, exc)
    value = request.fallback()
    if output_model is not None and not isinstance(value, output_model):
        return output_model.model_validate(value)
    return value


# ---------------------------------------------------------------------------
# Anthropic (primary)
# ---------------------------------------------------------------------------
class AnthropicLLMClient:
    """Claude via the official async SDK.

    Notes for anyone extending this:

    * Structured answers go through ``messages.parse(output_format=...)``,
      which constrains the response to the model's JSON schema and validates
      it — no hand-rolled "reply with JSON only" prompting.
    * Adaptive thinking is on by default; current models reject the old
      ``budget_tokens`` form.
    * ``temperature`` is deliberately **not** sent: current Claude models
      reject sampling parameters. Steer style through the system prompt.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        import anthropic  # imported lazily so `echo` runs without the SDK

        self.settings = settings or get_settings()
        self.settings.require("anthropic_api_key")
        self._client = anthropic.AsyncAnthropic(
            api_key=self.settings.anthropic_api_key,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=self.settings.http_max_retries,
        )

    def _kwargs(self, request: LLMRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.anthropic_model,
            "max_tokens": request.max_tokens or self.settings.llm_max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system:
            kwargs["system"] = request.system
        if self.settings.llm_adaptive_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        return kwargs

    async def complete(self, request: LLMRequest) -> str:
        try:
            response = await self._client.messages.create(**self._kwargs(request))
            if response.stop_reason == "refusal":
                raise LLMError(f"{request.intent}: model declined the request")
            return "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
        except Exception as exc:  # noqa: BLE001 - degrade, then re-raise if we cannot
            return _fallback_or_raise(request, None, exc)

    async def parse(self, request: LLMRequest, output_model: type[TModel]) -> TModel:
        try:
            response = await self._client.messages.parse(
                output_format=output_model, **self._kwargs(request)
            )
            if response.stop_reason == "refusal":
                raise LLMError(f"{request.intent}: model declined the request")
            parsed = response.parsed_output
            if parsed is None:
                raise LLMError(f"{request.intent}: no structured output returned")
            return parsed
        except Exception as exc:  # noqa: BLE001
            return _fallback_or_raise(request, output_model, exc)


# ---------------------------------------------------------------------------
# OpenAI (secondary)
# ---------------------------------------------------------------------------
class OpenAILLMClient:
    """Secondary provider, kept behind the same interface for failover."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from openai import AsyncOpenAI  # imported lazily

        self.settings = settings or get_settings()
        self.settings.require("openai_api_key")
        self._client = AsyncOpenAI(
            api_key=self.settings.openai_api_key,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=self.settings.http_max_retries,
        )

    def _messages(self, request: LLMRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    async def complete(self, request: LLMRequest) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.openai_model,
                max_tokens=request.max_tokens or self.settings.llm_max_tokens,
                temperature=self.settings.llm_temperature,
                messages=self._messages(request),
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            return _fallback_or_raise(request, None, exc)

    async def parse(self, request: LLMRequest, output_model: type[TModel]) -> TModel:
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.openai_model,
                max_tokens=request.max_tokens or self.settings.llm_max_tokens,
                temperature=self.settings.llm_temperature,
                messages=self._messages(request),
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": output_model.__name__,
                        "schema": output_model.model_json_schema(),
                        "strict": False,
                    },
                },
            )
            payload = json.loads(response.choices[0].message.content or "{}")
            return output_model.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            return _fallback_or_raise(request, output_model, exc)


# ---------------------------------------------------------------------------
# Echo (offline)
# ---------------------------------------------------------------------------
class EchoLLMClient:
    """Deterministic, network-free client.

    Resolution order for a request: a fixture registered under its ``intent``,
    then the request's own ``fallback``. Anything else is a programming error
    and raises, so a test never silently exercises an empty stub.
    """

    def __init__(self, fixtures: Optional[dict[str, Any]] = None) -> None:
        self.fixtures: dict[str, Any] = dict(fixtures or {})
        self.calls: list[LLMRequest] = []

    def register(self, intent: str, value: Any) -> None:
        self.fixtures[intent] = value

    async def complete(self, request: LLMRequest) -> str:
        self.calls.append(request)
        if request.intent in self.fixtures:
            return str(self.fixtures[request.intent])
        if request.fallback is not None:
            return str(request.fallback())
        raise LLMError(f"echo provider has no fixture for intent {request.intent!r}")

    async def parse(self, request: LLMRequest, output_model: type[TModel]) -> TModel:
        self.calls.append(request)
        if request.intent in self.fixtures:
            value = self.fixtures[request.intent]
            if isinstance(value, output_model):
                return value
            return output_model.model_validate(value)
        if request.fallback is not None:
            value = request.fallback()
            if isinstance(value, output_model):
                return value
            return output_model.model_validate(value)
        raise LLMError(f"echo provider has no fixture for intent {request.intent!r}")


_CLIENTS: dict[LLMProvider, LLMClient] = {}


def get_llm_client(settings: Optional[Settings] = None) -> LLMClient:
    """Return the configured client, memoised per provider."""
    settings = settings or get_settings()
    provider = settings.llm_provider
    if provider not in _CLIENTS:
        if provider is LLMProvider.ANTHROPIC:
            _CLIENTS[provider] = AnthropicLLMClient(settings)
        elif provider is LLMProvider.OPENAI:
            _CLIENTS[provider] = OpenAILLMClient(settings)
        else:
            _CLIENTS[provider] = EchoLLMClient()
    return _CLIENTS[provider]


def set_llm_client(
    client: LLMClient, provider: Optional[LLMProvider] = None
) -> None:
    """Install a client for a provider — the seam tests use to inject fixtures."""
    _CLIENTS[provider or get_settings().llm_provider] = client


def reset_llm_clients() -> None:
    """Drop cached clients (used by tests and by config reloads)."""
    _CLIENTS.clear()
