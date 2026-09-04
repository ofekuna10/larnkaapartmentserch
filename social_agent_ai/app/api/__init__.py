"""FastAPI routers, assembled into one versioned router."""

from fastapi import APIRouter

from app.api import auth, pipeline, posts, webhooks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(pipeline.router)
api_router.include_router(posts.router)
api_router.include_router(webhooks.router)

__all__ = ["api_router"]
