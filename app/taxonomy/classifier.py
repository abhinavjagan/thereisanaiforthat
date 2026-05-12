"""
Deterministic classifier: tags + name + description → (domain, category).

No LLM involved — pure tag mapping + keyword regex fallback.
"""

from __future__ import annotations

import re
from collections import Counter

from app.taxonomy.tag_map import SKIP_TAGS, TAG_MAP

# Keyword patterns applied to name + description when tag mapping fails.
# Order matters — first match wins.
_KEYWORD_RULES: list[tuple[re.Pattern[str], tuple[str, str]]] = [
    # ── Image ───────────────────────────────────────────────────────
    (re.compile(r"\bface[\s-]?swap", re.I), ("image", "face-swap")),
    (re.compile(r"\bbackground[\s-]?remov", re.I), ("image", "background-removal")),
    (re.compile(r"\bocr\b", re.I), ("image", "ocr-recognition")),
    (re.compile(r"\bupscal", re.I), ("image", "image-upscaling")),
    (re.compile(r"\bsuper[\s-]?resol", re.I), ("image", "image-upscaling")),
    (re.compile(r"\binpaint", re.I), ("image", "image-editing")),
    (re.compile(r"\btext[\s-]?to[\s-]?image\b|\bimage[\s-]?gen", re.I), ("image", "image-generation")),
    (re.compile(r"\bimage[\s-]?edit", re.I), ("image", "image-editing")),
    (re.compile(r"\bimage[\s-]?classif|\bobject[\s-]?detect|\bcomputer[\s-]?vision\b|\bimage[\s-]?recogn", re.I), ("image", "image-generation")),

    # ── Audio ───────────────────────────────────────────────────────
    (re.compile(r"\btext[\s-]?to[\s-]?speech\b|\btts\b", re.I), ("audio", "text-to-speech")),
    (re.compile(r"\bspeech[\s-]?to[\s-]?text\b|\basr\b|\btranscri|\bspeech[\s-]?recogn", re.I), ("audio", "speech-to-text")),
    (re.compile(r"\bvoice[\s-]?clon", re.I), ("audio", "voice-cloning")),
    (re.compile(r"\bmusic[\s-]?gen|\bmusic[\s-]?comp", re.I), ("audio", "music-generation")),

    # ── Video ───────────────────────────────────────────────────────
    (re.compile(r"\btext[\s-]?to[\s-]?video\b|\bvideo[\s-]?gen", re.I), ("video", "video-generation")),
    (re.compile(r"\bvideo[\s-]?edit", re.I), ("video", "video-editing")),
    (re.compile(r"\blip[\s-]?sync", re.I), ("video", "lip-sync")),

    # ── 3D ──────────────────────────────────────────────────────────
    (re.compile(r"\b3d[\s-]?gen|\bmesh[\s-]?gen", re.I), ("3d", "3d-generation")),
    (re.compile(r"\bdepth[\s-]?est", re.I), ("3d", "depth-estimation")),

    # ── Text / Language ─────────────────────────────────────────────
    (re.compile(r"\bchatbot|\bassistant|\bconvers", re.I), ("text", "chatbots-assistants")),
    (re.compile(r"\bcode[\s-]?gen|\bcoding[\s-]?assist|\bcode[\s-]?complet|\bauto[\s-]?complet.*code", re.I), ("text", "code-generation")),
    (re.compile(r"\btranslat", re.I), ("text", "translation")),
    (re.compile(r"\bsummar", re.I), ("text", "summarization")),
    (re.compile(r"\bcopywriting\b|\bcontent[\s-]?writ|\bblog[\s-]?writ|\btext[\s-]?gen", re.I), ("text", "writing-content")),
    (re.compile(r"\bsearch[\s-]?engine\b|\bsemantic[\s-]?search\b|\bvector[\s-]?search\b|\bRAG\b", re.I), ("text", "search-retrieval")),
    (re.compile(r"\bprompt[\s-]?engineer|\bprompt[\s-]?template|\bprompt[\s-]?manag", re.I), ("text", "chatbots-assistants")),
    (re.compile(r"\bchatgpt\b|\bgpt[\s-]?\d|\bopenai\b|\bclaude\b|\bgemini\b|\bllama\b|\bmistral\b", re.I), ("text", "chatbots-assistants")),

    # ── Developer / Infrastructure ──────────────────────────────────
    (re.compile(r"\bleaderboard\b|\bbenchmark\b|\beval", re.I), ("dev", "evaluation-benchmarks")),
    (re.compile(r"\bagent\b|\bagents\b|\bagentic\b|\bautonomous\b", re.I), ("dev", "agents-automation")),
    (re.compile(r"\bfine[\s-]?tun", re.I), ("dev", "fine-tuning")),
    (re.compile(r"\bllm\b|\blarge[\s-]?language", re.I), ("text", "chatbots-assistants")),
    (re.compile(r"\bdiffusion\b|\bflux\b|\bstable[\s-]?diffusion\b", re.I), ("image", "image-generation")),
    (re.compile(r"\bmlops\b|\bmodel[\s-]?deploy|\bmodel[\s-]?serv|\binference[\s-]?server", re.I), ("dev", "model-hosting")),
    (re.compile(r"\bvector[\s-]?database\b|\bembedding", re.I), ("text", "search-retrieval")),
    (re.compile(r"\breinforcement[\s-]?learn|\brl[\s-]?env", re.I), ("dev", "ml-frameworks")),
    (re.compile(r"\bmcp[\s-]?server\b|\bmodel[\s-]?context[\s-]?protocol", re.I), ("dev", "mcp-tools")),
    (re.compile(r"\bneural[\s-]?net|\bdeep[\s-]?learn|\bmachine[\s-]?learn|\bml[\s-]tool|\bml[\s-]?framework", re.I), ("dev", "ml-frameworks")),
    (re.compile(r"\btransform(?:er|ers)\b|\battention[\s-]?mechanism\b", re.I), ("dev", "ml-frameworks")),
    (re.compile(r"\bpytorch\b|\btensorflow\b|\bkeras\b|\bjax\b", re.I), ("dev", "ml-frameworks")),
    (re.compile(r"\bnlp\b|\bnatural[\s-]?language\b|\bsentiment\b|\btoken", re.I), ("text", "chatbots-assistants")),
    (re.compile(r"\b(?:self[\s-]?driv|autonom.*(?:car|vehicle|driv))", re.I), ("science", "robotics")),
    (re.compile(r"\brobot", re.I), ("science", "robotics")),

    # ── Data / Science ──────────────────────────────────────────────
    (re.compile(r"\bdata[\s-]?analy|\bdata[\s-]?visual|\bdashboard", re.I), ("data", "data-analysis")),
    (re.compile(r"\bpdf\b|\bdocument[\s-]?process|\bdocument[\s-]?extract", re.I), ("data", "document-processing")),
    (re.compile(r"\bmedic|\bhealth|\bdrug[\s-]?discov|\bclinical", re.I), ("science", "healthcare-medical")),

    # ── Catch-all: broad AI/ML terms → dev ──────────────────────────
    (re.compile(r"\bA\.?I\.?\b", re.I), ("dev", "ml-frameworks")),
]


def classify_tool(
    tags: list[str],
    name: str = "",
    description: str = "",
) -> tuple[str | None, str | None]:
    """Return (domain, category) for a tool, or (None, None) if unclassifiable.

    Strategy:
      1. Map each source tag through TAG_MAP; take the (domain, category) with
         the highest vote count.
      2. If no tag matches, fall back to keyword regex on name + description.
      3. If still nothing, return (None, None) → routed to manual review.
    """
    # ── Step 1: tag voting ──────────────────────────────────────────
    votes: Counter[tuple[str, str]] = Counter()
    for raw_tag in tags:
        tag = raw_tag.lower().strip()
        if tag in SKIP_TAGS:
            continue
        result = TAG_MAP.get(tag)
        if result:
            votes[result] += 1

    if votes:
        winner = votes.most_common(1)[0][0]
        return winner

    # ── Step 2: keyword regex fallback ──────────────────────────────
    text = f"{name} {description}"
    for pattern, pair in _KEYWORD_RULES:
        if pattern.search(text):
            return pair

    # ── Step 3: give up ─────────────────────────────────────────────
    return (None, None)
