from __future__ import annotations

import datetime as dt

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str] = mapped_column(String(500), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(String(300))
    homepage_url: Mapped[str | None] = mapped_column(String(2000))

    # Classification
    domain: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str | None] = mapped_column(String(100))
    pricing_type: Mapped[str] = mapped_column(String(30), default="unknown")

    # Metadata
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    legitimacy_score: Mapped[int] = mapped_column(Integer, default=0)

    # Timestamps
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    first_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    tags: Mapped[list[ToolTag]] = relationship(back_populates="tool", cascade="all, delete-orphan")
    sources: Mapped[list[ToolSource]] = relationship(back_populates="tool", cascade="all, delete-orphan")
    metrics: Mapped[list[ToolMetric]] = relationship(back_populates="tool", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tools_domain_category", "domain", "category"),
        Index("ix_tools_legitimacy", "legitimacy_score"),
    )


class ToolTag(Base):
    __tablename__ = "tool_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False)
    tag: Mapped[str] = mapped_column(String(200), nullable=False)

    tool: Mapped[Tool] = relationship(back_populates="tags")

    __table_args__ = (
        UniqueConstraint("tool_id", "tag", name="uq_tool_tag"),
        Index("ix_tool_tags_tag", "tag"),
    )


class ToolSource(Base):
    __tablename__ = "tool_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # github, huggingface, etc.
    source_id: Mapped[str] = mapped_column(String(500), nullable=False)  # unique ID within that source
    source_url: Mapped[str | None] = mapped_column(String(2000))
    source_metadata: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    tool: Mapped[Tool] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_source_type_id"),
    )


class ToolMetric(Base):
    __tablename__ = "tool_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(Integer, ForeignKey("tools.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    trending_score: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    tool: Mapped[Tool] = relationship(back_populates="metrics")

    __table_args__ = (
        Index("ix_tool_metrics_tool_recorded", "tool_id", "recorded_at"),
    )
