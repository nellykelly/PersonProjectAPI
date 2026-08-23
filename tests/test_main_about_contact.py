def test_home_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Nelson Koskela" in resp.data


def test_about_page(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    assert b"JPMorgan" in resp.data
    assert b"Cogswell" in resp.data


def test_about_page_has_resume_link(client):
    resp = client.get("/about")
    assert b"Nelson_Koskela_Resume.pdf" in resp.data


def test_contact_page(client):
    resp = client.get("/contact")
    assert resp.status_code == 200
    assert b"koskela.nelson@gmail.com" in resp.data


def test_site_wide_nav_links_present_on_every_page(client):
    for path in ("/", "/about", "/contact", "/projects"):
        resp = client.get(path)
        assert b"github.com/nellykelly" in resp.data
        assert b"linkedin.com/in/nelson-k" in resp.data


def test_404(client):
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404


def test_favicon_served_at_domain_root(client):
    # Browsers request this at the root regardless of the <link rel="icon">
    # tag in base.html -- confirms it isn't a 404 in real deployment.
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200


def test_robots_txt_served_at_domain_root(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    assert b"User-agent: *" in resp.data
    assert b"Allow: /" in resp.data


def test_open_graph_tags_present_on_every_page(client):
    # What actually renders when a link to this site is pasted into
    # Slack/LinkedIn/iMessage -- without these the preview is blank.
    for path in ("/", "/about", "/contact", "/projects"):
        resp = client.get(path)
        assert b'property="og:title"' in resp.data
        assert b'property="og:description"' in resp.data
        assert b'property="og:image"' in resp.data
        assert b'property="og:url"' in resp.data
        assert b'name="twitter:card" content="summary_large_image"' in resp.data


def test_og_image_is_an_absolute_url(client):
    # Crawlers fetch og:image directly (not through the page), so a
    # relative /static/... path wouldn't resolve for them.
    resp = client.get("/")
    assert b'property="og:image" content="http' in resp.data


def test_page_title_still_customizes_per_page(client):
    # The og:title plumbing reuses the existing {% block title %} --
    # confirms that override still actually reaches the <title> tag.
    resp = client.get("/about")
    assert b"<title>About | Software Engineer II</title>" in resp.data
