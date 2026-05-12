"""
APScheduler job definitions for periodic ingestion and enrichment.

Schedule strategy:
  - HackerNews & HuggingFace: every 4 hours (free, no auth, fast).
  - GitHub: every 6 hours (token rate-limited to 30 req/min).
  - ProductHunt: every 12 hours (strict rate limit, ~60 pages per window).
  - Enrichment (dedup + scoring): runs twice daily after ingestion waves.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import async_session
from app.ingesters.huggingface import HuggingFaceIngester
from app.ingesters.github import GitHubIngester
from app.ingesters.producthunt import ProductHuntIngester
from app.ingesters.hackernews import HackerNewsIngester
from app.enrichment.dedup import merge_duplicates
from app.enrichment.legitimacy import score_all
from app.enrichment.llm_extract import enrich_batch
from app.enrichment.reclassify import reclassify_unclassified

logger = logging.getLogger(__name__)


async def _run_ingester(klass: type) -> None:
    async with async_session() as session:
        try:
            ingester = klass()
            run = await ingester.run(session)
            logger.info(
                "%s: found=%d new=%d updated=%d errors=%d",
                run.source,
                run.tools_found,
                run.tools_new,
                run.tools_updated,
                run.errors,
            )
        except Exception:
            logger.exception("Ingester %s failed", klass.source_name)


async def run_free_ingesters() -> None:
    """HackerNews + HuggingFace — free APIs, no auth needed."""
    logger.info("Starting free-tier ingestion (HN + HF)")
    for klass in (HackerNewsIngester, HuggingFaceIngester):
        await _run_ingester(klass)


async def run_github_ingester() -> None:
    logger.info("Starting GitHub ingestion")
    await _run_ingester(GitHubIngester)


async def run_producthunt_ingester() -> None:
    logger.info("Starting ProductHunt ingestion")
    await _run_ingester(ProductHuntIngester)


async def run_enrichment() -> None:
    logger.info("Starting scheduled enrichment run")
    async with async_session() as session:
        merged = await merge_duplicates(session)
        reclassed = await reclassify_unclassified(session)
        scored = await score_all(session)
        enriched = await enrich_batch(session)
        logger.info(
            "Enrichment done: merged=%d reclassified=%d scored=%d llm_enriched=%d",
            merged,
            reclassed,
            scored,
            enriched,
        )


def create_scheduler() -> AsyncIOScheduler:
    """Create scheduler with staggered job schedule."""
    scheduler = AsyncIOScheduler()

    # HN + HF every 4 h at :05
    scheduler.add_job(
        run_free_ingesters,
        CronTrigger(hour="*/4", minute=5),
        id="ingest_free",
        replace_existing=True,
    )

    # GitHub every 6 h at :15
    scheduler.add_job(
        run_github_ingester,
        CronTrigger(hour="0,6,12,18", minute=15),
        id="ingest_github",
        replace_existing=True,
    )

    # ProductHunt every 12 h at :25
    scheduler.add_job(
        run_producthunt_ingester,
        CronTrigger(hour="8,20", minute=25),
        id="ingest_producthunt",
        replace_existing=True,
    )

    # Enrichment (dedup + score) twice daily at 03:00 and 15:00
    scheduler.add_job(
        run_enrichment,
        CronTrigger(hour="3,15", minute=0),
        id="enrichment",
        replace_existing=True,
    )

    return scheduler
