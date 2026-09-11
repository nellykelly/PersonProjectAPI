"""The Timed-Squares assistant tool -- a single public, read-only
leaderboard lookup with no authorization gate and no rate limit, matching
the parity of the web route it wraps (`GET
/projects/timed-squares/api/leaderboard`, which has none for this read
either).

Covers: the schema shape, that `get_leaderboard` returns rows in exactly
the same order the web route would for the same seeded data, and that an
oversized `limit` is capped rather than erroring.
"""
import json

import pytest

from app.models import TimedSquaresScore
from app.services import timed_squares
from app.services.assistant.timedsquares_tools import (
    build_timedsquares_tools,
    dispatch_timedsquares_tool,
)


def _seed(db, rows):
    for player_name, turns in rows:
        db.session.add(TimedSquaresScore(session_id="anonymous", player_name=player_name, turns_survived=turns))
    db.session.commit()


# --------------------------------------------------------------------------
# build_timedsquares_tools -- schema shape
# --------------------------------------------------------------------------

def test_build_timedsquares_tools_returns_one_schema():
    tools = build_timedsquares_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == {"get_leaderboard"}


def test_schema_shape_matches_the_tool_calling_contract():
    tool = build_timedsquares_tools()[0]
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "get_leaderboard"
    params = fn["parameters"]
    assert params["type"] == "object"
    assert "limit" in params["properties"]
    assert params["required"] == []
    assert params["additionalProperties"] is False


def test_build_timedsquares_tools_hands_out_a_fresh_copy():
    a = build_timedsquares_tools()
    a[0]["function"]["name"] = "mutated"
    assert build_timedsquares_tools()[0]["function"]["name"] == "get_leaderboard"


# --------------------------------------------------------------------------
# dispatch_timedsquares_tool -- ordering parity with the web route
# --------------------------------------------------------------------------

def test_get_leaderboard_matches_list_leaderboard_ordering(app, db):
    with app.app_context():
        _seed(db, [("LOW", 3), ("HIGH", 99), ("MID", 40)])

        direct = timed_squares.list_leaderboard(limit=10)
        out = dispatch_timedsquares_tool("get_leaderboard", {})

        # Same order the web route's /api/leaderboard would produce for
        # this data: HIGH, MID, LOW.
        assert [d["player_name"] for d in direct] == ["HIGH", "MID", "LOW"]
        lines = out.splitlines()[1:]
        assert [line.split(". ", 1)[1].split(" -- ")[0] for line in lines] == [
            "HIGH",
            "MID",
            "LOW",
        ]


def test_get_leaderboard_matches_the_web_routes_json_response(app, db, client):
    with app.app_context():
        _seed(db, [("A", 5), ("C", 25), ("B", 15)])

    resp = client.get("/projects/timed-squares/api/leaderboard")
    web_names = [s["player_name"] for s in resp.get_json()["scores"]]

    with app.app_context():
        out = dispatch_timedsquares_tool("get_leaderboard", {})
    lines = out.splitlines()[1:]
    tool_names = [line.split(". ", 1)[1].split(" -- ")[0] for line in lines]

    assert tool_names == web_names == ["C", "B", "A"]


def test_get_leaderboard_ties_broken_by_id_ascending(app, db):
    with app.app_context():
        _seed(db, [("FIRST", 10), ("SECOND", 10)])
        out = dispatch_timedsquares_tool("get_leaderboard", {})
        lines = out.splitlines()[1:]
        names = [line.split(". ", 1)[1].split(" -- ")[0] for line in lines]
        assert names == ["FIRST", "SECOND"]


def test_get_leaderboard_reports_turns_survived(app, db):
    with app.app_context():
        _seed(db, [("ACE", 77)])
        out = dispatch_timedsquares_tool("get_leaderboard", {})
        assert "ACE -- 77 turns" in out


def test_get_leaderboard_empty_is_a_friendly_string(app, db):
    with app.app_context():
        out = dispatch_timedsquares_tool("get_leaderboard", {})
        assert "no" in out.lower()
        assert "score" in out.lower()


# --------------------------------------------------------------------------
# limit handling
# --------------------------------------------------------------------------

def test_limit_is_capped_rather_than_erroring(app, db):
    with app.app_context():
        _seed(db, [(f"P{i}", i) for i in range(30)])
        out = dispatch_timedsquares_tool("get_leaderboard", {"limit": 9999})
        lines = out.splitlines()[1:]
        assert len(lines) == 25  # _MAX_LIMIT, not the requested 9999


def test_limit_below_default_is_respected(app, db):
    with app.app_context():
        _seed(db, [(f"P{i}", i) for i in range(10)])
        out = dispatch_timedsquares_tool("get_leaderboard", {"limit": 3})
        lines = out.splitlines()[1:]
        assert len(lines) == 3


def test_limit_non_numeric_is_a_string_not_an_exception(app, db):
    with app.app_context():
        out = dispatch_timedsquares_tool("get_leaderboard", {"limit": "lots"})
        assert isinstance(out, str)
        assert "whole number" in out.lower()


def test_limit_zero_or_negative_falls_back_to_default(app, db):
    with app.app_context():
        _seed(db, [(f"P{i}", i) for i in range(15)])
        out = dispatch_timedsquares_tool("get_leaderboard", {"limit": 0})
        lines = out.splitlines()[1:]
        assert len(lines) == 10  # _DEFAULT_LIMIT


# --------------------------------------------------------------------------
# dispatch_timedsquares_tool -- never raises
# --------------------------------------------------------------------------

def test_dispatch_unknown_tool_is_a_string(app, db):
    with app.app_context():
        assert "Unknown tool" in dispatch_timedsquares_tool("frobnicate", {})


def test_dispatch_bad_json_arguments_is_a_string(app, db):
    with app.app_context():
        out = dispatch_timedsquares_tool("get_leaderboard", "{not json")
        assert isinstance(out, str)
        assert "parse" in out.lower()


def test_dispatch_accepts_json_string_arguments(app, db):
    with app.app_context():
        _seed(db, [("SOLO", 5)])
        out = dispatch_timedsquares_tool("get_leaderboard", json.dumps({"limit": 5}))
        assert "SOLO" in out


def test_dispatch_no_authorized_kwarg_required(app, db):
    # Unlike dispatch_job_tool, this tool has no authorization gate at all
    # -- calling it with just (name, arguments) must work.
    with app.app_context():
        out = dispatch_timedsquares_tool("get_leaderboard", {})
        assert isinstance(out, str)
        assert "didn't work" not in out
