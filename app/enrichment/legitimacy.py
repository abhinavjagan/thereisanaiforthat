"""
Rule-based legitimacy scoring (0-100).

Assigns a score to each tool based on signals from its sources and metadata.
No LLM involved — purely deterministic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool, ToolSource, ToolMetric

logger = logging.getLogger(__name__)


@dataclass
class _Signals:
    num_sources: int = 0
    has_homepage: bool = False
    has_description: bool = False
    max_stars: int = 0
    max_likes: int = 0
    has_domain: bool = False


async def score_tool(session: AsyncSession, tool: Tool) -> int:
    """Compute legitimacy score for a single tool. Returns 0-100."""
    s = _Signals()

    # Count distinct sources
    s.num_sources = (
        await session.scalar(
            select(func.count()).where(ToolSource.tool_id == tool.id)
        )
    ) or 0

    s.has_homepage = bool(tool.homepage_url)
    s.has_description = bool(tool.description and len(tool.description) > 30)
    s.has_domain = tool.domain is not None

    # Best metric values
    metrics = (
        await session.execute(
            select(ToolMetric.stars, ToolMetric.likes)
            .where(ToolMetric.tool_id == tool.id)
            .order_by(ToolMetric.recorded_at.desc())
        )
    ).all()

    for stars, likes in metrics:
        if stars and stars > s.max_stars:
            s.max_stars = stars
        if likes and likes > s.max_likes:
            s.max_likes = likes

    return _compute(s)


def _compute(s: _Signals) -> int:
    score = 0

    # Multi-source corroboration (0-25)
    score += min(s.num_sources * 10, 25)

    # Homepage present (0-10)
    if s.has_homepage:
        score += 10

    # Description quality (0-10)
    if s.has_description:
        score += 10

    # Domain classified (0-5)
    if s.has_domain:
        score += 5

    # Star signal (0-25)
    if s.max_stars >= 1000:
        score += 25
    elif s.max_stars >= 100:
        score += 15
    elif s.max_stars >= 10:
        score += 8

    # Like/upvote signal (0-25)
    if s.max_likes >= 500:
        score += 25
    elif s.max_likes >= 50:
        score += 15
    elif s.max_likes >= 5:
        score += 8

    return min(score, 100)


async def score_all(session: AsyncSession, *, batch_size: int = 200) -> int:
    """Re-score all tools. Returns count of tools scored."""
    offset = 0
    count = 0
    while True:
        tools = (
            await session.execute(
                select(Tool).order_by(Tool.id).offset(offset).limit(batch_size)
            )
        ).scalars().all()
        if not tools:
            break
        for tool in tools:
            tool.legitimacy_score = await score_tool(session, tool)
            count += 1
        offset += batch_size

    await session.commit()
    logger.info("Scored %d tools", count)
    return count
