"""
Hacker News ingester.

Uses the free Firebase HN API to find AI tool launches from Show HN and new stories.
Deep mode uses the Algolia HN Search API for full historical search.
No authentication required.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re

import httpx

from app.ingesters.base import BaseIngester, ToolCandidate

logger = logging.getLogger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"
MAX_STORIES = 500  # check the 500 newest per run
BATCH_SIZE = 20  # concurrent item fetches

# Algolia HN Search API (free, no auth, full history)
ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
ALGOLIA_MAX_PAGES = 10  # 10 pages x 100 hits = 1000 per query (Algolia limit)

# Queries for deep historical search via Algolia
ALGOLIA_QUERIES = [
    "AI tool", "AI app", "LLM", "GPT", "ChatGPT", "OpenAI",
    "machine learning tool", "deep learning", "generative AI",
    "stable diffusion", "text to speech", "speech to text",
    "chatbot", "langchain", "vector database", "RAG",
    "fine tuning", "huggingface", "text to image", "voice cloning",
    "whisper", "computer vision tool", "AI agent", "AI assistant",
    "open source model", "transformer model", "neural network tool",
    "NLP tool", "image generation", "code generation AI",
]

# Keywords that suggest an AI tool (case-insensitive)
AI_KEYWORDS = re.compile(
    r"\b(?:AI|LLM|GPT|machine[\s-]?learning|neural|deep[\s-]?learning|"
    r"generative|diffusion|transformer|text[\s-]?to[\s-]?speech|"
    r"speech[\s-]?to[\s-]?text|computer[\s-]?vision|"
    r"stable[\s-]?diffusion|hugging[\s-]?face|open[\s-]?source[\s-]?model|"
    r"chatbot|langchain|vector[\s-]?db|RAG|fine[\s-]?tun|"
    r"text[\s-]?to[\s-]?image|voice[\s-]?clon|whisper|agent|copilot|"
    r"gemini|claude|llama|mistral|ollama|embedding)\b",
    re.IGNORECASE,
)


class HackerNewsIngester(BaseIngester):
    source_name = "hackernews"

    def __init__(self, deep: bool = False):
        self.deep = deep

    async def fetch_candidates(self) -> list[ToolCandidate]:
        if self.deep:
            return await self._fetch_deep()
        return await self._fetch_normal()

    async def _fetch_normal(self) -> list[ToolCandidate]:
        candidates: list[ToolCandidate] = []
        async with httpx.AsyncClient(timeout=20) as client:
            # Fetch Show HN + newest story IDs
            show_ids = await self._fetch_ids(client, "showstories")
            new_ids = await self._fetch_ids(client, "newstories")

            # Merge and take first MAX_STORIES
            all_ids = list(dict.fromkeys(show_ids + new_ids))[:MAX_STORIES]

            # Fetch items in batches
            for i in range(0, len(all_ids), BATCH_SIZE):
                batch = all_ids[i : i + BATCH_SIZE]
                items = await self._fetch_items(client, batch)
                for item in items:
                    cand = self._to_candidate(item)
                    if cand:
                        candidates.append(cand)

        logger.debug("HackerNews: fetched %d candidates", len(candidates))
        return candidates

    async def _fetch_deep(self) -> list[ToolCandidate]:
        """Deep historical pull using Algolia HN Search API."""
        seen_ids: set[str] = set()
        candidates: list[ToolCandidate] = []

        async with httpx.AsyncClient(timeout=30) as client:
            for query in ALGOLIA_QUERIES:
                # Search both Show HN and regular stories
                for tags in ["show_hn", "story"]:
                    for page in range(ALGOLIA_MAX_PAGES):
                        try:
                            resp = await client.get(
                                ALGOLIA_SEARCH,
                                params={
                                    "query": query,
                                    "tags": tags,
                                    "hitsPerPage": 100,
                                    "page": page,
                                    "numericFilters": "points>5",
                                },
                            )
                            resp.raise_for_status()
                        except httpx.HTTPError as exc:
                            logger.warning(
                                "Algolia error q=%s tags=%s page=%d: %s",
                                query, tags, page, exc,
                            )
                            break

                        data = resp.json()
                        hits = data.get("hits", [])
                        if not hits:
                            break

                        for hit in hits:
                            hn_id = str(hit.get("objectID", ""))
                            if hn_id in seen_ids:
                                continue
                            seen_ids.add(hn_id)
                            cand = self._algolia_to_candidate(hit)
                            if cand:
                                candidates.append(cand)

                        nb_pages = data.get("nbPages", 0)
                        if page + 1 >= nb_pages:
                            break
                        await asyncio.sleep(0.15)  # rate limiting

                logger.info(
                    "HackerNews deep: query=%r done, %d total unique candidates",
                    query, len(candidates),
                )

        logger.info("HackerNews deep: total %d candidates", len(candidates))
        return candidates

    @staticmethod
    async def _fetch_ids(client: httpx.AsyncClient, endpoint: str) -> list[int]:
        resp = await client.get(f"{HN_BASE}/{endpoint}.json")
        resp.raise_for_status()
        return resp.json() or []

    @staticmethod
    async def _fetch_items(client: httpx.AsyncClient, ids: list[int]) -> list[dict]:
        items: list[dict] = []
        for item_id in ids:
            try:
                resp = await client.get(f"{HN_BASE}/item/{item_id}.json")
                resp.raise_for_status()
                data = resp.json()
                if data and data.get("type") == "story" and not data.get("deleted"):
                    items.append(data)
            except httpx.HTTPError:
                continue
        return items

    @staticmethod
    def _algolia_to_candidate(hit: dict) -> ToolCandidate | None:
        """Convert an Algolia search hit to a ToolCandidate."""
        title = hit.get("title", "")
        url = hit.get("url", "")

        # Only keep stories with an external URL
        if not url:
            return None

        # Filter by AI keywords
        if not AI_KEYWORDS.search(title):
            return None

        hn_id = str(hit.get("objectID", ""))
        created = hit.get("created_at")
        created_dt = None
        if created:
            try:
                created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Extract tags from the title
        tags: list[str] = []
        lower_title = title.lower()
        for kw in ["llm", "gpt", "ai", "ml", "diffusion", "tts", "asr", "ocr",
                    "rag", "agent", "chatbot", "whisper", "langchain",
                    "embedding", "copilot"]:
            if kw in lower_title:
                tags.append(kw)
        if "show hn" in lower_title:
            tags.append("show-hn")

        points = hit.get("points", 0) or 0
        num_comments = hit.get("num_comments", 0) or 0

        return ToolCandidate(
            source_type="hackernews",
            source_id=hn_id,
            name=title.replace("Show HN: ", "").replace("Launch HN: ", "").strip()[:200],
            description=title,
            homepage_url=url,
            source_url=f"https://news.ycombinator.com/item?id={hn_id}",
            tags=tags,
            likes=points,
            source_metadata={
                "hn_id": hn_id,
                "score": points,
                "descendants": num_comments,
            },
            created_at_source=created_dt,
        )

    @staticmethod
    def _to_candidate(item: dict) -> ToolCandidate | None:
        title = item.get("title", "")
        url = item.get("url", "")

        # Only keep stories that look AI-related
        if not AI_KEYWORDS.search(title):
            return None

        # Skip stories without an external URL (self-posts)
        if not url:
            return None

        hn_id = str(item.get("id", ""))
        timestamp = item.get("time")
        created_dt = None
        if timestamp:
            created_dt = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)

        # Extract tags from the title via keyword matching
        tags: list[str] = []
        lower_title = title.lower()
        for kw in ["llm", "gpt", "ai", "ml", "diffusion", "tts", "asr", "ocr", "rag", "agent"]:
            if kw in lower_title:
                tags.append(kw)
        if "show hn" in lower_title:
            tags.append("show-hn")

        return ToolCandidate(
            source_type="hackernews",
            source_id=hn_id,
            name=title.replace("Show HN: ", "").replace("Launch HN: ", "").strip()[:200],
            description=title,
            homepage_url=url,
            source_url=f"https://news.ycombinator.com/item?id={hn_id}",
            tags=tags,
            likes=item.get("score", 0) or 0,
            source_metadata={
                "hn_id": hn_id,
                "score": item.get("score", 0),
                "descendants": item.get("descendants", 0),
            },
            created_at_source=created_dt,
        )
