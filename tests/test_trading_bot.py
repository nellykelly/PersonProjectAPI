"""Tests for /projects/trading-bot(/research) and app.services.trading_bot.

A hand-written snapshot JSON stands in for a real Trading Bot export
(scripts/export_stats.py in the sibling S:\\Tradingbot repo) -- fast,
hermetic, and matches the schema documented in that repo's
WEBSITE_HANDOVER.md.
"""

from __future__ import annotations

import json

import pytest

SNAPSHOT = {
    "generated_at": "2026-09-28T12:00:00+00:00",
    "mode": "paper",
    "live": {
        "equity": 99431.49,
        "cash": 50000.0,
        "last_equity": 100000.0,
        "day_change_pct": -0.57,
        "positions": [
            {
                "symbol": "AAPL",
                "qty": 10,
                "market_value": 1700.0,
                "unrealized_pl": 50.0,
                "unrealized_plpc": 0.03,
                "current_price": 170.0,
            }
        ],
        "pending_orders": [],
        "last_trend_rebalance": "2026-09-01T00:00:00+00:00",
        "last_alpha_rebalance": None,
        "alpha_sleeve_enabled": False,
    },
    "research": {
        "as_of": "2026-09-28",
        "methodology": "Walk-forward replay of the exact deployed strategy code.",
        "universe": "58 US large-cap stocks + ETFs",
        "strategy": "Trend-following, monthly rebalance",
        "period": "2022-01-16 to 2026-09-28",
        "sharpe": 1.02,
        "annualized_return_pct": 16.6,
        "total_return_pct": 64.2,
        "max_drawdown_pct": -22.0,
        "by_year_pct": {"2023": 21.2, "2022": -8.3, "2024": 20.7},
        "weekly_hit_rate_pct": 60,
        "weekly_mean_return_pct": 0.22,
        "weekly_std_pct": 1.85,
        "notes": ["Kronos found no edge.", "Survivorship bias in the universe."],
    },
}


@pytest.fixture
def snapshot_path(tmp_path):
    path = tmp_path / "trading_bot_stats.json"
    path.write_text(json.dumps(SNAPSHOT))
    return str(path)


# --------------------------------------------------------------------------
# app.services.trading_bot.get_dashboard_data
# --------------------------------------------------------------------------
def test_missing_file_is_graceful(tmp_path):
    from app.services import trading_bot

    result = trading_bot.get_dashboard_data(str(tmp_path / "nope.json"), 48)
    assert result["available"] is False
    assert "hasn't synced" in result["reason"]


def test_no_path_configured_is_graceful():
    from app.services import trading_bot

    result = trading_bot.get_dashboard_data("", 48)
    assert result["available"] is False


def test_corrupt_file_is_graceful(tmp_path):
    from app.services import trading_bot

    path = tmp_path / "bad.json"
    path.write_text("{not valid json")
    result = trading_bot.get_dashboard_data(str(path), 48)
    assert result["available"] is False
    assert "couldn't be read" in result["reason"]


def test_happy_path_shape(snapshot_path):
    from app.services import trading_bot

    result = trading_bot.get_dashboard_data(snapshot_path, 48)
    assert result["available"] is True
    assert result["mode"] == "paper"

    live = result["live"]
    assert live["equity"] == 99431.49
    assert live["position_count"] == 1
    assert live["pending_order_count"] == 0
    assert live["next_trend_rebalance_due"] == "2026-10-01"

    research = result["research"]
    # by_year_pct dict -> sorted-by-year list
    assert research["by_year"] == [
        {"year": "2022", "pct": -8.3},
        {"year": "2023", "pct": 21.2},
        {"year": "2024", "pct": 20.7},
    ]
    assert research["notes"] == ["Kronos found no edge.", "Survivorship bias in the universe."]


def test_stale_snapshot_is_flagged(tmp_path):
    from app.services import trading_bot

    stale = dict(SNAPSHOT, generated_at="2020-01-01T00:00:00+00:00")
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(stale))

    result = trading_bot.get_dashboard_data(str(path), 48)
    assert result["available"] is True
    assert result["stale"] is True


def test_fresh_snapshot_is_not_flagged_stale(tmp_path):
    from datetime import datetime, timezone

    from app.services import trading_bot

    fresh = dict(SNAPSHOT, generated_at=datetime.now(timezone.utc).isoformat())
    path = tmp_path / "fresh.json"
    path.write_text(json.dumps(fresh))

    result = trading_bot.get_dashboard_data(str(path), 48)
    assert result["stale"] is False


def test_empty_positions_still_reports_pending_orders(tmp_path):
    from app.services import trading_bot

    snap = json.loads(json.dumps(SNAPSHOT))
    snap["live"]["positions"] = []
    snap["live"]["pending_orders"] = [
        {"symbol": "SPY", "side": "buy", "notional": 500.0, "qty": None, "submitted_at": "x"}
    ]
    path = tmp_path / "s.json"
    path.write_text(json.dumps(snap))

    result = trading_bot.get_dashboard_data(str(path), 48)
    assert result["live"]["position_count"] == 0
    assert result["live"]["pending_order_count"] == 1


# --------------------------------------------------------------------------
# The password gate on /projects/trading-bot -- see tests/test_documentation.py,
# same shape. A real (paper-for-now, possibly real-money-later) brokerage
# account is the most sensitive data on this site, so this is gated as hard
# as anything here: never listed, never in the footer disclaimer, fails
# closed when unconfigured, noindex on every response.
# --------------------------------------------------------------------------
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture()
def locked_client(app, client):
    from werkzeug.security import generate_password_hash

    app.config["TRADING_BOT_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    yield client
    app.config["TRADING_BOT_PASSWORD_HASH"] = None


@pytest.fixture()
def unlocked_client(locked_client):
    locked_client.post("/projects/trading-bot", data={"password": PASSWORD})
    return locked_client


def test_section_fails_closed_when_no_password_configured(client, app):
    app.config["TRADING_BOT_PASSWORD_HASH"] = None
    resp = client.get("/projects/trading-bot")
    assert resp.status_code == 503


def test_locked_page_asks_for_a_password_and_reveals_nothing(locked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    resp = locked_client.get("/projects/trading-bot")
    assert resp.status_code == 200
    assert b"Password" in resp.data
    assert b"AAPL" not in resp.data
    assert b"stat-grid" not in resp.data


def test_wrong_password_is_rejected_and_reveals_nothing(locked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    resp = locked_client.post("/projects/trading-bot", data={"password": "hunter2"})
    assert resp.status_code == 401
    assert b"not right" in resp.data
    assert b"AAPL" not in resp.data


def test_correct_password_unlocks_the_dashboard(locked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    resp = locked_client.post(
        "/projects/trading-bot", data={"password": PASSWORD}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"AAPL" in resp.data
    assert b"stat-grid" in resp.data


def test_unlock_persists_across_requests(unlocked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    resp = unlocked_client.get("/projects/trading-bot")
    assert resp.status_code == 200
    assert b"AAPL" in resp.data


def test_locking_again_re_gates_the_dashboard(unlocked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    unlocked_client.get("/projects/trading-bot/lock")

    resp = unlocked_client.get("/projects/trading-bot")
    assert b"AAPL" not in resp.data
    assert b"Password" in resp.data


def test_gate_shows_not_a_simulator_disclaimer_once_unlocked(unlocked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    resp = unlocked_client.get("/projects/trading-bot")
    assert b"Not a simulator" in resp.data


def test_responses_carry_noindex_header(locked_client):
    resp = locked_client.get("/projects/trading-bot")
    assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"


def test_page_not_listed_on_projects_index(client):
    resp = client.get("/projects")
    assert b"/projects/trading-bot" not in resp.data


def test_robots_txt_disallows_the_section(client):
    resp = client.get("/robots.txt")
    assert b"Disallow: /projects/trading-bot" in resp.data


# --------------------------------------------------------------------------
# /projects/trading-bot/research -- same gate, no separate password
# --------------------------------------------------------------------------
def test_research_page_redirects_to_the_gate_when_locked(client):
    resp = client.get("/projects/trading-bot/research")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/projects/trading-bot")


def test_research_page_renders_once_unlocked(unlocked_client, app, snapshot_path):
    app.config["TRADING_BOT_STATS_PATH"] = snapshot_path
    resp = unlocked_client.get("/projects/trading-bot/research")
    assert resp.status_code == 200
    assert b"Sharpe" in resp.data
    assert b"trading-bot-by-year-chart" in resp.data
    assert b"Kronos found no edge." in resp.data


def test_research_page_graceful_when_snapshot_missing(unlocked_client, app, tmp_path):
    app.config["TRADING_BOT_STATS_PATH"] = str(tmp_path / "nope.json")
    resp = unlocked_client.get("/projects/trading-bot/research")
    assert resp.status_code == 200
    assert b"Trading Bot" in resp.data
