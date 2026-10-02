#!/usr/bin/env python
"""Sweep ASSISTANT_SEMANTIC_CACHE_THRESHOLD over a labeled set of question
pairs and report hit rate / false-hit rate at each threshold.

Uses the real `fastembed` embedder (local ONNX, BAAI/bge-small-en-v1.5,
same as production retrieval) -- no network call, no Groq quota, $0 cost.
The first run downloads the model file if it isn't already cached by a
prior `flask assistant reindex`; after that it's instant.

    python scripts/semantic_cache_sweep.py
    python scripts/semantic_cache_sweep.py --fixtures path/to/pairs.json

The fixture file (default: app/services/assistant/semantic_cache_fixtures.json)
is a JSON list of {"a": str, "b": str, "label": "paraphrase" | "near_miss"}
pairs, hand-labeled: "paraphrase" pairs are expected to hit (same intended
answer); "near_miss" pairs look similar but need a different answer and
must never hit, however similar their wording.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DEFAULT_FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "services", "assistant", "semantic_cache_fixtures.json",
)

# The same thresholds swept, regardless of fixture size -- a fixed, visible
# sweep grid rather than one derived from the data, so a reader can see
# exactly what was tried.
_THRESHOLDS = [
    0.70, 0.72, 0.74, 0.76, 0.78, 0.79, 0.80, 0.81, 0.82, 0.84,
    0.85, 0.88, 0.90, 0.92, 0.93, 0.94, 0.95, 0.97, 0.99,
]


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", default=_DEFAULT_FIXTURES)
    args = parser.parse_args()

    from app.services.assistant.embeddings import build_embedder

    with open(args.fixtures, "r", encoding="utf-8") as f:
        pairs = json.load(f)

    paraphrases = [p for p in pairs if p["label"] == "paraphrase"]
    near_misses = [p for p in pairs if p["label"] == "near_miss"]
    print(
        f"Loaded {len(pairs)} pairs from {args.fixtures} "
        f"({len(paraphrases)} paraphrase, {len(near_misses)} near_miss)."
    )

    config = {
        "ASSISTANT_EMBEDDER": "fastembed",
        "ASSISTANT_EMBED_MODEL": "BAAI/bge-small-en-v1.5",
        "ASSISTANT_EMBED_DIM": 384,
    }
    embedder = build_embedder(config)

    # Embed every distinct question once, not once per pair.
    questions = sorted({p["a"] for p in pairs} | {p["b"] for p in pairs})
    vectors = {q: embedder.embed_query(q) for q in questions}

    scored = [
        {**p, "similarity": _cosine(vectors[p["a"]], vectors[p["b"]])} for p in pairs
    ]

    print("\nPer-pair similarity:")
    for p in sorted(scored, key=lambda p: -p["similarity"]):
        print(f"  {p['similarity']:.4f}  [{p['label']:>10}]  {p['a']!r} <-> {p['b']!r}")

    print(f"\n{'threshold':>9}  {'hit_rate':>9}  {'false_hit_rate':>15}")
    best = None
    for threshold in _THRESHOLDS:
        hits = sum(1 for p in scored if p["label"] == "paraphrase" and p["similarity"] >= threshold)
        false_hits = sum(1 for p in scored if p["label"] == "near_miss" and p["similarity"] >= threshold)
        hit_rate = hits / len(paraphrases) if paraphrases else 0.0
        false_hit_rate = false_hits / len(near_misses) if near_misses else 0.0
        print(f"{threshold:>9.2f}  {hit_rate:>9.1%}  {false_hit_rate:>15.1%}")
        # Prefer the lowest threshold (best hit rate) among those with zero
        # measured false hits on this fixture -- ties broken by picking the
        # first (lowest) one encountered since _THRESHOLDS is ascending.
        if false_hit_rate == 0.0 and best is None:
            best = (threshold, hit_rate, false_hit_rate)

    print()
    if best is not None:
        threshold, hit_rate, _ = best
        print(
            f"Recommended threshold: {threshold:.2f} "
            f"(hit rate {hit_rate:.1%}, false-hit rate 0.0% on this {len(pairs)}-pair fixture)"
        )
    else:
        print(
            "No swept threshold reached a 0% false-hit rate on this fixture -- "
            "inspect the per-pair table above before enabling the feature."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
