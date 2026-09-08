"""(Re)build the `content_chunks` table from app/assistant_content/.

Delete-all-and-reinsert: the corpus is small and always rebuilt whole, so
this is the simplest thing that is correct. Called by `flask assistant
reindex` (see app/__init__.py) and by tests.
"""
from __future__ import annotations

from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import ContentChunk

from .content import load_chunks
from .embeddings import build_embedder


def content_root() -> Path:
    return Path(current_app.root_path) / "assistant_content"


def reindex(*, root: Path | None = None) -> dict:
    root = Path(root) if root is not None else content_root()
    loaded = load_chunks(root)

    embedder = build_embedder(current_app.config)
    # Embed the title alongside the passage so every chunk carries its
    # project's name -- otherwise a short query matches generic phrases
    # ("real queues", "production standard") that appear in every file.
    # The clean `content` is what gets stored and shown.
    vectors = (
        embedder.embed([f"{lc.title}. {lc.content}" for lc in loaded]) if loaded else []
    )

    ContentChunk.query.delete()
    db.session.flush()
    for lc, vec in zip(loaded, vectors):
        db.session.add(
            ContentChunk(
                source=lc.source,
                kind=lc.kind,
                title=lc.title,
                chunk_index=lc.chunk_index,
                content=lc.content,
                token_estimate=lc.token_estimate,
                embedding=[float(x) for x in vec],
            )
        )
    db.session.commit()

    return {
        "files": len({lc.source for lc in loaded}),
        "chunks": len(loaded),
        "embedder": current_app.config["ASSISTANT_EMBEDDER"],
    }
