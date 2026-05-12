"""
Cross-source deduplication and URL normalisation.

After ingesters run, different sources may reference the same tool.
This module merges Tool rows that point to the same canonical homepage URL.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool, ToolSource, ToolTag, ToolMetric

logger = logging.getLogger(__name__)


def normalise_url(raw: str | None) -> str | None:
    """Canonicalise a URL for comparison."""
    if not raw:
        return None
    parsed = urlparse(raw.strip().rstrip("/"))
    # Remove www prefix
    host = parsed.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    # Drop fragments and query strings for comparison
    canonical = urlunparse(("https", host, parsed.path.rstrip("/"), "", "", ""))
    return canonical.lower() if canonical != "https://" else None


async def merge_duplicates(session: AsyncSession) -> int:
    """
    Find tools sharing the same canonical homepage_url and merge them.

    Strategy: keep the tool with the most sources attached (ties broken by
    lowest id = earliest created). Move all tags, sources, and metrics from
    duplicates to the keeper, then delete duplicates.

    Returns number of tools merged away.
    """
    # Build a mapping of canonical_url → list[tool_id]
    result = await session.execute(
        select(Tool.id, Tool.homepage_url).where(Tool.homepage_url.isnot(None))
    )
    rows = result.all()

    url_groups: dict[str, list[int]] = {}
    for tool_id, url in rows:
        canon = normalise_url(url)
        if canon:
            url_groups.setdefault(canon, []).append(tool_id)

    merged_count = 0
    for canon_url, tool_ids in url_groups.items():
        if len(tool_ids) < 2:
            continue

        # Pick keeper: most sources, then lowest id
        source_counts = {}
        for tid in tool_ids:
            cnt = await session.scalar(
                select(func.count()).where(ToolSource.tool_id == tid)
            )
            source_counts[tid] = cnt or 0

        sorted_ids = sorted(
            tool_ids, key=lambda t: (-source_counts[t], t)
        )
        keeper_id = sorted_ids[0]
        duplicate_ids = sorted_ids[1:]

        for dup_id in duplicate_ids:
            await _absorb_tool(session, keeper_id, dup_id)
            merged_count += 1

    await session.commit()
    logger.info("Merged %d duplicate tools", merged_count)
    return merged_count


async def _absorb_tool(
    session: AsyncSession, keeper_id: int, dup_id: int
) -> None:
    """Move children from dup to keeper, delete dup."""
    # Move sources (skip if source already exists on keeper)
    dup_sources = (
        await session.execute(
            select(ToolSource).where(ToolSource.tool_id == dup_id)
        )
    ).scalars().all()
    for src in dup_sources:
        existing = await session.scalar(
            select(ToolSource.id).where(
                ToolSource.source_type == src.source_type,
                ToolSource.source_id == src.source_id,
            )
        )
        if existing:
            await session.delete(src)
        else:
            src.tool_id = keeper_id

    # Move tags (skip duplicates)
    dup_tags = (
        await session.execute(
            select(ToolTag).where(ToolTag.tool_id == dup_id)
        )
    ).scalars().all()
    for tag in dup_tags:
        existing = await session.scalar(
            select(ToolTag.id).where(
                ToolTag.tool_id == keeper_id, ToolTag.tag == tag.tag
            )
        )
        if existing:
            await session.delete(tag)
        else:
            tag.tool_id = keeper_id

    # Move metrics
    dup_metrics = (
        await session.execute(
            select(ToolMetric).where(ToolMetric.tool_id == dup_id)
        )
    ).scalars().all()
    for m in dup_metrics:
        m.tool_id = keeper_id

    # Delete duplicate tool
    dup_tool = await session.get(Tool, dup_id)
    if dup_tool:
        await session.delete(dup_tool)
