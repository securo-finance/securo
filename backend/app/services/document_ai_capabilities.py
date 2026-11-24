"""Does the configured model read images?

Text PDFs are read from their own text and work with any model. A photo, or a
PDF whose pages are only scans, has to be sent as images, and many models
cannot take them. Securo answers the question before sending anything, in
this order:

1. The setting, when someone said "true" or "false" (the workspace's, then
   the instance's DOCUMENT_AI_VISION).
2. Ollama itself: `/api/show` lists the model's capabilities, `vision`
   among them, on every Ollama recent enough to run a vision model.
3. The model's name, against the families below.

When none of these knows, the answer is "unknown": Securo tries, and a
provider that refuses the image is reported as the same "needs a model that
reads images" the known cases get.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

Vision = Literal["yes", "no", "unknown"]
VisionSource = Literal["setting", "provider", "catalog", "none"]

# Families that read images. Matched against the lowercased model id, with
# any `provider/` prefix and `:tag` suffix kept, so `ollama` tags, OpenRouter
# ids and Bedrock ids all match.
_VISION_PATTERNS = [
    r"gpt-4o", r"gpt-4\.1", r"gpt-4\.5", r"gpt-4-turbo", r"gpt-4-vision", r"gpt-5", r"chatgpt-4o",
    r"(^|[/:.-])o[134](-|$|[/:])", r"o3-pro",
    r"claude-3", r"claude-(opus|sonnet|haiku)-[4-9]", r"claude-(opus|sonnet|haiku|fable)", r"claude-[4-9]",
    r"gemini", r"gemma-?3", r"gemma-?4",
    r"llava", r"bakllava", r"moondream", r"minicpm-v", r"llama3\.2-vision", r"llama-3\.2-\d+b-vision",
    r"llama-?4", r"qwen[\d.]*-?vl", r"qvq", r"pixtral", r"mistral-small-?3\.[12]", r"mistral-medium",
    r"granite3\.2-vision", r"granite-vision", r"internvl", r"phi-?3\.5-vision", r"phi-?4-multimodal",
    r"grok-(2-)?vision", r"grok-4", r"glm-4\.?\dv", r"kimi-vl", r"vision",
]

# Families known to read text only. Checked after the list above, so a
# `-vision` variant of a text family still counts as reading images.
_TEXT_ONLY_PATTERNS = [
    r"gpt-3\.5", r"gpt-4($|-0613|-32k)", r"text-davinci",
    r"deepseek-(r1|v2|v3|chat|coder|reasoner)", r"deepseek-r1",
    r"llama-?2", r"llama-?3($|[:.-](?!2-vision))", r"codellama",
    r"qwen[\d.]*($|[:-](?!vl))", r"qwq", r"mistral($|[:-]7b|-nemo|-large)", r"mixtral",
    r"phi-?[234]($|[:-](?!.*(vision|multimodal)))", r"gemma-?2?($|[:-])", r"command-r", r"tinyllama",
    r"smollm", r"starcoder", r"wizardlm", r"nous-hermes", r"openchat", r"yi-",
    r"claude-(2|instant)",
]

# Text-only models whose names would otherwise match a vision family.
_TEXT_FIRST_PATTERNS = [r"o1-mini", r"o3-mini", r"-audio", r"-realtime", r"-transcribe", r"-tts", r"embed"]

_TEXT_FIRST_RE = [re.compile(p) for p in _TEXT_FIRST_PATTERNS]
_VISION_RE = [re.compile(p) for p in _VISION_PATTERNS]
_TEXT_RE = [re.compile(p) for p in _TEXT_ONLY_PATTERNS]


def vision_from_name(model: Optional[str]) -> Vision:
    """What the model's name says, or "unknown"."""
    name = (model or "").strip().lower()
    if not name:
        return "unknown"
    if any(r.search(name) for r in _TEXT_FIRST_RE):
        return "no"
    if any(r.search(name) for r in _VISION_RE):
        return "yes"
    if any(r.search(name) for r in _TEXT_RE):
        return "no"
    return "unknown"


def vision_from_setting(value: Optional[str]) -> Optional[Vision]:
    v = (value or "").strip().lower()
    if v in ("true", "yes", "1", "on"):
        return "yes"
    if v in ("false", "no", "0", "off"):
        return "no"
    return None


async def ollama_vision(base_url: str, model: str, *, timeout: float = 5.0) -> Optional[Vision]:
    """Ask Ollama what the model can do. None when it can't say."""
    if not base_url or not model:
        return None
    url = f"{base_url.rstrip('/')}/api/show"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"model": model})
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        capabilities = resp.json().get("capabilities")
    except ValueError:
        return None
    if not isinstance(capabilities, list):
        return None  # an Ollama too old to report them
    return "yes" if "vision" in capabilities else "no"


@dataclass
class VisionAnswer:
    vision: Vision
    source: VisionSource


async def resolve_vision(
    *, provider: str, base_url: str, model: str, setting: Optional[str], probe: bool = True,
) -> VisionAnswer:
    forced = vision_from_setting(setting)
    if forced is not None:
        return VisionAnswer(forced, "setting")
    if probe and provider == "ollama":
        asked = await ollama_vision(base_url or "http://ollama:11434", model)
        if asked is not None:
            return VisionAnswer(asked, "provider")
    named = vision_from_name(model)
    if named != "unknown":
        return VisionAnswer(named, "catalog")
    return VisionAnswer("unknown", "none")


_REFUSED_IMAGE_HINTS = (
    "image", "vision", "multimodal", "does not support", "not support", "unsupported content",
    "image_url", "invalid content type",
)


def looks_like_image_refusal(message: str) -> bool:
    """A provider's 4xx that means "this model takes no images"."""
    text = (message or "").lower()
    return any(h in text for h in _REFUSED_IMAGE_HINTS) and any(
        w in text for w in ("image", "vision", "multimodal", "image_url")
    )
