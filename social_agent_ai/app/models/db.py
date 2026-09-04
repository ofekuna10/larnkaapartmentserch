"""SQLAlchemy 2.0 models — the relational side of the platform.

Note the shape of ``platform_credentials``: the token columns hold Fernet
ciphertext, never plaintext, and there is one row per (user, platform) so a
reconnect updates in place instead of accumulating stale grants.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base; Alembic autogenerate targets ``Base.metadata``."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Per-account overrides for the pipeline defaults (auto-publish, floors).
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    credentials: Mapped[list["PlatformCredential"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    runs: Mapped[list["PipelineRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class PlatformCredential(Base, TimestampMixin):
    """One connected social account. Token columns are encrypted at rest."""

    __tablename__ = "platform_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_credential_user_platform"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    handle: Mapped[str] = mapped_column(String(200), default="")
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    user: Mapped[User] = relationship(back_populates="credentials")


class PipelineRun(Base, TimestampMixin):
    """One execution of the graph. ``snapshot`` is the serialised final state."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_user_created", "user_id", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    execution_status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_node: Mapped[str] = mapped_column(String(64), default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    goals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="runs")
    drafts: Mapped[list["Draft"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Draft(Base, TimestampMixin):
    """A generated piece of content, with its latest verdict and outcome."""

    __tablename__ = "drafts"
    __table_args__ = (Index("ix_drafts_run_platform", "run_id", "platform"),)

    draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pipeline_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    content_format: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    is_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    safety_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    brand_voice_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    publish_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    platform_post_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    permalink: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped[PipelineRun] = relationship(back_populates="drafts")


class ApprovalQueueItem(Base, TimestampMixin):
    """A draft parked for a human, and what the human decided."""

    __tablename__ = "approval_queue"
    __table_args__ = (
        UniqueConstraint("draft_id", name="uq_approval_draft"),
        Index("ix_approval_pending", "user_id", "decided_at"),
    )

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("drafts.draft_id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    reviewer_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reviewer_note: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BrandVoiceDocument(Base, TimestampMixin):
    """Source of truth for brand voice; the vector store is its index."""

    __tablename__ = "brand_voice_documents"
    __table_args__ = (Index("ix_brand_voice_user", "user_id"),)

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


__all__ = [
    "ApprovalQueueItem",
    "Base",
    "BrandVoiceDocument",
    "Draft",
    "PipelineRun",
    "PlatformCredential",
    "User",
]
