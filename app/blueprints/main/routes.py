from flask import Response, render_template, request, send_from_directory, url_for, current_app

from app.blueprints.main import bp
from app.blueprints.projects.routes import listed_projects

# The most substantial builds, for the landing page's highlight -- the
# full set (plus Earlier Projects) already lives at /projects, this is
# just a taste of it. Filtered through listed_projects(), so a project
# put on hold drops off the landing page too rather than needing to be
# removed from this tuple as well and being missed.
FEATURED_PROJECT_SLUGS = ("trading-simulator", "pipeline-world", "qr-quant-scraper", "timed-squares")

# Public, crawlable pages that aren't project subpages -- static routes
# with no arguments. Project pages are added from listed_projects() so
# the sitemap can't drift from what's actually shown.
_STATIC_PUBLIC_ENDPOINTS = (
    ("main.index", 1.0),
    ("about.index", 0.8),
    ("projects.index", 0.9),
    ("contact.index", 0.6),
    ("assistant.index", 0.7),
    ("assistant.stats", 0.4),
    ("legal.index", 0.3),
    # leetcode.index is deliberately left out -- it's @login_required and
    # 302s to /auth/login for anyone not signed in, so it isn't a
    # crawlable destination.
)


@bp.route("/")
def index():
    listed = listed_projects()
    featured = [p for p in listed if p["slug"] in FEATURED_PROJECT_SLUGS]
    # Counted, not written into the template: it drifted before (the copy
    # still said 5 after a sixth shipped), and putting a project on hold
    # changes it again.
    return render_template("main/index.html", featured_projects=featured, project_count=len(listed))


@bp.route("/favicon.ico")
def favicon():
    # Browsers request this at the domain root regardless of the <link
    # rel="icon"> tag in base.html -- served here so it isn't a 404 in
    # every server log / browser console.
    return send_from_directory(
        f"{current_app.static_folder}/assets/img", "favicon.ico", mimetype="image/vnd.microsoft.icon"
    )


def _public_urls() -> list[tuple[str, float]]:
    urls: list[tuple[str, float]] = []
    for endpoint, priority in _STATIC_PUBLIC_ENDPOINTS:
        try:
            urls.append((url_for(endpoint, _external=True), priority))
        except Exception:  # noqa: BLE001 - a not-yet-registered blueprint just drops out
            pass
    for project in listed_projects():
        try:
            urls.append((url_for(project["endpoint"], _external=True), 0.7))
        except Exception:  # noqa: BLE001
            pass
    # de-dup while keeping the first (highest-priority) occurrence
    seen: set[str] = set()
    out: list[tuple[str, float]] = []
    for u, p in urls:
        if u not in seen:
            seen.add(u)
            out.append((u, p))
    return out


@bp.route("/sitemap.xml")
def sitemap():
    """A real sitemap so search engines index every public page instead
    of guessing. Built from the same `listed_projects()` the nav uses, so
    it can't fall behind. The two password-gated sections are left out on
    purpose (see robots.txt)."""
    rows = "".join(
        f"<url><loc>{loc}</loc><priority>{priority:.1f}</priority></url>"
        for loc, priority in _public_urls()
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{rows}</urlset>"
    )
    return Response(xml, mimetype="application/xml")


@bp.route("/llms.txt")
def llms_txt():
    """The emerging `/llms.txt` convention: a short, plain-text map of the
    site for LLM-based crawlers and assistants, so they summarise it from
    a curated description rather than scraping the DOM. Mirrors what the
    on-site assistant ("Hera") is grounded on."""
    base = request.url_root.rstrip("/")
    body = f"""\
# Nelson Koskela

> Software engineer in Houston, TX. Was a Software Engineer II at JPMorgan Chase
> (Corporate & Investment Banking) from 2022 to 2026; now building independently.
> This site is a Flask portfolio of production-standard side projects.

## Pages
- [Home]({base}/): intro and links
- [About]({base}/about): background and resume
- [Projects]({base}/projects): the full project list
- [Assistant]({base}/assistant): "Hera", a retrieval-grounded chatbot that answers from this site's own content
- [Contact]({base}/contact): email, GitHub, LinkedIn
- [Legal & privacy]({base}/legal): terms, privacy, cookies, AI disclaimer, accessibility

## Projects
- Trading Simulator ({base}/projects/trading-simulator): shared public PnL tracker, Black-Scholes pricing/Greeks, risk priced on a worker
- Company Scorer ({base}/projects/qr-quant-scraper): SEC EDGAR + yfinance composite score with a point-in-time backtest
- Pipeline World ({base}/projects/pipeline-world): a real queued 7-stage CI/CD pipeline visualised live; SQL analytics on Postgres
- SRE Infra Layer ({base}/projects/sre-infra): the Redis queue / cache-aside / rate-limit layer under Pipeline World
- Site Traffic Analytics ({base}/projects/network-sniffer): an aggregate board over the app's own inbound/outbound traffic
- Timed-Squares ({base}/projects/timed-squares): a turn-based browser survival game with a public leaderboard

## Notes
- All market data is simulated or delayed. Nothing on the site is financial advice.
- The assistant can be wrong and only knows what is published here.
"""
    return Response(body, mimetype="text/plain; charset=utf-8")


@bp.route("/robots.txt")
def robots():
    # Search engines request this at the domain root, not under /static/
    # -- same reason favicon.ico gets its own route above. Permissive for
    # everyone, INCLUDING AI crawlers (GPTBot, ClaudeBot, PerplexityBot,
    # Google-Extended, ...): this is a public portfolio and being
    # summarisable is the point -- see /llms.txt. The only Disallows are
    # the two password-gated private sections and the auth pages, which
    # have nothing to index. This is a hint to well-behaved crawlers, not
    # access control -- that's the server-side gate.
    sitemap_url = url_for("main.sitemap", _external=True)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /documentation\n"
        "Disallow: /job-tracker\n"
        "Disallow: /family\n"
        "Disallow: /auth/\n"
        f"\nSitemap: {sitemap_url}\n"
    )
    return Response(body, mimetype="text/plain")
