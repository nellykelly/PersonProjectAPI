"""Job-listing source clients for Job Discovery (see app/services/job_discovery.py).

Each module exposes one `search(...)` that returns a list of plain dicts,
all in the same normalized shape, so job_discovery.py never needs to know
which source a listing came from until it stores `source`:

    {
        "company_name": str,
        "role_title": str,
        "location": str | None,
        "job_posting_url": str,
        "date_posted": datetime.date | None,
        "salary_range": str | None,
        "description": str,   # raw posting text, the LLM scorer's context
        "source": str,         # e.g. "Adzuna", "RemoteOK", "Greenhouse"
        "_keyword": str,       # internal -- which configured search title this
                                # matched, used to round-robin the per-run cap
                                # fairly across both keywords AND sources (see
                                # job_discovery._round_robin). Popped before a
                                # listing is ever stored.
    }

Two shapes of source:
  - Adzuna has real server-side keyword search (one call per keyword), so
    it tags `_keyword` with the phrase it searched for.
  - RemoteOK/Greenhouse have no keyword search at all -- they hand back
    everything on a board/feed, so `matches_any_keyword()` below does the
    filtering here, client-side, and tags whichever configured title
    plausibly matched.

`is_excluded_title()` is the single place "not an internship/co-op" is
enforced, so every source -- present and future -- filters the same way
without each reimplementing the regex.
"""
import re

# Word-boundary so "internal"/"international" titles (which contain
# "intern" as a substring) don't get caught.
_EXCLUDED_TITLE_RE = re.compile(r"\b(intern|internship|co-?op|apprentice(ship)?)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")


def is_excluded_title(title: str | None) -> bool:
    """True for internship/co-op/apprentice postings -- filtered out
    before scoring even runs, on every source."""
    return bool(_EXCLUDED_TITLE_RE.search(title or ""))


def matches_any_keyword(title: str, keywords: list[str]) -> str | None:
    """For sources with no server-side keyword search: does this title
    plausibly match one of the configured search titles? A loose word-
    overlap check (every word of the keyword phrase must appear
    somewhere in the title, in any order) rather than an exact
    substring, so "AI Engineer" also matches "Senior AI Engineer II".
    Returns the first matching keyword (for round-robin grouping) or
    None if the title doesn't match anything configured."""
    title_words = set(_WORD_RE.findall((title or "").lower()))
    for kw in keywords:
        kw_words = set(_WORD_RE.findall(kw.lower()))
        if kw_words and kw_words <= title_words:
            return kw
    return None
