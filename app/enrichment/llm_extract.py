"""
LLM-based structured extraction for pricing and summary.

Strictly scoped: LLM is NEVER used for categorisation.
It only reads a tool's homepage (fetched HTML text) and extracts:
  - pricing_type: free | freemium | paid | open_source | unknown
  - one-line summary (≤ 160 chars)

Rate-limited to settings.llm_max_per_day calls to control cost.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.tool import Tool

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a structured data extraction assistant. Given the text content
of a web page for a software tool, return JSON with exactly two keys:
  "pricing_type": one of "free", "freemium", "paid", "open_source", "unknown"
  "summary": a single sentence (max 160 characters) describing the tool.
Return ONLY valid JSON, no markdown fences, no explanation."""


async def enrich_tool(session: AsyncSession, tool: Tool, page_text: str) -> bool:
    """
    Call the LLM to extract pricing and summary for one tool.
    Returns True if enrichment was applied.
    """
    if not settings.llm_enrichment_enabled:
        return False

    if not page_text or len(page_text.strip()) < 50:
        return False

    try:
        import openai

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        # Truncate page text to avoid huge contexts
        truncated = page_text[:6000]

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            temperature=0,
            max_tokens=200,
        )

        content = response.choices[0].message.content or ""
        data = json.loads(content)

        pricing = data.get("pricing_type", "unknown")
        if pricing in ("free", "freemium", "paid", "open_source", "unknown"):
            tool.pricing_type = pricing

        summary = data.get("summary", "")
        if summary and len(summary) <= 300:
            tool.summary = summary[:160]

        return True

    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("LLM extraction parse error for %s: %s", tool.slug, exc)
        return False
    except Exception as exc:
        logger.error("LLM extraction failed for %s: %s", tool.slug, exc)
        return False


async def enrich_batch(session: AsyncSession) -> int:
    """
    Enrich tools that lack pricing_type or summary, respecting daily limit.
    Returns count of tools enriched.
    """
    if not settings.llm_enrichment_enabled:
        logger.info("LLM enrichment disabled")
        return 0

    import httpx

    # Find tools needing enrichment with a homepage URL
    tools = (
        await session.execute(
            select(Tool)
            .where(
                Tool.homepage_url.isnot(None),
                Tool.pricing_type.is_(None),
                Tool.summary.is_(None),
            )
            .order_by(Tool.legitimacy_score.desc().nullsfirst())
            .limit(settings.llm_max_per_day)
        )
    ).scalars().all()

    enriched = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
        for tool in tools:
            try:
                resp = await http.get(tool.homepage_url)
                if resp.status_code != 200:
                    continue
                page_text = resp.text
            except httpx.HTTPError:
                continue

            ok = await enrich_tool(session, tool, page_text)
            if ok:
                enriched += 1

    await session.commit()
    logger.info("LLM enriched %d tools", enriched)
    return enriched
