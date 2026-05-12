"""FastAPI route definitions."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from slugify import slugify

from app.database import get_session
from app.models.tool import Tool, ToolTag, ToolSource, ToolMetric
from app.taxonomy.domains import DOMAINS
from app.taxonomy.classifier import classify_tool
from app.api.schemas import (
    ToolSummary,
    ToolDetail,
    TagOut,
    SourceOut,
    MetricOut,
    DomainOut,
    PaginatedTools,
    StatsOut,
    ToolSubmission,
)

router = APIRouter(prefix="/api")


# ---- helpers ----


def _tool_to_detail(tool: Tool) -> ToolDetail:
    return ToolDetail(
        id=tool.id,
        name=tool.name,
        slug=tool.slug,
        summary=tool.summary,
        description=tool.description,
        domain=tool.domain,
        category=tool.category,
        pricing_type=tool.pricing_type,
        legitimacy_score=tool.legitimacy_score,
        homepage_url=tool.homepage_url,
        created_at=tool.created_at,
        updated_at=tool.updated_at,
        tags=[TagOut(tag=t.tag) for t in tool.tags],
        sources=[
            SourceOut(
                source_type=s.source_type,
                source_id=s.source_id,
                source_url=s.source_url,
                fetched_at=s.fetched_at,
            )
            for s in tool.sources
        ],
        metrics=[
            MetricOut(
                source=m.source,
                stars=m.stars,
                likes=m.likes,
                trending_score=m.trending_score,
                recorded_at=m.recorded_at,
            )
            for m in tool.metrics
        ],
    )


# ---- endpoints ----


@router.get("/tools", response_model=PaginatedTools)
async def list_tools(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    domain: Optional[str] = None,
    category: Optional[str] = None,
    pricing_type: Optional[str] = None,
    min_legitimacy: Optional[int] = Query(None, ge=0, le=100),
    sort: str = Query("newest", pattern="^(newest|legitimacy|name)$"),
):
    query = select(Tool).where(Tool.is_active.is_(True))

    if domain:
        query = query.where(Tool.domain == domain)
    if category:
        query = query.where(Tool.category == category)
    if pricing_type:
        query = query.where(Tool.pricing_type == pricing_type)
    if min_legitimacy is not None:
        query = query.where(Tool.legitimacy_score >= min_legitimacy)

    # Total count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await session.scalar(count_q)) or 0

    # Sorting
    if sort == "legitimacy":
        query = query.order_by(desc(Tool.legitimacy_score))
    elif sort == "name":
        query = query.order_by(Tool.name)
    else:
        query = query.order_by(desc(Tool.created_at))

    # Pagination
    query = query.offset((page - 1) * page_size).limit(page_size)
    tools = (await session.execute(query)).scalars().all()

    return PaginatedTools(
        items=[ToolSummary.model_validate(t) for t in tools],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/tools/search", response_model=PaginatedTools)
async def search_tools(
    q: str = Query(..., min_length=2, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    """Simple ILIKE search on name and description."""
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

    query = query.offset((page - 1) * page_size).limit(page_size)
    tools = (await session.execute(query)).scalars().all()

    return PaginatedTools(
        items=[ToolSummary.model_validate(t) for t in tools],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/tools/trending", response_model=list[ToolSummary])
async def trending_tools(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
):
    """Tools with highest recent trending scores."""
    from sqlalchemy.orm import aliased

    subq = (
        select(
            ToolMetric.tool_id,
            func.max(ToolMetric.trending_score).label("max_trending"),
        )
        .where(ToolMetric.trending_score.isnot(None))
        .group_by(ToolMetric.tool_id)
        .subquery()
    )

    query = (
        select(Tool)
        .join(subq, Tool.id == subq.c.tool_id)
        .where(Tool.is_active.is_(True))
        .order_by(desc(subq.c.max_trending))
        .limit(limit)
    )

    tools = (await session.execute(query)).scalars().all()
    return [ToolSummary.model_validate(t) for t in tools]


@router.get("/tools/{slug}", response_model=ToolDetail)
async def get_tool(slug: str, session: AsyncSession = Depends(get_session)):
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
        raise HTTPException(status_code=404, detail="Tool not found")
    return _tool_to_detail(tool)


@router.get("/domains", response_model=list[DomainOut])
async def list_domains():
    return [
        DomainOut(key=d.key, name=d.name, categories=list(d.categories))
        for d in DOMAINS.values()
    ]


@router.get("/stats", response_model=StatsOut)
async def get_stats(session: AsyncSession = Depends(get_session)):
    total = (await session.scalar(select(func.count(Tool.id)))) or 0
    active = (
        await session.scalar(
            select(func.count(Tool.id)).where(Tool.is_active.is_(True))
        )
    ) or 0
    avg_leg = await session.scalar(
        select(func.avg(Tool.legitimacy_score)).where(
            Tool.legitimacy_score.isnot(None)
        )
    )

    # Domain breakdown
    domain_rows = (
        await session.execute(
            select(Tool.domain, func.count(Tool.id))
            .where(Tool.domain.isnot(None))
            .group_by(Tool.domain)
        )
    ).all()

    # Source breakdown
    source_rows = (
        await session.execute(
            select(ToolSource.source_type, func.count(ToolSource.id)).group_by(
                ToolSource.source_type
            )
        )
    ).all()

    return StatsOut(
        total_tools=total,
        active_tools=active,
        domains={r[0]: r[1] for r in domain_rows},
        sources={r[0]: r[1] for r in source_rows},
        avg_legitimacy=round(avg_leg, 1) if avg_leg else None,
    )


@router.post("/tools/submit", response_model=ToolDetail, status_code=201)
async def submit_tool(
    body: ToolSubmission, session: AsyncSession = Depends(get_session)
):
    """Manual submission of a tool (community contributions)."""
    slug = slugify(body.name)

    existing = await session.scalar(select(Tool).where(Tool.slug == slug))
    if existing:
        raise HTTPException(status_code=409, detail="Tool already exists")

    domain, category = classify_tool(body.tags, body.name, body.description or "")

    tool = Tool(
        name=body.name,
        slug=slug,
        description=body.description,
        homepage_url=body.homepage_url,
        domain=domain,
        category=category,
    )
    session.add(tool)
    await session.flush()

    for tag_str in body.tags:
        session.add(ToolTag(tool_id=tool.id, tag=tag_str.lower().strip()))

    session.add(
        ToolSource(
            tool_id=tool.id,
            source_type="manual",
            source_id=f"manual:{slug}",
            source_url=body.homepage_url,
        )
    )

    await session.commit()

    # Reload with relations
    query = (
        select(Tool)
        .options(
            selectinload(Tool.tags),
            selectinload(Tool.sources),
            selectinload(Tool.metrics),
        )
        .where(Tool.id == tool.id)
    )
    tool = (await session.execute(query)).scalar_one()
    return _tool_to_detail(tool)
