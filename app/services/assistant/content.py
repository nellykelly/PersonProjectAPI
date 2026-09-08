"""Load the Markdown corpus under app/assistant_content/ into chunk records.

Each file has a tiny `--- key: value ---` front-matter block (`title`,
`kind`) followed by Markdown body. `source` is derived from the path
relative to the content root, minus the extension:

    app/assistant_content/bio.md              -> source "bio"
    app/assistant_content/projects/beeznest.md -> source "projects/beeznest"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.models import ASSISTANT_CONTENT_KINDS

from .chunking import chunk_markdown, estimate_tokens

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class LoadedChunk:
    source: str
    kind: str
    title: str
    chunk_index: int
    content: str
    token_estimate: int


class ContentError(ValueError):
    """A content file is missing front-matter, a title, or has an unknown kind."""


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    text = text.replace("\r\n", "\n")
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key.strip().lower()] = value
    return meta, text[m.end():]


def iter_content_files(root: Path):
    yield from sorted(root.rglob("*.md"))


def load_chunks(root: Path) -> list[LoadedChunk]:
    root = Path(root)
    out: list[LoadedChunk] = []
    for path in iter_content_files(root):
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(raw)

        source = path.relative_to(root).with_suffix("").as_posix()
        title = meta.get("title") or source
        kind = (meta.get("kind") or "").lower()
        if kind not in ASSISTANT_CONTENT_KINDS:
            raise ContentError(
                f"{path.name}: kind must be one of {ASSISTANT_CONTENT_KINDS}, got {kind!r}"
            )

        pieces = chunk_markdown(body)
        if not pieces:
            raise ContentError(f"{path.name}: no content after front-matter")

        for i, piece in enumerate(pieces):
            out.append(
                LoadedChunk(
                    source=source,
                    kind=kind,
                    title=title,
                    chunk_index=i,
                    content=piece,
                    token_estimate=estimate_tokens(piece),
                )
            )
    return out
