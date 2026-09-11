#!/usr/bin/env python
"""Smoke test for the Tiny JVM project page.

Two modes:

  # In-process: builds the app with the factory, uses the test client.
  # No server, no network, no real database needed.
  python scripts/smoke_tiny_jvm.py

  # Against a running server (start it first with `flask --app wsgi run`):
  python scripts/smoke_tiny_jvm.py --url http://127.0.0.1:5000

Exit code 0 if every check passes, 1 otherwise. Prints one line per check.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow `python scripts/smoke_tiny_jvm.py` from anywhere: put the repo root
# (this file's parent's parent) on the path so `import app` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The strings the rendered /projects/tiny-jvm page must contain. These come
# straight from OPCODES / SAMPLE_PROGRAMS in app/blueprints/tiny_jvm/routes.py
# and the template's copy -- if the page stops showing them, something
# regressed.
PAGE_MUST_CONTAIN = [
    "Tiny JVM",
    "0x01",            # PUSH, first opcode row
    "0x13",            # DIV
    "DWRITE",          # GPIO opcode name
    "HALT",            # last opcode row
    "Fibonacci",       # sample program
    "Factorial",       # sample program
    "Over-temp alarm", # sample program
    "WebAssembly",
    "build-spec-tiny-jvm.md",
]

PROJECTS_MUST_CONTAIN = ["Tiny JVM", "/projects/tiny-jvm", "tiny-jvm.svg"]


class Result:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = " ok " if ok else "FAIL"
        line = f"  [{mark}] {name}"
        if detail and not ok:
            line += f"  -- {detail}"
        print(line)
        if not ok:
            self.failed += 1


def _run(get, res: Result) -> None:
    """`get(path)` -> object with `.status_code` and `.text`. Works for both
    a Flask test-client response and a `requests` response."""
    # 1. home page
    r = get("/")
    res.check("GET / -> 200", r.status_code == 200, f"got {r.status_code}")

    # 2. projects landing lists the card + link + icon
    r = get("/projects")
    res.check("GET /projects -> 200", r.status_code == 200, f"got {r.status_code}")
    for needle in PROJECTS_MUST_CONTAIN:
        res.check(f"/projects contains {needle!r}", needle in r.text)

    # 3. the project page renders
    r = get("/projects/tiny-jvm")
    res.check("GET /projects/tiny-jvm -> 200", r.status_code == 200, f"got {r.status_code}")
    page = r.text if r.status_code == 200 else ""
    for needle in PAGE_MUST_CONTAIN:
        res.check(f"tiny-jvm page contains {needle!r}", needle in page)

    # 4. the card icon is served
    r = get("/static/assets/img/icons/tiny-jvm.svg")
    res.check(
        "GET tiny-jvm.svg -> 200",
        r.status_code == 200,
        f"got {r.status_code}",
    )
    res.check(
        "tiny-jvm.svg looks like SVG",
        r.status_code == 200 and "<svg" in r.text,
    )


def _in_process(res: Result) -> None:
    from app import create_app

    app = create_app("development")
    with app.test_client() as client:
        # give the checks a uniform interface
        class _R:
            def __init__(self, resp):
                self.status_code = resp.status_code
                self.text = resp.get_data(as_text=True)

        _run(lambda path: _R(client.get(path)), res)

    # bonus: the source-of-truth constants are sane
    from app.blueprints.tiny_jvm.routes import OPCODES, SAMPLE_PROGRAMS

    res.check("OPCODES non-empty", len(OPCODES) >= 10, f"len={len(OPCODES)}")
    res.check(
        "every opcode row has the 4 fields",
        all({"hex", "name", "operand", "effect"} <= op.keys() for op in OPCODES),
    )
    res.check(
        "SAMPLE_PROGRAMS non-empty with source",
        len(SAMPLE_PROGRAMS) >= 1 and all(p.get("source") for p in SAMPLE_PROGRAMS),
    )


def _against_url(base_url: str, res: Result) -> None:
    import requests

    base = base_url.rstrip("/")
    _run(lambda path: requests.get(base + path, timeout=10), res)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        help="Base URL of a running server (e.g. http://127.0.0.1:5000). "
        "Omit to run in-process with the Flask test client.",
    )
    args = parser.parse_args()

    res = Result()
    if args.url:
        print(f"Smoke test: Tiny JVM  (live server at {args.url})")
        _against_url(args.url, res)
    else:
        print("Smoke test: Tiny JVM  (in-process test client)")
        _in_process(res)

    print()
    if res.failed:
        print(f"FAILED: {res.failed} check(s) did not pass")
        return 1
    print("PASSED: all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
