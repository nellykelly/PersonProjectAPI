"""Split a Markdown document into retrieval-sized chunks.

Strategy: walk the doc in blank-line-separated blocks, tracking the most
recent heading. Pack blocks into a buffer until it reaches the target
token budget, then flush -- prefixing the current heading so a chunk that
starts mid-section still says what section it's from. A tiny trailing
chunk is merged back into the previous one.

"Token" here is a cheap character-based estimate (~4 chars/token); this
is for sizing, not billing.
"""
from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")

TARGET_TOKENS = 280
MAX_TOKENS = 400
MIN_TOKENS = 60


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _blocks(body: str) -> list[str]:
    # Normalise newlines, split on one-or-more blank lines, drop empties.
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    return [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]


def chunk_markdown(
    body: str,
    *,
    target_tokens: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    min_tokens: int = MIN_TOKENS,
) -> list[str]:
    chunks: list[str] = []
    heading: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        text = "\n\n".join(buf).strip()
        if heading and not text.lstrip().startswith("#"):
            text = f"{heading}\n\n{text}"
        chunks.append(text)
        buf.clear()

    for block in _blocks(body):
        m = _HEADING_RE.match(block)
        if m:
            # A heading starts a new section. Flush what we have so the
            # previous section doesn't bleed into the next one's chunk.
            flush()
            heading = block.strip()
            buf.append(block)
            continue

        buf.append(block)
        if estimate_tokens("\n\n".join(buf)) >= target_tokens:
            # If a single block already blows past max on its own, still
            # flush it -- one oversized chunk beats splitting mid-sentence.
            flush()

    flush()

    # Merge a runt trailing chunk into its predecessor.
    if len(chunks) >= 2 and estimate_tokens(chunks[-1]) < min_tokens:
        chunks[-2] = chunks[-2] + "\n\n" + chunks[-1]
        chunks.pop()

    return chunks
