"""
GitHub repository ingester.

Searches for AI-related repositories via the GitHub REST Search API.
Rotates through multiple topic queries to cover different AI domains.
Rate limit: 30 req/min authenticated, 10 unauthenticated.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging

import httpx

from app.config import settings
from app.ingesters.base import BaseIngester, ToolCandidate

logger = logging.getLogger(__name__)

GITHUB_SEARCH = "https://api.github.com/search/repositories"
PER_PAGE = 100
MAX_PAGES_PER_QUERY = 3  # 300 repos per topic query (normal mode)

# Queries to rotate — each covers a different AI niche
TOPIC_QUERIES = [
    "topic:artificial-intelligence",
    "topic:llm",
    "topic:generative-ai",
    "topic:stable-diffusion",
    "topic:computer-vision",
    "topic:nlp",
    "topic:text-to-speech",
    "topic:machine-learning stars:>100",
    "topic:deep-learning stars:>100",
    "topic:transformers",
]

# Deep mode: many more queries + deeper pagination
DEEP_MAX_PAGES_PER_QUERY = 10  # GitHub caps at 1000 results per query
DEEP_TOPIC_QUERIES = TOPIC_QUERIES + [
    # Additional topics for breadth
    "topic:chatgpt",
    "topic:openai",
    "topic:langchain",
    "topic:vector-database",
    "topic:rag",
    "topic:ai-agents",
    "topic:text-to-image",
    "topic:speech-recognition",
    "topic:ocr",
    "topic:object-detection",
    "topic:image-segmentation",
    "topic:reinforcement-learning",
    "topic:huggingface",
    "topic:pytorch stars:>50",
    "topic:tensorflow stars:>50",
    "topic:whisper",
    "topic:llama",
    "topic:embeddings",
    "topic:semantic-search",
    "topic:ai-assistant",
    "topic:copilot",
    "topic:prompt-engineering",
    "topic:fine-tuning",
    "topic:text-generation",
    "topic:image-generation",
    "topic:voice-cloning",
    "topic:chatbot",
    "topic:neural-network",
    "topic:diffusion-models",
    "topic:transformer",
    "topic:attention-mechanism",
    "topic:bert",
    "topic:gpt",
    "topic:llm-framework",
    "topic:mlops",
    "topic:model-serving",
    "topic:inference",
    "topic:ai-safety",
    # Description-based searches for tools without proper topics
    "AI tool in:description,name stars:>50",
    "LLM framework in:description stars:>50",
    "machine learning tool in:description stars:>100",
    "generative AI in:description stars:>50",
    "text to speech in:description stars:>30",
    "image generation in:description stars:>50",
    "vector database in:description stars:>30",
    "AI agent in:description stars:>30",
    "speech recognition in:description stars:>50",
    "computer vision in:description,name stars:>100",
    "stable diffusion in:description stars:>30",
    "chatbot framework in:description stars:>30",
]


class GitHubIngester(BaseIngester):
    source_name = "github"

    def __init__(self, deep: bool = False):
        self.deep = deep

    async def fetch_candidates(self) -> list[ToolCandidate]:
        candidates: list[ToolCandidate] = []
        seen_ids: set[str] = set()

        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        queries = DEEP_TOPIC_QUERIES if self.deep else TOPIC_QUERIES
        max_pages = DEEP_MAX_PAGES_PER_QUERY if self.deep else MAX_PAGES_PER_QUERY

        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            for qi, query in enumerate(queries):
                for page in range(1, max_pages + 1):
                    resp = await client.get(
                        GITHUB_SEARCH,
                        params={
                            "q": query,
                            "sort": "stars",
                            "order": "desc",
                            "per_page": PER_PAGE,
                            "page": page,
                        },
                    )

                    if resp.status_code == 403:
                        # Rate limit — check reset time and wait
                        reset_ts = resp.headers.get("X-RateLimit-Reset")
                        if reset_ts and self.deep:
                            wait = max(int(reset_ts) - int(dt.datetime.now(dt.timezone.utc).timestamp()), 1)
                            wait = min(wait, 65)  # cap wait at 65 seconds
                            logger.warning(
                                "GitHub rate limit hit at query %d/%d, waiting %ds",
                                qi + 1, len(queries), wait,
                            )
                            await asyncio.sleep(wait)
                            # Retry this page
                            resp = await client.get(
                                GITHUB_SEARCH,
                                params={
                                    "q": query,
                                    "sort": "stars",
                                    "order": "desc",
                                    "per_page": PER_PAGE,
                                    "page": page,
                                },
                            )
                            if resp.status_code == 403:
                                logger.warning("GitHub still rate limited, skipping query")
                                break
                        else:
                            logger.warning("GitHub rate limit hit, stopping current query")
                            break

                    if resp.status_code == 422:
                        # GitHub returns 422 for invalid queries or >1000 offset
                        logger.debug("GitHub 422 for query '%s' page %d, skipping", query, page)
                        break

                    resp.raise_for_status()

                    data = resp.json()
                    items = data.get("items", [])
                    for item in items:
                        full_name = item.get("full_name", "")
                        if full_name in seen_ids:
                            continue
                        seen_ids.add(full_name)
                        cand = self._to_candidate(item)
                        if cand:
                            candidates.append(cand)

                    if len(items) < PER_PAGE:
                        break

                    # Small delay between pages to stay under rate limit
                    if self.deep:
                        await asyncio.sleep(2.5)

                logger.info(
                    "GitHub query %d/%d '%s': %d total unique candidates",
                    qi + 1, len(queries), query[:50], len(candidates),
                )

        return candidates

    @staticmethod
    def _to_candidate(item: dict) -> ToolCandidate | None:
        full_name = item.get("full_name", "")
        if not full_name:
            return None

        name = item.get("name", full_name.split("/")[-1])
        topics = item.get("topics", []) or []
        created = item.get("created_at")
        created_dt = None
        if created:
            try:
                created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        license_info = item.get("license")
        license_name = license_info.get("spdx_id", "") if license_info else ""

        return ToolCandidate(
            source_type="github",
            source_id=full_name,
            name=name,
            description=item.get("description", "") or "",
            homepage_url=item.get("homepage") or item.get("html_url", ""),
            source_url=item.get("html_url", ""),
            tags=topics,
            stars=item.get("stargazers_count", 0) or 0,
            trending_score=0,
            source_metadata={
                "full_name": full_name,
                "language": item.get("language"),
                "license": license_name,
                "forks": item.get("forks_count", 0),
                "open_issues": item.get("open_issues_count", 0),
                "archived": item.get("archived", False),
            },
            created_at_source=created_dt,
        )
