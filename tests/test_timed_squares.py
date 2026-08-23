import pytest

from app.models import TimedSquaresScore
from app.services import validators


def test_page_loads(client):
    resp = client.get("/projects/timed-squares")
    assert resp.status_code == 200
    assert b"Timed-Squares" in resp.data


def test_page_shows_empty_leaderboard_state(client):
    resp = client.get("/projects/timed-squares")
    assert b"No scores yet" in resp.data


def test_submit_score_persists_and_returns_rank(client, db):
    resp = client.post("/projects/timed-squares/api/scores", data={"turns_survived": "42", "player_name": "AAA"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["score"]["turns_survived"] == 42
    assert data["score"]["player_name"] == "AAA"
    assert data["rank"] == 1
    assert TimedSquaresScore.query.count() == 1


def test_submit_score_rejects_non_integer(client, db):
    resp = client.post("/projects/timed-squares/api/scores", data={"turns_survived": "not-a-number"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_submit_score_rejects_negative(client, db):
    resp = client.post("/projects/timed-squares/api/scores", data={"turns_survived": "-1"})
    assert resp.status_code == 400


def test_submit_score_rejects_absurdly_large_values(client, db, app):
    max_turns = app.config["TIMED_SQUARES_MAX_TURNS"]
    resp = client.post("/projects/timed-squares/api/scores", data={"turns_survived": str(max_turns + 1)})
    assert resp.status_code == 400


def test_submit_score_defaults_to_anon_with_no_name(client, db):
    resp = client.post("/projects/timed-squares/api/scores", data={"turns_survived": "5"})
    assert resp.get_json()["score"]["player_name"] == "ANON"


def test_leaderboard_orders_by_turns_survived_descending(client, db):
    for name, turns in (("LOW", 3), ("HIGH", 99), ("MID", 40)):
        client.post("/projects/timed-squares/api/scores", data={"turns_survived": str(turns), "player_name": name})

    resp = client.get("/projects/timed-squares/api/leaderboard")
    names = [s["player_name"] for s in resp.get_json()["scores"]]
    assert names[:3] == ["HIGH", "MID", "LOW"]


def test_leaderboard_caps_at_ten_entries(client, db):
    for i in range(15):
        client.post("/projects/timed-squares/api/scores", data={"turns_survived": str(i)})
    resp = client.get("/projects/timed-squares/api/leaderboard")
    assert len(resp.get_json()["scores"]) == 10


def test_rank_reflects_position_among_existing_scores(client, db):
    client.post("/projects/timed-squares/api/scores", data={"turns_survived": "100"})
    client.post("/projects/timed-squares/api/scores", data={"turns_survived": "50"})
    resp = client.post("/projects/timed-squares/api/scores", data={"turns_survived": "75"})
    assert resp.get_json()["rank"] == 2


# ---------- arcade name sanitization ----------


def test_sanitize_arcade_name_uppercases_valid_input():
    assert validators.sanitize_arcade_name("player1") == "PLAYER1"


def test_sanitize_arcade_name_falls_back_to_anon_for_blank():
    assert validators.sanitize_arcade_name("") == "ANON"
    assert validators.sanitize_arcade_name(None) == "ANON"
    assert validators.sanitize_arcade_name("   ") == "ANON"


def test_sanitize_arcade_name_rejects_html_tags():
    assert validators.sanitize_arcade_name("<script>") == "ANON"


def test_sanitize_arcade_name_truncates_rather_than_rejects():
    result = validators.sanitize_arcade_name("A" * 30)
    assert result == "A" * validators.MAX_ARCADE_NAME_LENGTH


def test_sanitize_arcade_name_blocks_profanity():
    # Same hashed blocklist Pipeline World's names use (see
    # validators.py's module docstring for why it's hashed, not
    # plaintext) -- a leaderboard name gets the same treatment.
    assert validators.sanitize_arcade_name("damn") == "ANON"
