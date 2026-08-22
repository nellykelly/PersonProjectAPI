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
