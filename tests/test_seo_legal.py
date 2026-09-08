"""SEO / crawlability / compliance surface added after an audit against
the "vibecoded website giveaways" and "ways your app gets sued" lists:
canonical tags, JSON-LD, a real sitemap, /llms.txt, a robots.txt that
does NOT block AI crawlers, a skip link, and the /legal page.
"""
import json
import re

import pytest


def _ld_json(html: str) -> dict:
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert m, "no JSON-LD block"
    return json.loads(m.group(1))


@pytest.mark.parametrize("path", ["/", "/about", "/projects", "/contact", "/assistant", "/legal"])
def test_every_page_has_canonical_and_indexable_robots(client, path):
    html = client.get(path).get_data(as_text=True)
    assert 'rel="canonical"' in html
    assert re.search(r'name="robots" content="index, follow"', html)


def test_structured_data_is_valid_person_and_website(client):
    data = _ld_json(client.get("/").get_data(as_text=True))
    types = {node["@type"] for node in data["@graph"]}
    assert {"Person", "WebSite"} <= types
    person = next(n for n in data["@graph"] if n["@type"] == "Person")
    assert person["name"] == "Nelson Koskela"
    assert any("github.com" in s for s in person["sameAs"])


def test_sitemap_lists_every_public_page_and_no_gated_ones(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.mimetype == "application/xml"
    body = resp.get_data(as_text=True)
    for path in ("/", "/about", "/projects", "/legal", "/projects/pipeline-world", "/assistant"):
        assert f"<loc>http://localhost{path}</loc>" in body
    # password-gated sections stay out
    assert "/documentation" not in body
    assert "/job-tracker" not in body


def test_robots_txt_allows_all_crawlers_and_points_at_the_sitemap(client):
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Allow: /" in body
    assert "Sitemap: http://localhost/sitemap.xml" in body
    # AI crawlers must NOT be blocked -- a public portfolio wants to be summarised
    assert "GPTBot" not in body and "ClaudeBot" not in body  # no per-bot Disallow
    assert "Disallow: /\n" not in body  # never a blanket block


def test_llms_txt_is_served_and_describes_the_site(client):
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    body = resp.get_data(as_text=True)
    assert body.startswith("# Nelson Koskela")
    assert "/assistant" in body and "financial advice" in body


def test_skip_link_is_first_focusable_element(client):
    html = client.get("/").get_data(as_text=True)
    assert '<a class="skip-link" href="#content">Skip to content</a>' in html
    assert html.index("skip-link") < html.index('id="page-wrapper"')


def test_legal_page_covers_the_compliance_surface(client):
    html = client.get("/legal").get_data(as_text=True)
    for anchor in ("terms", "privacy", "cookies", "assistant", "accessibility", "abuse", "eligibility"):
        assert f'id="{anchor}"' in html
    assert "not financial, legal, or professional advice" in html
    assert "at least" in html and "13 years old" in html
    assert "No third-party analytics" in html


def test_footer_links_to_legal_on_every_page(client):
    for path in ("/", "/about", "/contact"):
        assert 'href="/legal"' in client.get(path).get_data(as_text=True)


def test_assistant_page_carries_the_ai_disclaimer(client):
    html = client.get("/assistant").get_data(as_text=True)
    assert "not a binding statement by Nelson" in html
    assert "/legal#assistant" in html


def test_registration_states_the_age_gate(client, app):
    app.config["REGISTRATION_ENABLED"] = True
    html = client.get("/auth/register").get_data(as_text=True)
    assert "at least 13" in html
    assert 'href="/legal"' in html
