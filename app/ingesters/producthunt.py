"""
Product Hunt ingester.

Uses the PH GraphQL API v2 to fetch AI-tagged product launches.
Requires a free developer token from https://www.producthunt.com/v2/oauth/applications
Attribution required per PH API terms.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx

from app.config import settings
from app.ingesters.base import BaseIngester, ToolCandidate

logger = logging.getLogger(__name__)

PH_GRAPHQL = "https://api.producthunt.com/v2/api/graphql"
POSTS_PER_PAGE = 20  # PH API caps at 20; higher values with nested fields exceed complexity limits
MAX_PAGES = 10  # 200 posts max per normal run

# Deep mode: paginate everything across multiple topics
DEEP_MAX_PAGES = 500  # up to 10,000 posts per topic

QUERY_TEMPLATE = """
query($cursor: String) {
  posts(first: %d, after: $cursor, topic: "%s", order: NEWEST) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        website
        votesCount
        createdAt
        topics {
          edges {
            node {
              name
              slug
            }
          }
        }
      }
    }
    pageInfo {
      endCursor
      hasNextPage
    }
  }
}
"""

# Normal mode topic
NORMAL_TOPIC = "artificial-intelligence"

# Deep mode: query multiple AI-related PH topics
DEEP_TOPICS = [
    "artificial-intelligence",
    "machine-learning",
    "chatgpt",
    "developer-tools",
    "natural-language-processing",
    "generative-ai",
]


class ProductHuntIngester(BaseIngester):
    source_name = "producthunt"

    def __init__(self, deep: bool = False):
        self.deep = deep

    async def fetch_candidates(self) -> list[ToolCandidate]:
        if not settings.producthunt_token:
            logger.warning("No Product Hunt token configured, skipping")
            return []

        if self.deep:
            return await self._fetch_deep()
        return await self._fetch_topic(NORMAL_TOPIC, MAX_PAGES)

    async def _fetch_deep(self) -> list[ToolCandidate]:
        """Deep historical pull: multiple topics, unlimited pagination."""
        seen_ids: set[str] = set()
        candidates: list[ToolCandidate] = []

        for topic in DEEP_TOPICS:
            logger.info("ProductHunt deep: fetching topic=%s", topic)
            topic_candidates = await self._fetch_topic(topic, DEEP_MAX_PAGES)

            new_count = 0
            for cand in topic_candidates:
                if cand.source_id not in seen_ids:
                    seen_ids.add(cand.source_id)
                    candidates.append(cand)
                    new_count += 1

            logger.info(
                "ProductHunt deep: topic=%s fetched %d, %d new unique, %d total",
                topic, len(topic_candidates), new_count, len(candidates),
            )

        return candidates

    async def _fetch_topic(self, topic: str, max_pages: int) -> list[ToolCandidate]:
        """Fetch posts for a single topic with pagination."""
        candidates: list[ToolCandidate] = []
        cursor: str | None = None
        query = QUERY_TEMPLATE % (POSTS_PER_PAGE, topic)
        consecutive_429s = 0

        async with httpx.AsyncClient(timeout=30) as client:
            for page in range(max_pages):
                variables: dict = {}
                if cursor:
                    variables["cursor"] = cursor

                try:
                    resp = await client.post(
                        PH_GRAPHQL,
                        json={"query": query, "variables": variables},
                        headers={
                            "Authorization": f"Bearer {settings.producthunt_token}",
                            "Content-Type": "application/json",
                        },
                    )

                    if resp.status_code == 429:
                        consecutive_429s += 1
                        if consecutive_429s >= 3:
                            logger.warning(
                                "ProductHunt: 3 consecutive rate limits for topic=%s, moving on (%d posts so far)",
                                topic, len(candidates),
                            )
                            break
                        wait = 60 * consecutive_429s  # 60s, 120s
                        logger.warning("ProductHunt rate limited at page %d, waiting %ds", page, wait)
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    consecutive_429s = 0  # reset on success
                except httpx.HTTPError as exc:
                    logger.warning("ProductHunt error at page %d: %s", page, exc)
                    break

                data = resp.json()

                # Check for GraphQL errors
                if data.get("errors"):
                    logger.warning("ProductHunt GraphQL errors: %s", data["errors"])
                    break

                posts_data = data.get("data", {}).get("posts", {})
                edges = posts_data.get("edges", [])

                if not edges:
                    break

                for edge in edges:
                    node = edge.get("node", {})
                    cand = self._to_candidate(node)
                    if cand:
                        candidates.append(cand)

                page_info = posts_data.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break
                cursor = page_info.get("endCursor")

                if page % 50 == 0 and page > 0:
                    logger.info("ProductHunt topic=%s page %d: %d candidates", topic, page, len(candidates))

                # Small delay to respect rate limits
                if self.deep:
                    await asyncio.sleep(1.0)

        return candidates

    @staticmethod
    def _to_candidate(node: dict) -> ToolCandidate | None:
        post_id = node.get("id")
        name = node.get("name", "")
        if not post_id or not name:
            return None

        topic_edges = node.get("topics", {}).get("edges", [])
        tags = [e["node"]["slug"] for e in topic_edges if e.get("node", {}).get("slug")]

        created = node.get("createdAt")
        created_dt = None
        if created:
            try:
                created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        description = node.get("description") or node.get("tagline") or ""

        return ToolCandidate(
            source_type="producthunt",
            source_id=str(post_id),
            name=name,
            description=description,
            homepage_url=node.get("website") or node.get("url") or "",
            source_url=node.get("url") or "",
            tags=tags,
            likes=node.get("votesCount", 0) or 0,
            source_metadata={
                "tagline": node.get("tagline"),
                "ph_url": node.get("url"),
            },
            created_at_source=created_dt,
        )
