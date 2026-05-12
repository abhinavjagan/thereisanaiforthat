"""
HuggingFace Spaces ingester.

API: GET https://huggingface.co/api/spaces
No authentication required. Returns JSON array of spaces with tags, likes,
trending scores.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx

from app.ingesters.base import BaseIngester, ToolCandidate

logger = logging.getLogger(__name__)

HF_API = "https://huggingface.co/api/spaces"
PAGE_LIMIT = 100
MAX_PAGES = 20  # 2000 spaces max per normal run

# Deep mode settings
DEEP_MAX_PAGES = 100  # 10,000 per sort order
DEEP_SORT_ORDERS = ["trendingScore", "likes", "createdAt"]
DESCRIPTION_BATCH = 10  # concurrent detail fetches


class HuggingFaceIngester(BaseIngester):
    source_name = "huggingface"

    def __init__(self, deep: bool = False):
        self.deep = deep

    async def fetch_candidates(self) -> list[ToolCandidate]:
        if self.deep:
            return await self._fetch_deep()
        return await self._fetch_normal()

    async def _fetch_normal(self) -> list[ToolCandidate]:
        candidates: list[ToolCandidate] = []
        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(MAX_PAGES):
                offset = page * PAGE_LIMIT
                resp = await client.get(
                    HF_API,
                    params={"limit": PAGE_LIMIT, "sort": "trendingScore", "offset": offset},
                )
                resp.raise_for_status()
                items = resp.json()
                if not items:
                    break

                for item in items:
                    cand = self._to_candidate(item)
                    if cand:
                        candidates.append(cand)

                logger.debug("HuggingFace page %d: %d items", page, len(items))
                if len(items) < PAGE_LIMIT:
                    break

        return candidates

    async def _fetch_deep(self) -> list[ToolCandidate]:
        """Deep historical pull: multiple sort orders + individual detail fetches."""
        seen_ids: set[str] = set()
        candidates: list[ToolCandidate] = []

        async with httpx.AsyncClient(timeout=30) as client:
            for sort_key in DEEP_SORT_ORDERS:
                logger.info("HuggingFace deep: fetching sort=%s", sort_key)
                for page in range(DEEP_MAX_PAGES):
                    offset = page * PAGE_LIMIT
                    try:
                        resp = await client.get(
                            HF_API,
                            params={"limit": PAGE_LIMIT, "sort": sort_key, "offset": offset},
                        )
                        resp.raise_for_status()
                    except httpx.HTTPError as exc:
                        logger.warning("HuggingFace page %d sort=%s error: %s", page, sort_key, exc)
                        break

                    items = resp.json()
                    if not items:
                        break

                    for item in items:
                        space_id = item.get("id", "")
                        if space_id in seen_ids:
                            continue
                        seen_ids.add(space_id)
                        cand = self._to_candidate(item)
                        if cand:
                            candidates.append(cand)

                    if page % 10 == 0:
                        logger.info(
                            "HuggingFace deep sort=%s page %d: %d total unique candidates",
                            sort_key, page, len(candidates),
                        )
                    if len(items) < PAGE_LIMIT:
                        break
                    # Small delay to be respectful
                    await asyncio.sleep(0.2)

            # Fetch descriptions for candidates that don't have one
            logger.info("HuggingFace deep: fetching descriptions for %d spaces", len(candidates))
            await self._fill_descriptions(client, candidates)

        return candidates

    async def _fill_descriptions(
        self, client: httpx.AsyncClient, candidates: list[ToolCandidate]
    ) -> None:
        """Fetch individual space details to get descriptions."""
        need_desc = [c for c in candidates if not c.description]
        for i in range(0, len(need_desc), DESCRIPTION_BATCH):
            batch = need_desc[i : i + DESCRIPTION_BATCH]
            tasks = [self._fetch_description(client, c) for c in batch]
            await asyncio.gather(*tasks)
            if i % 100 == 0 and i > 0:
                logger.info("HuggingFace deep: fetched %d/%d descriptions", i, len(need_desc))
            await asyncio.sleep(0.1)

    @staticmethod
    async def _fetch_description(client: httpx.AsyncClient, cand: ToolCandidate) -> None:
        """Fetch a single space's detail for its card/description."""
        space_id = cand.source_id
        try:
            resp = await client.get(f"https://huggingface.co/api/spaces/{space_id}")
            if resp.status_code == 200:
                data = resp.json()
                card = data.get("cardData", {}) or {}
                desc = card.get("short_description") or card.get("title") or ""
                if desc:
                    cand.description = desc[:5000]
        except httpx.HTTPError:
            pass

    @staticmethod
    def _to_candidate(item: dict) -> ToolCandidate | None:
        space_id = item.get("id", "")
        if not space_id:
            return None

        # Space ID is "owner/name" — use the name portion for display
        name = space_id.split("/")[-1] if "/" in space_id else space_id

        tags = [t for t in item.get("tags", []) if isinstance(t, str)]
        sdk = item.get("sdk", "")
        created = item.get("createdAt")
        created_dt = None
        if created:
            try:
                created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return ToolCandidate(
            source_type="huggingface",
            source_id=space_id,
            name=name,
            description="",  # HF spaces list endpoint doesn't return descriptions
            homepage_url=f"https://huggingface.co/spaces/{space_id}",
            source_url=f"https://huggingface.co/spaces/{space_id}",
            tags=tags,
            likes=item.get("likes", 0) or 0,
            trending_score=item.get("trendingScore", 0) or 0,
            source_metadata={
                "sdk": sdk,
                "private": item.get("private", False),
                "hf_id": space_id,
            },
            created_at_source=created_dt,
        )
