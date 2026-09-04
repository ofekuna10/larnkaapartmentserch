"""SocialAgent AI — FastAPI entry point.

Run it with::

    uvicorn main:app --reload

With an empty ``.env`` the service starts in offline mode: the ``echo`` LLM
provider and the sandbox connectors let you drive the whole pipeline before a
single platform app is registered.

    curl -XPOST localhost:8000/api/v1/pipeline/run \\
      -H 'X-Dev-User-Id: demo' -H 'content-type: application/json' \\
      -d '{"platforms":["instagram"],"wait":true}'
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agents.graph import get_pipeline
from app.api import api_router
from app.core.config import LLMProvider, get_settings
from app.core.logging import configure_logging
from app.models.api import HealthResponse
from app.services.base import SocialAPIError
from app.services.run_store import in_flight

VERSION = "0.1.0"

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Compile the graph at startup, not on the first request."""
    settings = get_settings()
    configure_logging(settings)
    get_pipeline()
    log.info(
        "%s %s starting: env=%s llm=%s vector=%s auto_publish=%s",
        settings.app_name,
        VERSION,
        settings.app_env.value,
        settings.llm_provider.value,
        settings.vector_backend.value,
        settings.auto_publish_enabled,
    )
    if settings.is_production and settings.llm_provider is LLMProvider.ECHO:
        log.error("LLM_PROVIDER=echo in production: no real content will be generated")
    try:
        yield
    finally:
        from app.core.database import dispose_engine

        await dispose_engine()
        log.info("shutdown complete (%s run(s) still in flight)", in_flight())


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        description=(
            "Multi-agent social media growth platform: analytics, strategy, "
            "content creation, validation and publishing."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.exception_handler(SocialAPIError)
    async def social_api_error_handler(
        request: Request, exc: SocialAPIError
    ) -> JSONResponse:
        """A platform's fault, not the caller's — report it as a bad gateway."""
        log.warning("social api error on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)}
        )

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    async def health() -> HealthResponse:
        """Liveness: is the process up and is the graph compiled?"""
        return HealthResponse(
            status="ok",
            app_env=settings.app_env.value,
            version=VERSION,
            llm_provider=settings.llm_provider.value,
            vector_backend=settings.vector_backend.value,
            checks={"graph": "compiled"},
        )

    @app.get("/health/ready", response_model=HealthResponse, tags=["ops"])
    async def readiness() -> HealthResponse:
        """Readiness: also probe the dependencies a run actually needs."""
        from app.core.database import check_connection

        checks = {
            "graph": "compiled",
            "database": "ok" if await check_connection() else "unavailable",
            "llm_credentials": (
                "ok"
                if settings.llm_provider is LLMProvider.ECHO
                or (
                    settings.anthropic_api_key
                    if settings.llm_provider is LLMProvider.ANTHROPIC
                    else settings.openai_api_key
                )
                else "missing"
            ),
        }
        degraded = [name for name, value in checks.items() if value != "ok"]
        return HealthResponse(
            status="degraded" if degraded else "ok",
            app_env=settings.app_env.value,
            version=VERSION,
            llm_provider=settings.llm_provider.value,
            vector_backend=settings.vector_backend.value,
            checks=checks,
        )

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug and not settings.is_production,
        log_level=settings.log_level.lower(),
    )
