"""Visitor analytics wiring (Microsoft Clarity + PostHog) -- server side.

The browser half lives in `app/static/js/analytics.js`; this module only
decides *whether* to render it and hands the template a small, public
config dict. Nothing here talks to either vendor.

Both tools are optional and independent: with no env vars set,
`build_analytics_context` returns `ANALYTICS = None` and base.html renders
nothing, so the site behaves exactly as it did before analytics existed.
"""
from __future__ import annotations

import re

from flask import Flask, request

# Clarity project IDs are short alphanumeric strings (e.g. "k3x9abc12d");
# PostHog project keys look like "phc_...". Anything else is dropped rather
# than interpolated into an inline <script>.
_CLARITY_ID_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
_POSTHOG_KEY_RE = re.compile(r"^[A-Za-z0-9_]{8,100}$")


def _posthog_assets_host(api_host: str) -> str:
    """PostHog serves its JS bundle from a sibling "-assets" host
    (us.i.posthog.com -> us-assets.i.posthog.com); a self-hosted or
    proxied api_host serves it from itself."""
    return api_host.replace(".i.posthog.com", "-assets.i.posthog.com")


def _current_project() -> dict | None:
    """The project the current request is on, if any, so the page can fire
    `project_viewed`. Derived from projects/routes.py's PROJECTS list (the
    one source of truth for what a "project" is) by matching the request's
    blueprint to each project's endpoint -- so adding a project to that
    list is all it takes; there is no second list to keep in sync.
    The password-gated Trading Bot isn't in PROJECTS, so it never matches."""
    from app.blueprints.projects.routes import PROJECTS

    blueprint = request.blueprint
    if not blueprint:
        return None
    for project in PROJECTS:
        if project["endpoint"].split(".")[0] == blueprint:
            return {"slug": project["slug"], "name": project["title"]}
    return None


def build_analytics_context(app: Flask) -> dict:
    clarity_id = app.config.get("CLARITY_PROJECT_ID") or ""
    posthog_key = app.config.get("POSTHOG_API_KEY") or ""
    if not _CLARITY_ID_RE.match(clarity_id):
        clarity_id = ""
    if not _POSTHOG_KEY_RE.match(posthog_key):
        posthog_key = ""
    if not (clarity_id or posthog_key):
        return {"ANALYTICS": None}

    host = app.config.get("POSTHOG_HOST") or "https://us.i.posthog.com"
    config = {
        "clarityId": clarity_id or None,
        "posthogKey": posthog_key or None,
        "posthogHost": host,
        "posthogAssetsHost": _posthog_assets_host(host),
        "project": _current_project(),
    }
    return {"ANALYTICS": config}


def analytics_csp_sources(app: Flask) -> dict[str, list[str]]:
    """Extra CSP source-list entries for whichever tools are configured,
    keyed by directive. The CSP is report-only today, but kept accurate so
    flipping it to enforcing doesn't silently kill analytics."""
    sources: dict[str, list[str]] = {"script-src": [], "connect-src": [], "img-src": []}
    if _CLARITY_ID_RE.match(app.config.get("CLARITY_PROJECT_ID") or ""):
        sources["script-src"] += ["https://www.clarity.ms", "https://scripts.clarity.ms"]
        sources["connect-src"] += ["https://*.clarity.ms", "https://c.bing.com"]
        sources["img-src"] += ["https://*.clarity.ms", "https://c.bing.com"]
    if _POSTHOG_KEY_RE.match(app.config.get("POSTHOG_API_KEY") or ""):
        host = app.config.get("POSTHOG_HOST") or "https://us.i.posthog.com"
        sources["script-src"].append(_posthog_assets_host(host))
        sources["connect-src"] += [host, _posthog_assets_host(host)]
    return sources
