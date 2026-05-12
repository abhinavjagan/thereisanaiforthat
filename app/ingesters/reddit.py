"""
Reddit ingester.

Fetches posts from AI-related subreddits looking for tool launches and links.
Requires OAuth2 credentials (free script-type app).
"""

from __future__ import annotations

import datetime as dt
import logging
import re

import httpx

from app.config import settings
from app.ingesters.base import BaseIngester, ToolCandidate

logger = logging.getLogger(__name__)

SUBREDDITS = [
    "artificial",
    "MachineLearning",
    "LocalLLaMA",
    "singularity",
    "StableDiffusion",
]
POSTS_PER_SUB = 100

# Domains that are likely tool launches (not just news articles)
TOOL_DOMAIN_PATTERN = re.compile(
    r"github\.com|huggingface\.co|replicate\.com|"
    r"\.ai$|\.dev$|\.app$|\.io$|\.co$",
    re.IGNORECASE,
)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"


class RedditIngester(BaseIngester):
    source_name = "reddit"

    async def fetch_candidates(self) -> list[ToolCandidate]:
        if not settings.reddit_client_id or not settings.reddit_client_secret:
            logger.warning("No Reddit credentials configured, skipping")
            return []

        token = await self._get_token()
        if not token:
            return []

        candidates: list[ToolCandidate] = []
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": settings.reddit_user_agent,
        }

        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            for sub in SUBREDDITS:
                try:
                    resp = await client.get(
                        f"{API_BASE}/r/{sub}/new",
                        params={"limit": POSTS_PER_SUB},
                    )
                    if resp.status_code == 403:
                        logger.warning("Reddit: forbidden for r/%s, skipping", sub)
                        continue
                    resp.raise_for_status()

                    listing = resp.json().get("data", {}).get("children", [])
                    for child in listing:
                        post = child.get("data", {})
                        cand = self._to_candidate(post, sub)
                        if cand:
                            candidates.append(cand)

                except httpx.HTTPError as exc:
                    logger.warning("Reddit: error fetching r/%s: %s", sub, exc)

        logger.debug("Reddit: fetched %d candidates", len(candidates))
        return candidates

    @staticmethod
    async def _get_token() -> str | None:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    TOKEN_URL,
                    data={"grant_type": "client_credentials"},
                    auth=(settings.reddit_client_id, settings.reddit_client_secret),
                    headers={"User-Agent": settings.reddit_user_agent},
                )
                resp.raise_for_status()
                return resp.json().get("access_token")
        except httpx.HTTPError as exc:
            logger.error("Reddit: failed to get token: %s", exc)
            return None

    @staticmethod
    def _to_candidate(post: dict, subreddit: str) -> ToolCandidate | None:
        url = post.get("url", "")
        title = post.get("title", "")
        post_id = post.get("id", "")

        # Only keep link posts pointing to likely tool domains
        if post.get("is_self") or not url:
            return None
        if not TOOL_DOMAIN_PATTERN.search(url):
            return None
        if not post_id:
            return None

        created_utc = post.get("created_utc")
        created_dt = None
        if created_utc:
            created_dt = dt.datetime.fromtimestamp(created_utc, tz=dt.timezone.utc)

        # Derive tags from subreddit
        sub_tags: dict[str, list[str]] = {
            "artificial": ["ai"],
            "MachineLearning": ["machine-learning"],
            "LocalLLaMA": ["llm", "local-inference"],
            "singularity": ["ai"],
            "StableDiffusion": ["stable-diffusion", "image-generation"],
        }
        tags = sub_tags.get(subreddit, ["ai"])

        return ToolCandidate(
            source_type="reddit",
            source_id=f"reddit:{post_id}",
            name=title[:200],
            description=post.get("selftext", "")[:2000] or title,
            homepage_url=url,
            source_url=f"https://reddit.com{post.get('permalink', '')}",
            tags=tags,
            likes=max(post.get("score", 0), 0),
            source_metadata={
                "subreddit": subreddit,
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "upvote_ratio": post.get("upvote_ratio", 0),
            },
            created_at_source=created_dt,
        )
