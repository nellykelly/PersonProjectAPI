"""The engineering reference at /documentation, and its gated interview
sub-section.

The whole reference is a real access control now (previously only the
interview sub-section was gated) -- it's no longer linked from the site
nav, only from /contact -- so it gets tested like one: the content must
not reach an unauthenticated request *at all*, and the gate must fail
closed when unconfigured. The reference itself is a standalone long-form
document rather than a normal page of the site (it carries its own
stylesheet and doesn't extend base.html when unlocked), so the things
worth guarding once unlocked are that it's reachable and that the parts
most likely to break silently -- the Mermaid diagrams and the Jinja links
back into the site -- are present.
"""
import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "correct-horse-battery-staple"

# A distinctive phrase that only exists inside the gated interview
# content, used to prove the answers never appear in a response they
# shouldn't.
SECRET_PHRASE = b"what a strong answer covers"

# A phrase that only exists in the general reference, used the same way
# to prove nothing leaks before the section is unlocked at all.
REFERENCE_PHRASE = b"Portfolio Site Engineering Reference"


@pytest.fixture()
def locked_client(app, client):
    """A client whose app has a real password configured."""
    app.config["DOCS_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    yield client
    app.config["DOCS_PASSWORD_HASH"] = None


@pytest.fixture()
def unlocked_client(locked_client):
    """A client that has already entered the correct password."""
    locked_client.post("/documentation", data={"password": PASSWORD})
    return locked_client


# ---------- the gate on /documentation itself ----------


def test_documentation_asks_for_a_password_when_locked(locked_client):
    resp = locked_client.get("/documentation")
    assert resp.status_code == 200
    assert b"Password" in resp.data
    assert REFERENCE_PHRASE not in resp.data
    assert SECRET_PHRASE not in resp.data


def test_wrong_password_is_rejected_and_reveals_nothing(locked_client):
    resp = locked_client.post("/documentation", data={"password": "hunter2"})
    assert resp.status_code == 401
    assert b"not right" in resp.data
    assert REFERENCE_PHRASE not in resp.data


def test_correct_password_unlocks_the_reference(locked_client):
    resp = locked_client.post("/documentation", data={"password": PASSWORD}, follow_redirects=True)
    assert resp.status_code == 200
    assert REFERENCE_PHRASE in resp.data


def test_unlock_persists_across_requests(unlocked_client):
    resp = unlocked_client.get("/documentation")
    assert resp.status_code == 200
    assert REFERENCE_PHRASE in resp.data


def test_locking_again_re_gates_the_reference(unlocked_client):
    unlocked_client.get("/documentation/lock")

    resp = unlocked_client.get("/documentation")
    assert REFERENCE_PHRASE not in resp.data
    assert b"Password" in resp.data


def test_gate_fails_closed_when_no_password_is_configured(app, client):
    """Unset must mean 'nobody gets in', not 'everybody gets in' and not
    'some checked-in fallback is the real password on every deploy'."""
    app.config["DOCS_PASSWORD_HASH"] = None

    resp = client.get("/documentation")
    assert resp.status_code == 503
    assert REFERENCE_PHRASE not in resp.data

    # ...and no password opens it either.
    resp = client.post("/documentation", data={"password": PASSWORD})
    assert resp.status_code == 503
    assert REFERENCE_PHRASE not in resp.data


def test_no_password_hash_is_committed_to_the_repo():
    """The hash belongs in the environment. A value checked into a public
    repo would be the same on every deployment and impossible to rotate
    without a code change."""
    from app.config import Config

    assert Config.DOCS_PASSWORD_HASH in (None, "")


# ---------- visibility: nav vs. Contact ----------


def test_documentation_is_not_linked_from_the_site_nav(client):
    # Checked on a page that *does* extend base.html -- the doc page
    # itself deliberately doesn't render the site nav.
    resp = client.get("/about")
    assert b">Documentation<" not in resp.data


def test_documentation_is_linked_from_contact(client):
    resp = client.get("/contact")
    assert b"/documentation" in resp.data


# ---------- the unlocked reference ----------


def test_documentation_links_back_into_the_site(unlocked_client):
    resp = unlocked_client.get("/documentation")
    assert b'href="/"' in resp.data
    assert b'href="/projects"' in resp.data


def test_documentation_loads_mermaid_and_has_diagrams_to_render(unlocked_client):
    resp = unlocked_client.get("/documentation")
    assert b"mermaid.esm.min.mjs" in resp.data
    assert resp.data.count(b'<pre class="mermaid">') == 12


def test_every_diagram_pins_its_own_theme(unlocked_client):
    """Without an init directive Mermaid inherits the viewer's theme and
    renders light text on the document's white diagram cards -- invisible
    rather than merely ugly, and silent, since it still 'renders'."""
    resp = unlocked_client.get("/documentation")
    assert resp.data.count(b"%%{init:") == resp.data.count(b'<pre class="mermaid">')


def test_documentation_covers_the_container_setup(unlocked_client):
    """Docker is a large part of how this app actually runs and is prime
    interview ground, so the reference has to explain it rather than just
    naming the services."""
    resp = unlocked_client.get("/documentation")
    for topic in (
        b"Containerisation",
        b"Dockerfile",
        b"HEALTHCHECK",
        b"service_healthy",
        b"docker-compose.yml",
        b"appuser",              # non-root user
        b"PYTHONUNBUFFERED",
        b"caddy_data",           # the volume whose loss actually hurts
        b"--force-recreate",
    ):
        assert topic in resp.data, f"missing from the reference: {topic!r}"


def test_documentation_covers_every_project(unlocked_client):
    resp = unlocked_client.get("/documentation")
    for heading in (
        b"Trading Simulator",
        b"Company Scorer",
        b"Pipeline World",
        b"SRE Infra Layer",
        b"Site Traffic Analytics",
        b"Timed-Squares",
    ):
        assert heading in resp.data


def test_documentation_page_is_not_indexable(locked_client):
    resp = locked_client.get("/documentation")
    assert b"noindex" in resp.data


# ---------- the interview sub-section (behind the same gate) ----------


def test_interview_redirects_to_the_gate_when_locked(locked_client):
    resp = locked_client.get("/documentation/interview")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/documentation")


def test_interview_is_reachable_once_the_section_is_unlocked(unlocked_client):
    """No separate password: unlocking /documentation is enough to reach
    the interview bank too, since it's one section behind one gate now."""
    resp = unlocked_client.get("/documentation/interview")
    assert resp.status_code == 200
    assert SECRET_PHRASE in resp.data
    assert b"Interview Question Bank" in resp.data


def test_interview_bank_includes_container_questions(unlocked_client):
    resp = unlocked_client.get("/documentation/interview")
    assert b"Containers and deployment" in resp.data
    for q in (
        b"Walk me through your Dockerfile",
        b"share one image",
        b"service_healthy",
        b"volume would hurt most",
        b"How do secrets get into the containers",
        b"run a migration against production",
    ):
        assert q in resp.data, f"missing question: {q!r}"


def test_interview_bank_leads_with_core_stories_not_a_question_list(unlocked_client):
    """39 memorised answers is the wrong preparation -- it sounds rehearsed
    and collapses on a reworded question. The bank opens with the ten
    underlying stories, marks the ten highest-value questions, and gives
    the codebase-specific ones a general framing so the prepared example
    still lands when the interviewer asks the wider version."""
    resp = unlocked_client.get("/documentation/interview")

    assert b"Prepare ten stories" in resp.data
    assert b"story-table" in resp.data

    # Exactly eight questions carry the core marker. Counted on the
    # <summary> specifically: the guidance paragraph shows the pill inline
    # as an example, so a document-wide count is off by one and would make
    # this assertion quietly meaningless.
    assert resp.data.count(b'core</span></summary>') == 8

    # The core set has to be the *general* questions -- an interviewer who
    # hasn't read the codebase asks those, not the implementation trivia.
    assert b"Walk me through this project." in resp.data
    assert b"Why one gunicorn worker? That seems wrong.</summary>" in resp.data  # present, not core

    # ...and the specific ones carry a general re-framing.
    assert resp.data.count(b"Broader framing") >= 10
    assert b"How do you choose between communication mechanisms?" in resp.data


def test_interview_page_is_not_indexable(unlocked_client):
    resp = unlocked_client.get("/documentation/interview")
    assert b"noindex" in resp.data
