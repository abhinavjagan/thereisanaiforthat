"""
Abstract base class for all data ingesters.

Every ingester must:
  1. Fetch raw records from its API source.
  2. Convert each record to a common ToolCandidate dict.
  3. Call self._persist() to upsert into the DB (handled by base class).

All ingesters are idempotent — re-running produces no duplicates.
"""

from __future__ import annotations

import datetime as dt
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion import IngestionRun
from app.models.tool import Tool, ToolMetric, ToolSource, ToolTag
from app.taxonomy.classifier import classify_tool

logger = logging.getLogger(__name__)


@dataclass
class ToolCandidate:
    """Normalised representation coming out of any ingester."""

    source_type: str  # "huggingface", "github", etc.
    source_id: str  # unique within the source
    name: str
    description: str = ""
    homepage_url: str = ""
    source_url: str = ""
    tags: list[str] = field(default_factory=list)
    stars: int = 0
    likes: int = 0
    trending_score: float = 0.0
    source_metadata: dict | None = None
    created_at_source: dt.datetime | None = None


class BaseIngester(ABC):
    """Subclass and implement `fetch_candidates`."""

    source_name: str  # set by subclass

    @abstractmethod
    async def fetch_candidates(self) -> list[ToolCandidate]:
        """Hit the external API and return normalised candidates."""
        ...

    async def run(self, session: AsyncSession) -> IngestionRun:
        """Full pipeline: fetch → classify → persist → log."""
        run = IngestionRun(source=self.source_name)
        session.add(run)
        await session.flush()

        try:
            candidates = await self.fetch_candidates()
            run.tools_found = len(candidates)
            logger.info("%s: fetched %d candidates", self.source_name, len(candidates))

            for cand in candidates:
                is_new = await self._persist(session, cand)
                if is_new:
                    run.tools_new += 1
                else:
                    run.tools_updated += 1

        except Exception as exc:
            logger.exception("%s: ingestion error", self.source_name)
            run.errors = str(exc)[:4000]

        run.completed_at = dt.datetime.now(dt.timezone.utc)
        await session.commit()
        logger.info(
            "%s: done — %d new, %d updated",
            self.source_name,
            run.tools_new,
            run.tools_updated,
        )
        return run

    async def _persist(self, session: AsyncSession, cand: ToolCandidate) -> bool:
        """Upsert a single candidate. Return True if a new tool was created."""
        # Check if source already known
        existing_source = (
            await session.execute(
                select(ToolSource).where(
                    ToolSource.source_type == cand.source_type,
                    ToolSource.source_id == cand.source_id,
                )
            )
        ).scalar_one_or_none()

        if existing_source:
            return await self._update_existing(session, existing_source, cand)

        # Check if tool exists by slug (may have been added from another source)
        slug = slugify(cand.name, max_length=490)
        tool = (
            await session.execute(select(Tool).where(Tool.slug == slug))
        ).scalar_one_or_none()

        if tool:
            # Tool exists from another source — add this as a new source
            self._add_source(session, tool, cand)
            self._add_metric(session, tool, cand)
            return False

        # Brand-new tool
        domain, category = classify_tool(cand.tags, cand.name, cand.description)
        tool = Tool(
            name=cand.name,
            slug=slug,
            description=cand.description[:5000] if cand.description else None,
            homepage_url=cand.homepage_url or cand.source_url or None,
            domain=domain,
            category=category,
            first_seen_at=cand.created_at_source or dt.datetime.now(dt.timezone.utc),
        )
        session.add(tool)
        await session.flush()  # get tool.id

        # Tags
        for raw_tag in cand.tags:
            session.add(ToolTag(tool_id=tool.id, tag=raw_tag.lower().strip()[:200]))

        self._add_source(session, tool, cand)
        self._add_metric(session, tool, cand)
        return True

    async def _update_existing(
        self, session: AsyncSession, source: ToolSource, cand: ToolCandidate
    ) -> bool:
        """Update metadata for an already-tracked source record."""
        source.source_metadata = cand.source_metadata
        source.fetched_at = dt.datetime.now(dt.timezone.utc)

        tool = (
            await session.execute(select(Tool).where(Tool.id == source.tool_id))
        ).scalar_one()
        if cand.description and (not tool.description or len(cand.description) > len(tool.description)):
            tool.description = cand.description[:5000]

        self._add_metric(session, tool, cand)
        return False

    @staticmethod
    def _add_source(session: AsyncSession, tool: Tool, cand: ToolCandidate) -> None:
        session.add(
            ToolSource(
                tool_id=tool.id,
                source_type=cand.source_type,
                source_id=cand.source_id,
                source_url=cand.source_url or None,
                source_metadata=cand.source_metadata,
            )
        )

    @staticmethod
    def _add_metric(session: AsyncSession, tool: Tool, cand: ToolCandidate) -> None:
        session.add(
            ToolMetric(
                tool_id=tool.id,
                source=cand.source_type,
                stars=cand.stars,
                likes=cand.likes,
                trending_score=cand.trending_score,
            )
        )
