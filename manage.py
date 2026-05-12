#!/usr/bin/env python
"""CLI for managing the AI Tools Database."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import sys

import click
from rich.console import Console
from rich.table import Table

console = Console()


def _run(coro):
    """Run an async coroutine from sync click context."""
    return asyncio.run(coro)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool):
    """AI Tools Database management CLI."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )


@cli.command()
@click.option(
    "--source",
    type=click.Choice(
        ["all", "huggingface", "github", "producthunt", "hackernews"]
    ),
    default="all",
)
@click.option("--deep", is_flag=True, help="Deep historical pull (one-time bulk fetch)")
def ingest(source: str, deep: bool):
    """Run data ingestion from configured sources."""

    async def _ingest():
        from app.database import async_session
        from app.ingesters.huggingface import HuggingFaceIngester
        from app.ingesters.github import GitHubIngester
        from app.ingesters.producthunt import ProductHuntIngester
        from app.ingesters.hackernews import HackerNewsIngester
        # Classes that support deep mode
        deep_supported = {HuggingFaceIngester, HackerNewsIngester, GitHubIngester, ProductHuntIngester}

        source_map = {
            "huggingface": HuggingFaceIngester,
            "github": GitHubIngester,
            "producthunt": ProductHuntIngester,
            "hackernews": HackerNewsIngester,
        }

        if source == "all":
            klasses = list(source_map.values())
        else:
            klasses = [source_map[source]]

        async with async_session() as session:
            for klass in klasses:
                mode = "DEEP" if deep and klass in deep_supported else "normal"
                console.print(f"[bold blue]Running {klass.source_name} ({mode})...[/]")
                try:
                    if deep and klass in deep_supported:
                        ingester = klass(deep=True)
                    else:
                        ingester = klass()
                    run = await ingester.run(session)
                    console.print(
                        f"  [green]✓[/] found={run.tools_found} "
                        f"new={run.tools_new} updated={run.tools_updated} "
                        f"errors={run.errors}"
                    )
                except Exception as exc:
                    console.print(f"  [red]✗[/] {exc}")

    _run(_ingest())


@cli.command()
@click.option("--dedup/--no-dedup", default=True)
@click.option("--score/--no-score", default=True)
@click.option("--llm/--no-llm", default=False)
def enrich(dedup: bool, score: bool, llm: bool):
    """Run enrichment pipeline (dedup, scoring, LLM extraction)."""

    async def _enrich():
        from app.database import async_session
        from app.enrichment.dedup import merge_duplicates
        from app.enrichment.legitimacy import score_all
        from app.enrichment.llm_extract import enrich_batch
        from app.enrichment.reclassify import reclassify_unclassified

        async with async_session() as session:
            if dedup:
                console.print("[bold blue]Deduplicating...[/]")
                merged = await merge_duplicates(session)
                console.print(f"  [green]✓[/] merged {merged} duplicates")

            console.print("[bold blue]Reclassifying unclassified tools...[/]")
            reclassed = await reclassify_unclassified(session)
            console.print(f"  [green]✓[/] reclassified {reclassed} tools")

            if score:
                console.print("[bold blue]Scoring legitimacy...[/]")
                scored = await score_all(session)
                console.print(f"  [green]✓[/] scored {scored} tools")

            if llm:
                console.print("[bold blue]LLM enrichment...[/]")
                enriched = await enrich_batch(session)
                console.print(f"  [green]✓[/] enriched {enriched} tools")

    _run(_enrich())


@cli.command()
def stats():
    """Show database statistics."""

    async def _stats():
        from sqlalchemy import select, func
        from app.database import async_session
        from app.models.tool import Tool, ToolSource

        async with async_session() as session:
            total = (await session.scalar(select(func.count(Tool.id)))) or 0
            active = (
                await session.scalar(
                    select(func.count(Tool.id)).where(Tool.is_active.is_(True))
                )
            ) or 0
            classified = (
                await session.scalar(
                    select(func.count(Tool.id)).where(Tool.domain.isnot(None))
                )
            ) or 0
            avg_leg = await session.scalar(
                select(func.avg(Tool.legitimacy_score)).where(
                    Tool.legitimacy_score.isnot(None)
                )
            )

            table = Table(title="AI Tools Database Stats")
            table.add_column("Metric", style="bold")
            table.add_column("Value", justify="right")
            table.add_row("Total tools", str(total))
            table.add_row("Active", str(active))
            table.add_row("Classified", str(classified))
            table.add_row("Unclassified", str(total - classified))
            table.add_row(
                "Avg legitimacy",
                f"{avg_leg:.1f}" if avg_leg else "N/A",
            )
            console.print(table)

            # Source breakdown
            source_rows = (
                await session.execute(
                    select(
                        ToolSource.source_type, func.count(ToolSource.id)
                    ).group_by(ToolSource.source_type)
                )
            ).all()

            if source_rows:
                st = Table(title="Sources")
                st.add_column("Source")
                st.add_column("Count", justify="right")
                for src, cnt in sorted(source_rows, key=lambda r: -r[1]):
                    st.add_row(src, str(cnt))
                console.print(st)

            # Domain breakdown
            domain_rows = (
                await session.execute(
                    select(Tool.domain, func.count(Tool.id))
                    .where(Tool.domain.isnot(None))
                    .group_by(Tool.domain)
                )
            ).all()

            if domain_rows:
                dt_table = Table(title="Domains")
                dt_table.add_column("Domain")
                dt_table.add_column("Count", justify="right")
                for dom, cnt in sorted(domain_rows, key=lambda r: -r[1]):
                    dt_table.add_row(dom, str(cnt))
                console.print(dt_table)

    _run(_stats())


@cli.command()
@click.argument("output", type=click.Path(), default="export.csv")
@click.option("--min-legitimacy", type=int, default=0)
def export(output: str, min_legitimacy: int):
    """Export tools to CSV."""

    async def _export():
        from sqlalchemy import select
        from app.database import async_session
        from app.models.tool import Tool

        async with async_session() as session:
            query = select(Tool).where(Tool.is_active.is_(True))
            if min_legitimacy > 0:
                query = query.where(Tool.legitimacy_score >= min_legitimacy)
            query = query.order_by(Tool.legitimacy_score.desc())

            tools = (await session.execute(query)).scalars().all()

            with open(output, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "name",
                        "slug",
                        "domain",
                        "category",
                        "pricing_type",
                        "legitimacy_score",
                        "homepage_url",
                        "summary",
                        "created_at",
                    ]
                )
                for t in tools:
                    writer.writerow(
                        [
                            t.name,
                            t.slug,
                            t.domain or "",
                            t.category or "",
                            t.pricing_type or "",
                            t.legitimacy_score or "",
                            t.homepage_url or "",
                            t.summary or "",
                            t.created_at.isoformat() if t.created_at else "",
                        ]
                    )

            console.print(f"[green]Exported {len(tools)} tools to {output}[/]")

    _run(_export())


@cli.command()
def serve():
    """Run the FastAPI server with scheduler."""
    import uvicorn
    from app.main import app
    from app.scheduler import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()

    console.print("[bold green]Starting server with scheduler...[/]")
    uvicorn.run(app, host="0.0.0.0", port=8000)


@cli.command()
def init_db():
    """Create all database tables (dev only — use Alembic in production)."""

    async def _init():
        from app.database import engine, Base
        from app.models.tool import Tool, ToolTag, ToolSource, ToolMetric  # noqa: F401
        from app.models.ingestion import IngestionRun  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        console.print("[green]Tables created.[/]")

    _run(_init())


if __name__ == "__main__":
    cli()
