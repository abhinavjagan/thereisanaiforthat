"""Pydantic response and request schemas."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field


# ---------- response models ----------


class TagOut(BaseModel):
    tag: str


class SourceOut(BaseModel):
    source_type: str
    source_id: str
    source_url: str | None = None
    fetched_at: dt.datetime | None = None


class MetricOut(BaseModel):
    source: str
    stars: int | None = None
    likes: int | None = None
    trending_score: float | None = None
    recorded_at: dt.datetime


class ToolSummary(BaseModel):
    """Lightweight model for list views."""

    id: int
    name: str
    slug: str
    summary: str | None = None
    domain: str | None = None
    category: str | None = None
    pricing_type: str | None = None
    legitimacy_score: int | None = None
    homepage_url: str | None = None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class ToolDetail(ToolSummary):
    """Full model with relations."""

    description: str | None = None
    tags: list[TagOut] = []
    sources: list[SourceOut] = []
    metrics: list[MetricOut] = []
    updated_at: dt.datetime | None = None

    model_config = {"from_attributes": True}


class DomainOut(BaseModel):
    key: str
    name: str
    categories: list[str]


class PaginatedTools(BaseModel):
    items: list[ToolSummary]
    total: int
    page: int
    page_size: int


class StatsOut(BaseModel):
    total_tools: int
    active_tools: int
    domains: dict[str, int]
    sources: dict[str, int]
    avg_legitimacy: float | None = None


# ---------- request / query models ----------


class ToolSubmission(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    homepage_url: str = Field(..., min_length=5, max_length=2000)
    description: str | None = Field(None, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=20)
