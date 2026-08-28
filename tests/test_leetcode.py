import re

from app.blueprints.leetcode.problems import TOPICS, TOTAL, iter_problems, problem_url
from app.models import LeetCodeProgress, User


# ---------------------------------------------------------------- dataset

def test_dataset_is_exactly_the_top_interview_150():
    problems = list(iter_problems())
    assert TOTAL == 150
    assert len(problems) == 150


def test_dataset_has_no_duplicate_slugs():
    slugs = [p["slug"] for p in iter_problems()]
    assert len(slugs) == len(set(slugs))


def test_every_problem_url_is_a_leetcode_problem_link():
    pattern = re.compile(r"^https://leetcode\.com/problems/[a-z0-9-]+/$")
    for p in iter_problems():
        assert pattern.match(p["url"]), p


def test_problem_url_helper():
    assert problem_url("two-sum") == "https://leetcode.com/problems/two-sum/"


def test_known_problems_are_present_with_correct_slugs():
    by_title = {p["title"]: p["slug"] for p in iter_problems()}
    assert by_title["Two Sum"] == "two-sum"
    assert by_title["Add Two Numbers"] == "add-two-numbers"
    assert by_title["3Sum"] == "3sum"
    assert by_title["Pow(x, n)"] == "powx-n"
    assert by_title["Sqrt(x)"] == "sqrtx"
    assert by_title["LRU Cache"] == "lru-cache"


def test_topics_cover_the_official_study_plan_groups():
    names = [t["name"] for t in TOPICS]
    assert "Array / String" in names
    assert "Binary Tree General" in names
    assert "Multidimensional DP" in names
    assert len(names) == len(set(names))


# ---------------------------------------------------------------- page (login-gated)

def test_anonymous_is_redirected_to_login(client):
    resp = client.get("/leetcode-150")
    assert resp.status_code == 302
    assert "/auth/login" in resp.headers["Location"]


def test_page_loads_and_links_every_problem(auth_client):
    resp = auth_client.get("/leetcode-150")
    assert resp.status_code == 200
    assert b"Top Interview 150 Tracker" in resp.data
    body = resp.data.decode("utf-8")
    # fresh account: every row is unmarked, so class is exactly "lc-row"
    assert body.count('class="lc-row"') == 150
    for p in iter_problems():
        assert p["url"] in body


def test_page_has_yes_no_controls_and_script(auth_client):
    body = auth_client.get("/leetcode-150").data.decode("utf-8")
    assert body.count('class="lc-yes"') == 150
    assert body.count('class="lc-no"') == 150
    assert "leetcode_tracker.js" in body
    assert 'id="lc-reset-dialog"' in body
    assert 'name="csrf-token"' in body
    assert 'id="lc-initial-state"' in body


def test_tracker_is_linked_from_the_projects_index(client):
    body = client.get("/projects").data.decode("utf-8")
    assert "Top Interview 150 Tracker" in body
    assert "/leetcode-150" in body


# ---------------------------------------------------------------- progress API

def test_progress_api_requires_login(client):
    resp = client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "yes"})
    assert resp.status_code in (302, 401)


def test_set_get_and_clear_a_mark(auth_client, db):
    r = auth_client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "yes"})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    r = auth_client.get("/leetcode-150/api/progress")
    assert r.get_json()["marks"] == {"two-sum": "yes"}
    assert LeetCodeProgress.query.count() == 1

    # flip
    auth_client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "no"})
    assert auth_client.get("/leetcode-150/api/progress").get_json()["marks"] == {"two-sum": "no"}

    # clear
    auth_client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": None})
    assert auth_client.get("/leetcode-150/api/progress").get_json()["marks"] == {}
    assert LeetCodeProgress.query.count() == 0


def test_unknown_slug_is_rejected(auth_client):
    r = auth_client.post("/leetcode-150/api/progress", json={"slug": "not-a-real-problem", "mark": "yes"})
    assert r.status_code == 400


def test_bad_mark_is_rejected(auth_client):
    r = auth_client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "maybe"})
    assert r.status_code == 400


def test_reset_clears_only_the_current_users_board(make_user, login):
    a = login(make_user("alice"))
    b = login(make_user("bob"))

    a.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "yes"})
    a.post("/leetcode-150/api/progress", json={"slug": "3sum", "mark": "no"})
    b.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "yes"})

    a.post("/leetcode-150/api/progress/reset")

    assert a.get("/leetcode-150/api/progress").get_json()["marks"] == {}
    assert b.get("/leetcode-150/api/progress").get_json()["marks"] == {"two-sum": "yes"}


def test_boards_are_isolated_per_user(make_user, login):
    a = login(make_user("carol"))
    b = login(make_user("dave"))
    a.post("/leetcode-150/api/progress", json={"slug": "lru-cache", "mark": "yes"})
    assert b.get("/leetcode-150/api/progress").get_json()["marks"] == {}


def test_index_renders_the_saved_board(auth_client):
    auth_client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "yes"})
    body = auth_client.get("/leetcode-150").data.decode("utf-8")
    # the two-sum row is server-rendered with the completed class...
    assert 'class="lc-row lc-row-yes" data-slug="two-sum"' in body
    # ...and its Yes checkbox is pre-checked, and the JSON island carries it
    row = body.split('data-slug="two-sum"', 1)[1].split("</tr>", 1)[0]
    assert 'class="lc-yes"' in row and "checked" in row
    assert '"two-sum": "yes"' in body or '"two-sum":"yes"' in body


def test_import_merges_without_overwriting(auth_client):
    auth_client.post("/leetcode-150/api/progress", json={"slug": "two-sum", "mark": "no"})
    r = auth_client.post(
        "/leetcode-150/api/progress/import",
        json={"marks": {"two-sum": "yes", "3sum": "yes", "bogus": "yes", "add-two-numbers": "sideways"}},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["added"] == 1  # only 3sum: two-sum already set, bogus unknown, add-two-numbers bad mark

    marks = auth_client.get("/leetcode-150/api/progress").get_json()["marks"]
    assert marks == {"two-sum": "no", "3sum": "yes"}
