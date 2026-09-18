"""Hera hands back the resume PDF directly when asked, not just a pointer to
the About page: `_source_url("resume")` cites the actual file (the same
`RESUME_PATH` the About page's "View/Download Resume" buttons use), and the
`resume`/`faq` content itself carries a real markdown link to it so the
model's own reply text can include one-click-away URL, not just the
citation chip.
"""
from app import RESUME_PATH
from app.blueprints.assistant.routes import _source_url


def test_resume_source_links_directly_to_the_pdf_not_the_about_page(app):
    with app.test_request_context("/"):
        url = _source_url("resume")
        assert url is not None
        assert url.endswith(RESUME_PATH.rsplit("/", 1)[-1])
        assert "/about" not in url


def test_faq_source_still_links_to_the_about_page(app):
    # faq.md covers more than the resume (deploy, contact, code) -- its
    # citation must stay pointed at /about, not get swept into the
    # resume-only special case above.
    with app.test_request_context("/"):
        assert _source_url("faq") == "/about"


def test_resume_content_carries_a_real_markdown_link_to_the_pdf():
    text = (app_root() / "assistant_content" / "resume.md").read_text(encoding="utf-8")
    assert "](/static/assets/files/Nelson_Koskela_Resume.pdf)" in text


def test_faq_resume_answer_carries_a_real_markdown_link_to_the_pdf():
    text = (app_root() / "assistant_content" / "faq.md").read_text(encoding="utf-8")
    assert "](/static/assets/files/Nelson_Koskela_Resume.pdf)" in text


def app_root():
    from pathlib import Path

    import app as app_pkg

    return Path(app_pkg.__file__).parent
