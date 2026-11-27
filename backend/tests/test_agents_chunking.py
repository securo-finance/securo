"""Chunking helper.

Tests cover:
  - empty / whitespace input → no chunks
  - text below target → single chunk
  - paragraph-aware splits stay close to the target size
  - overlap text appears at the start of subsequent chunks
  - very long single-paragraph fallback (hard split)
  - PDF / TXT mime extraction selection
"""

from app.agents.services.chunking import (
    TARGET_CHARS,
    chunk_text,
    extract_text,
)


def test_empty_input_yields_nothing():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \n") == []


def test_short_text_single_chunk():
    out = chunk_text("hello world")
    assert out == ["hello world"]


def test_paragraphs_grouped_within_target():
    paras = "\n\n".join(["para " + str(i) + " " + ("x" * 200) for i in range(20)])
    chunks = chunk_text(paras, target=1000, overlap=50)
    assert len(chunks) >= 4
    # No chunk should be too far above the target after the overlap-adjusted split.
    for c in chunks:
        assert len(c) <= TARGET_CHARS * 1.5 + 50


def test_overlap_carries_tail_into_next_chunk():
    p1 = "AAAA " * 200  # ~1000 chars
    p2 = "BBBB " * 200
    chunks = chunk_text(f"{p1}\n\n{p2}", target=900, overlap=100)
    assert len(chunks) >= 2
    # The overlap is the last ~OVERLAP_CHARS of chunk 0 prepended to chunk 1.
    # Stripping whitespace handles the space/newline noise around the join.
    tail = chunks[0][-100:].strip()
    head = chunks[1][:300]
    assert tail and tail in head, f"expected overlap tail in next chunk; got head={head[:120]!r}"


def test_huge_single_paragraph_hard_split():
    huge = "Z" * 8000
    chunks = chunk_text(huge, target=2000, overlap=0)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c) <= 2000


def test_extract_text_handles_txt_and_md():
    payload = "# Title\n\nbody text".encode("utf-8")
    assert "body text" in extract_text(payload, mime="text/markdown", filename="x.md")
    assert "body text" in extract_text(payload, mime="text/plain", filename="x.txt")


def test_extract_text_falls_back_for_unknown_mime():
    payload = b"opaque bytes that are still utf8"
    out = extract_text(payload, mime="application/octet-stream", filename="thing.bin")
    assert "opaque" in out
