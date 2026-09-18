"""GET /projects/market-warehouse/api/analyze/stream -- the live "autonomous
stock analysis" demo on the Market Data Warehouse page. Server-Sent Events,
driven by `assistant.stream_answer()` (LangGraph's own `.stream()`), so a
visitor watches each real step the instant it happens instead of waiting on
one blocking call. Validate the ticker against the 9-symbol projection set,
then forward the graph's own progress events verbatim.

Deterministic tool calls come from `ScriptedBackend`, same pattern as
tests/test_assistant_orchestrator_public.py.
"""
import json

import pytest

from app.extensions import db
from app.models import Leg, Strategy
from app.services import market_data
from app.services.assistant.backends import SCRIPTED_BACKEND

_ENDPOINT = "/projects/market-warehouse/api/analyze/stream"


def _book_a_stock_leg(ticker="AAPL", quantity=3, session_id="s1"):
    from app.services import instruments

    instrument = instruments.get_or_create_instrument(ticker, "stock")
    strategy = Strategy(session_id=session_id, name="Single Leg")
    leg = Leg(
        strategy=strategy, instrument=instrument, side="buy", quantity=quantity,
        entry_price=150.0, entry_underlying_price=150.0,
    )
    db.session.add(strategy)
    db.session.add(leg)
    db.session.commit()
    return leg


@pytest.fixture(autouse=True)
def _reset_scripted_backend():
    SCRIPTED_BACKEND.reset()
    yield
    SCRIPTED_BACKEND.reset()


@pytest.fixture(autouse=True)
def fake_market_data(monkeypatch):
    # No live network -- same stand-in test_assistant_orchestrator_public.py
    # and test_assistant_trading_tools.py use for get_quote's price lookup.
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 150.0)


def _events(resp):
    """Split an SSE response body into its parsed `data:` payloads, in
    order, skipping the leading ": connected" comment line."""
    body = resp.get_data(as_text=True)
    out = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if not chunk or chunk.startswith(":"):
            continue
        assert chunk.startswith("data: ")
        out.append(json.loads(chunk[len("data: "):]))
    return out


def test_rejects_a_ticker_outside_the_projection_set(client, app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    resp = client.get(_ENDPOINT, query_string={"ticker": "TSLA"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "TSLA" not in body["error"]  # rejection lists the supported set, not an echo
    assert "AAPL" in body["error"]
    # No model call was made -- the ticker check runs before the stream opens.
    assert SCRIPTED_BACKEND.calls == []


def test_rejects_a_missing_ticker(client, app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    resp = client.get(_ENDPOINT)
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_happy_path_streams_an_ordered_event_sequence(client, app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    SCRIPTED_BACKEND.push(
        {"tool_calls": [{"id": "1", "name": "get_quote", "arguments": '{"ticker": "AAPL"}'}]},
        {"tool_calls": [{"id": "2", "name": "get_projection", "arguments": '{"ticker": "AAPL"}'}]},
        {"text": "AAPL looks steady, for what a demo model is worth."},
    )

    resp = client.get(_ENDPOINT, query_string={"ticker": "aapl"})
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"

    events = _events(resp)
    assert [e["type"] for e in events] == [
        "retrieved",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "final",
    ]
    assert events[1] == {"type": "tool_call", "tool": "get_quote"}
    assert events[2]["tool"] == "get_quote"
    assert events[2]["arguments"] == {"ticker": "AAPL"}
    assert events[2]["result"]  # whatever the real tool returned, non-empty
    assert events[3] == {"type": "tool_call", "tool": "get_projection"}
    assert events[-1] == {"type": "final", "reply": "AAPL looks steady, for what a demo model is worth."}


def test_no_tool_calls_still_streams_a_final_event(client, app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    SCRIPTED_BACKEND.push({"text": "Nothing to check here."})

    resp = client.get(_ENDPOINT, query_string={"ticker": "SPY"})
    assert resp.status_code == 200
    events = _events(resp)
    assert [e["type"] for e in events] == ["retrieved", "final"]
    assert events[-1]["reply"] == "Nothing to check here."


def test_backend_unavailable_streams_an_error_event_not_a_500(client, app):
    # By the time a backend failure happens, the SSE response has already
    # started (status 200, headers sent) -- it can't switch to a 503, so
    # the failure has to surface as an in-stream error event instead.
    app.config["ASSISTANT_LLM_BACKEND"] = "groq"
    app.config["GROQ_API_KEY"] = ""
    resp = client.get(_ENDPOINT, query_string={"ticker": "MSFT"})
    assert resp.status_code == 200
    events = _events(resp)
    assert events[-1]["type"] == "error"
    assert events[-1]["message"]


def test_stock_analysis_config_prefers_the_dedicated_groq_key():
    """STOCK_ANALYSIS_GROQ_API_KEY exists specifically so this demo's Groq
    usage can never eat into the main /assistant chat's shared daily
    quota again (found the hard way: testing this feature burned through
    it). The route must actually build its config with that key, not just
    define the option and forget to wire it in."""
    from app.blueprints.market_warehouse.routes import _stock_analysis_config

    app_config = {"GROQ_API_KEY": "shared-assistant-key", "STOCK_ANALYSIS_GROQ_API_KEY": "dedicated-stock-analysis-key"}
    result = _stock_analysis_config(app_config)
    assert result["GROQ_API_KEY"] == "dedicated-stock-analysis-key"
    # Everything else in the config passes through untouched.
    assert result is not app_config


def test_stock_analysis_config_falls_back_to_the_shared_key_when_unset():
    from app.blueprints.market_warehouse.routes import _stock_analysis_config

    app_config = {"GROQ_API_KEY": "shared-assistant-key", "STOCK_ANALYSIS_GROQ_API_KEY": ""}
    assert _stock_analysis_config(app_config)["GROQ_API_KEY"] == "shared-assistant-key"


def test_projection_tool_results_are_enriched_with_real_chart_data(client, app, warehouse_db):
    """The get_projection tool's result is a sentence for the MODEL to
    read; a visitor needs the actual chart the numbers describe, not just
    the sentence. The route should attach the same projection + recent-
    actual series the page's own "Price projection" section draws, fetched
    by the exact method/lookback the model actually called with -- not the
    page's default combination."""
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    SCRIPTED_BACKEND.push(
        {"tool_calls": [{
            "id": "1",
            "name": "get_projection",
            "arguments": json.dumps({"ticker": "AAPL", "method": "mean_reversion", "lookback_days": 30}),
        }]},
        {"text": "Mean-reversion projection attached."},
    )

    resp = client.get(_ENDPOINT, query_string={"ticker": "AAPL"})
    assert resp.status_code == 200
    events = _events(resp)
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert "chart" in tool_result
    assert tool_result["chart"]["projection"]["tickers"] == ["AAPL"]
    assert tool_result["chart"]["projection"]["series"]["AAPL"]["projected"] == [108.0]
    assert "AAPL" in tool_result["chart"]["recent_actual"]

    # Other tool results (and the retrieved/final events) must NOT carry a
    # chart key at all -- only get_projection's result gets enriched.
    non_projection = [e for e in events if e.get("tool") != "get_projection"]
    assert all("chart" not in e for e in non_projection)


def test_a_rate_limited_tool_call_works_inside_the_stream(client, app):
    """Regression test: found live -- get_risk_report calls
    rate_limit.consume(), which reads request.remote_addr. The SSE
    generator body runs after the original request context has already
    been torn down (Werkzeug iterates it lazily), so a plain
    `with app.app_context():` inside the stream restores current_app/db
    but NOT `request` -- every rate-limited trading tool call failed with
    "RuntimeError: Working outside of request context", swallowed by
    dispatch_trading_tool into an opaque "That didn't work (RuntimeError)."
    The route now rebuilds a real request context from the original
    request's own WSGI environ (`app.request_context(environ)`) inside the
    generator; this must actually resolve request.remote_addr, not just
    avoid the crash."""
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    with app.app_context():
        leg = _book_a_stock_leg(ticker="AAPL", quantity=3)
        leg_id = leg.id

    SCRIPTED_BACKEND.push(
        {"tool_calls": [{"id": "1", "name": "get_risk_report", "arguments": json.dumps({"leg_id": leg_id})}]},
        {"text": "All clear."},
    )

    resp = client.get(_ENDPOINT, query_string={"ticker": "AAPL"})
    assert resp.status_code == 200
    events = _events(resp)
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert "Risk report" in tool_result["result"]
    assert "RuntimeError" not in tool_result["result"]
    assert "Working outside" not in tool_result["result"]
