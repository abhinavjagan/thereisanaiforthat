"""
Static two-level taxonomy: Domain → Categories.

Every AI tool is classified into exactly one (domain, category) pair.
Add new domains/categories here; the classifier and API will pick them up
automatically.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Domain:
    key: str
    name: str
    categories: tuple[str, ...]


DOMAINS: dict[str, Domain] = {
    d.key: d
    for d in [
        Domain(
            key="text",
            name="Text & Language",
            categories=(
                "chatbots-assistants",
                "writing-content",
                "translation",
                "summarization",
                "code-generation",
                "search-retrieval",
            ),
        ),
        Domain(
            key="image",
            name="Image",
            categories=(
                "image-generation",
                "image-editing",
                "image-upscaling",
                "background-removal",
                "face-swap",
                "ocr-recognition",
            ),
        ),
        Domain(
            key="audio",
            name="Audio & Speech",
            categories=(
                "text-to-speech",
                "speech-to-text",
                "voice-cloning",
                "music-generation",
                "audio-enhancement",
                "audio-separation",
            ),
        ),
        Domain(
            key="video",
            name="Video",
            categories=(
                "video-generation",
                "video-editing",
                "video-enhancement",
                "animation",
                "lip-sync",
            ),
        ),
        Domain(
            key="3d",
            name="3D & Spatial",
            categories=(
                "3d-generation",
                "3d-reconstruction",
                "depth-estimation",
            ),
        ),
        Domain(
            key="data",
            name="Data & Analytics",
            categories=(
                "data-analysis",
                "data-visualization",
                "document-processing",
                "financial-analysis",
            ),
        ),
        Domain(
            key="dev",
            name="Developer Tools",
            categories=(
                "ml-frameworks",
                "model-hosting",
                "fine-tuning",
                "evaluation-benchmarks",
                "agents-automation",
                "mcp-tools",
            ),
        ),
        Domain(
            key="multimodal",
            name="Multimodal",
            categories=(
                "vision-language",
                "omni-models",
            ),
        ),
        Domain(
            key="science",
            name="Science & Research",
            categories=(
                "healthcare-medical",
                "robotics",
                "scientific-computing",
            ),
        ),
    ]
}

# Flat set of all valid (domain, category) pairs — used for validation
VALID_PAIRS: set[tuple[str, str]] = {
    (d.key, cat) for d in DOMAINS.values() for cat in d.categories
}
