"""The engineering-reference page at /documentation.

It's a standalone long-form document rather than a normal page of the
site (it carries its own stylesheet and doesn't extend base.html), so
the things worth guarding are that it's reachable, that it's linked
from the site nav, and that the parts most likely to break silently --
the Mermaid diagrams and the Jinja links back into the site -- are
actually present in the rendered output.
"""


def test_documentation_page_loads(client):
    resp = client.get("/documentation")
    assert resp.status_code == 200
    assert b"Portfolio Site Engineering Reference" in resp.data


def test_documentation_is_linked_from_the_site_nav(client):
    # Checked on a page that *does* extend base.html -- the doc page
    # itself deliberately doesn't render the site nav.
    resp = client.get("/about")
    assert b"/documentation" in resp.data
    assert b">Documentation<" in resp.data


def test_documentation_links_back_into_the_site(client):
    """The page replaces the site chrome with its own bar, so those two
    links are the only way back -- a broken url_for here would strand a
    reader on the document."""
    resp = client.get("/documentation")
    assert b'href="/"' in resp.data
    assert b'href="/projects"' in resp.data


def test_documentation_loads_mermaid_and_has_diagrams_to_render(client):
    """Mermaid is rendered natively by other hosts but has to be loaded
    explicitly here. Asserting both halves together means neither the
    script nor the diagrams can go missing without failing."""
    resp = client.get("/documentation")
    assert b"mermaid.esm.min.mjs" in resp.data
    assert resp.data.count(b'<pre class="mermaid">') == 11


def test_every_diagram_pins_its_own_theme(client):
    """Each diagram carries an init directive fixing its colours. Without
    it Mermaid inherits the viewer's theme and renders light text on the
    document's white diagram cards, which is invisible rather than merely
    ugly -- and it fails silently, since the diagram still 'renders'."""
    resp = client.get("/documentation")
    assert resp.data.count(b"%%{init:") == resp.data.count(b'<pre class="mermaid">')


def test_documentation_covers_every_project(client):
    resp = client.get("/documentation")
    for heading in (
        b"Trading Simulator",
        b"Quant Company Scorer",
        b"Pipeline World",
        b"SRE Infra Layer",
        b"Site Traffic Analytics",
        b"Timed-Squares",
    ):
        assert heading in resp.data
