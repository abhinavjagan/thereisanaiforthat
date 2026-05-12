"""Server-rendered web routes for the frontend dashboard."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.models.tool import Tool, ToolTag, ToolSource, ToolMetric
from app.taxonomy.domains import DOMAINS

templates = Jinja2Templates(directory="app/templates")

web = APIRouter()


async def _get_stats(session: AsyncSession) -> dict:
    total = (await session.scalar(select(func.count(Tool.id)))) or 0
    active = (await session.scalar(
        select(func.count(Tool.id)).where(Tool.is_active.is_(True))
    )) or 0
    avg = await session.scalar(
        select(func.avg(Tool.legitimacy_score)).where(Tool.legitimacy_score.isnot(None))
    )
    domain_rows = (await session.execute(
        select(Tool.domain, func.count(Tool.id))
        .where(Tool.domain.isnot(None))
        .group_by(Tool.domain)
    )).all()
    source_rows = (await session.execute(
        select(ToolSource.source_type, func.count(ToolSource.id))
        .group_by(ToolSource.source_type)
    )).all()
    return {
        "total_tools": total,
        "active_tools": active,
        "avg_legitimacy": round(avg, 1) if avg else None,
        "domains": {r[0]: r[1] for r in domain_rows},
        "sources": {r[0]: r[1] for r in source_rows},
    }


@web.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_session)):
    stats = await _get_stats(session)

    # Trending
    subq = (
        select(
            ToolMetric.tool_id,
            func.max(ToolMetric.trending_score).label("max_ts"),
        )
        .where(ToolMetric.trending_score.isnot(None), ToolMetric.trending_score > 0)
        .group_by(ToolMetric.tool_id)
        .subquery()
    )
    trending = (await session.execute(
        select(Tool)
        .join(subq, Tool.id == subq.c.tool_id)
        .where(Tool.is_active.is_(True))
        .order_by(desc(subq.c.max_ts))
        .limit(12)
    )).scalars().all()

    # Latest
    latest = (await session.execute(
        select(Tool)
        .where(Tool.is_active.is_(True))
        .order_by(desc(Tool.created_at))
        .limit(12)
    )).scalars().all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_page": "home",
        "stats": stats,
        "trending": trending,
        "latest": latest,
        "total_tools": stats["total_tools"],
    })


@web.get("/browse", response_class=HTMLResponse)
async def browse(
    request: Request,
    session: AsyncSession = Depends(get_session),
    domain: Optional[str] = None,
    category: Optional[str] = None,
    sort: str = Query("newest", pattern="^(newest|legitimacy|name)$"),
    min_legitimacy: Optional[int] = Query(None, ge=0, le=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(36, ge=1, le=100),
):
    query = select(Tool).where(Tool.is_active.is_(True))

    if domain:
        query = query.where(Tool.domain == domain)
    if category:
        query = query.where(Tool.category == category)
    if min_legitimacy is not None:
        query = query.where(Tool.legitimacy_score >= min_legitimacy)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await session.scalar(count_q)) or 0

    if sort == "legitimacy":
        query = query.order_by(desc(Tool.legitimacy_score))
    elif sort == "name":
        query = query.order_by(Tool.name)
    else:
        query = query.order_by(desc(Tool.created_at))

    query = query.offset((page - 1) * page_size).limit(page_size)
    tools = (await session.execute(query)).scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    # Domain counts for the filter dropdown
    domain_rows = (await session.execute(
        select(Tool.domain, func.count(Tool.id))
        .where(Tool.domain.isnot(None))
        .group_by(Tool.domain)
    )).all()
    domain_counts = {r[0]: r[1] for r in domain_rows}

    # Categories for selected domain
    categories = []
    if domain and domain in DOMAINS:
        categories = list(DOMAINS[domain].categories)

    all_domains = list(DOMAINS.values())

    def pagination_url(p: int) -> str:
        params = {}
        if domain:
            params["domain"] = domain
        if category:
            params["category"] = category
        if sort != "newest":
            params["sort"] = sort
        if min_legitimacy is not None:
            params["min_legitimacy"] = min_legitimacy
        params["page"] = p
        return f"/browse?{urlencode(params)}"

    return templates.TemplateResponse("browse.html", {
        "request": request,
        "active_page": "browse",
        "tools": tools,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "domain": domain,
        "category": category,
        "sort": sort,
        "min_legitimacy": min_legitimacy,
        "all_domains": all_domains,
        "domain_counts": domain_counts,
        "categories": categories,
        "pagination_url": pagination_url,
        "total_tools": total,
    })


@web.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    q: str = Query("", min_length=0, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(36, ge=1, le=100),
):
    if not q:
        return templates.TemplateResponse("browse.html", {
            "request": request,
            "active_page": "browse",
            "tools": [],
            "total": 0,
            "page": 1,
            "page_size": page_size,
            "total_pages": 1,
            "domain": None,
            "category": None,
            "sort": "newest",
            "min_legitimacy": None,
            "all_domains": list(DOMAINS.values()),
            "domain_counts": {},
            "categories": [],
            "q": q,
            "pagination_url": lambda p: f"/search?q={q}&page={p}",
            "total_tools": 0,
        })

    pattern = f"%{q}%"
    query = (
        select(Tool)
        .where(
            Tool.is_active.is_(True),
            (Tool.name.ilike(pattern)) | (Tool.description.ilike(pattern)),
        )
        .order_by(desc(Tool.legitimacy_score))
    )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await session.scalar(count_q)) or 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    query = query.offset((page - 1) * page_size).limit(page_size)
    tools = (await session.execute(query)).scalars().all()

    domain_rows = (await session.execute(
        select(Tool.domain, func.count(Tool.id))
        .where(Tool.domain.isnot(None))
        .group_by(Tool.domain)
    )).all()

    return templates.TemplateResponse("browse.html", {
        "request": request,
        "active_page": "browse",
        "tools": tools,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "domain": None,
        "category": None,
        "sort": "legitimacy",
        "min_legitimacy": None,
        "all_domains": list(DOMAINS.values()),
        "domain_counts": {r[0]: r[1] for r in domain_rows},
        "categories": [],
        "q": q,
        "pagination_url": lambda p: f"/search?q={q}&page={p}",
        "total_tools": total,
    })


@web.get("/tool/{slug}", response_class=HTMLResponse)
async def tool_detail(
    request: Request,
    slug: str,
    session: AsyncSession = Depends(get_session),
):
    query = (
        select(Tool)
        .options(
            selectinload(Tool.tags),
            selectinload(Tool.sources),
            selectinload(Tool.metrics),
        )
        .where(Tool.slug == slug)
    )
    tool = (await session.execute(query)).scalar_one_or_none()
    if not tool:
        return HTMLResponse("<h1>Tool not found</h1>", status_code=404)

    return templates.TemplateResponse("tool_detail.html", {
        "request": request,
        "active_page": "browse",
        "tool": tool,
        "total_tools": None,
    })


@web.get("/domains", response_class=HTMLResponse)
async def domains_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    domain_rows = (await session.execute(
        select(Tool.domain, func.count(Tool.id))
        .where(Tool.domain.isnot(None))
        .group_by(Tool.domain)
    )).all()
    domain_counts = {r[0]: r[1] for r in domain_rows}

    return templates.TemplateResponse("domains.html", {
        "request": request,
        "active_page": "domains",
        "domains": list(DOMAINS.values()),
        "domain_counts": domain_counts,
        "total_tools": sum(domain_counts.values()),
    })
