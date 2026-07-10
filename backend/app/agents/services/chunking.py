"""Format-aware chunking for knowledge-base ingestion.

Goal: produce chunks of roughly TARGET characters with light paragraph-aware
splits and OVERLAP between adjacent chunks to preserve context. Cheap and
predictable; good enough for a v1 RAG pipeline.
"""

from __future__ import annotations

import io
import re
from typing import Iterable


TARGET_CHARS = 2000
OVERLAP_CHARS = 200


def chunk_text(text: str, *, target: int = TARGET_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if buf_len + len(p) + 2 <= target or not buf:
            buf.append(p)
            buf_len += len(p) + 2
        else:
            out.append("\n\n".join(buf))
            # Start the next chunk with the tail of the previous one for
            # context continuity.
            tail = "\n\n".join(buf)[-overlap:] if overlap else ""
            buf = [tail, p] if tail else [p]
            buf_len = len(tail) + len(p) + 2
    if buf:
        out.append("\n\n".join(buf))
    # Final pass: anything still way too big gets hard-split.
    final: list[str] = []
    for c in out:
        if len(c) <= target * 1.5:
            final.append(c)
        else:
            for i in range(0, len(c), target):
                final.append(c[i : i + target])
    return [c.strip() for c in final if c.strip()]


def parse_pdf_to_text(payload: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def extract_text(payload: bytes, mime: str, filename: str = "") -> str:
    mime_l = (mime or "").lower()
    if "pdf" in mime_l or filename.lower().endswith(".pdf"):
        return parse_pdf_to_text(payload)
    # Default: treat as utf-8 text (covers .md, .txt, .markdown, .rst).
    try:
        return payload.decode("utf-8", errors="replace")
    except Exception:
        return ""


def chunks_from_upload(payload: bytes, mime: str, filename: str = "") -> Iterable[str]:
    return chunk_text(extract_text(payload, mime, filename))
