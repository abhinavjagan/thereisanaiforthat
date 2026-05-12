"""
Re-classify tools that have domain=NULL using updated tag maps and keyword rules.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.tool import Tool, ToolTag
from app.taxonomy.classifier import classify_tool

logger = logging.getLogger(__name__)


async def reclassify_unclassified(session: AsyncSession, *, batch_size: int = 500) -> int:
    """Re-run classification on tools with domain IS NULL. Returns count updated."""
    updated = 0
    last_id = 0
    while True:
        tools = (
            await session.execute(
                select(Tool)
                .where(Tool.domain.is_(None), Tool.id > last_id)
                .options(selectinload(Tool.tags))
                .order_by(Tool.id)
                .limit(batch_size)
            )
        ).scalars().all()
        if not tools:
            break

        last_id = tools[-1].id

        for tool in tools:
            tag_list = [t.tag for t in tool.tags]
            domain, category = classify_tool(tag_list, tool.name or "", tool.description or "")
            if domain:
                tool.domain = domain
                tool.category = category
                updated += 1

    await session.commit()
    logger.info("Reclassified %d previously-unclassified tools", updated)
    return updated
