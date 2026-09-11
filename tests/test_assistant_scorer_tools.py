"""app/services/assistant/scorer_tools.py -- the Company Scorer tool layer.

Unlike the job-tracker tools, these carry no authorization gate:
`build_scorer_tools()` always returns both schemas, since scoring a
ticker or running a backtest only reads public market data -- there is
nothing here for a crafted message to "write". The one thing that does
need guarding is cost (both tools hit SEC EDGAR / yfinance under the
hood), so `dispatch_scorer_tool` draws from the exact same
`qr_score` / `qr_backtest` rate-limit buckets the `/projects/qr-quant-
scraper` web routes use -- proven here by exhausting the bucket through
one channel and showing the other is refused too.

`edgar`/`market_data` are monkeypatched throughout (same technique as
tests/test_qr.py) so nothing here touches live SEC EDGAR or yfinance.
"""
import pytest

from app import create_app
from app.extensions import limiter
from app.services import backtest, edgar, market_data
from app.services.assistant.scorer_tools import build_scorer_tools, dispatch_scorer_tool


def _fundamentals(**overrides):
    base = {
        "Revenues": {"current": 1000, "prior": 900},
        "NetIncomeLoss": {"current": 100, "prior": 80},
        "Assets": {"current": 2000, "prior": 1800},
        "Liabilities": {"current": 800, "prior": 750},
        "StockholdersEquity": {"current": 1200, "prior": 1050},
        "AssetsCurrent": {"current": 600, "prior": 550},
        "LiabilitiesCurrent": {"current": 300, "prior": 280},
        "OperatingIncomeLoss": {"current": 150, "prior": 120},
        "InterestExpense": {"current": 20, "prior": 18},
        "DepreciationDepletionAndAmortization": {"current": 50, "prior": 45},
        "CostOfRevenue": {"current": 600, "prior": 540},
    }
    base.update(overrides)
    return base


def _empty_fundamentals():
    return {key: {"current": None, "prior": None} for key in _fundamentals()}


def _market_info(**overrides):
    base = {"marketCap": 5000, "trailingPE": 20.0, "priceToBook": 4.0}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def fake_data_sources(monkeypatch):
    monkeypatch.setattr(edgar, "get_fundamentals", lambda ticker, as_of_date=None: _fundamentals())
    monkeypatch.setattr(market_data, "get_info", lambda ticker: _market_info())


# --------------------------------------------------------------------------
# build_scorer_tools -- always offered, no chokepoint
# --------------------------------------------------------------------------

def test_build_scorer_tools_returns_both_schemas():
    tools = build_scorer_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == {"score_company", "run_backtest"}


def test_score_company_schema_requires_only_ticker():
    tools = build_scorer_tools()
    spec = next(t for t in tools if t["function"]["name"] == "score_company")
    assert spec["function"]["parameters"]["required"] == ["ticker"]
    assert "ticker" in spec["function"]["parameters"]["properties"]


def test_run_backtest_schema_has_no_required_fields():
    tools = build_scorer_tools()
    spec = next(t for t in tools if t["function"]["name"] == "run_backtest")
    assert spec["function"]["parameters"]["required"] == []
    assert "tickers" in spec["function"]["parameters"]["properties"]
    assert "years_ago" in spec["function"]["parameters"]["properties"]


def test_build_scorer_tools_hands_out_a_fresh_copy():
    a = build_scorer_tools()
    a[0]["function"]["name"] = "mutated"
    assert build_scorer_tools()[0]["function"]["name"] == "score_company"


# --------------------------------------------------------------------------
# dispatch_scorer_tool -- score_company
# --------------------------------------------------------------------------

def test_dispatch_score_company_success(app):
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", {"ticker": "AAPL"})
    assert "AAPL" in out
    assert "overall score" in out
    assert "valuation" in out


def test_dispatch_score_company_accepts_as_of_date(app):
    with app.test_request_context():
        out = dispatch_scorer_tool(
            "score_company", {"ticker": "AAPL", "as_of_date": "2024-01-01"}
        )
    assert "2024-01-01" in out


def test_dispatch_score_company_bad_date_is_a_string(app):
    with app.test_request_context():
        out = dispatch_scorer_tool(
            "score_company", {"ticker": "AAPL", "as_of_date": "not-a-date"}
        )
    assert "valid date" in out.lower()


def test_dispatch_score_company_missing_ticker(app):
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", {})
    assert "which ticker" in out.lower()


def test_dispatch_score_company_rejects_off_whitelist_ticker(app):
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", {"ticker": "NOTATICKER"})
    assert "not on the supported ticker list" in out


def test_dispatch_score_company_reports_no_data_available(app, monkeypatch):
    monkeypatch.setattr(edgar, "get_fundamentals", lambda ticker, as_of_date=None: _empty_fundamentals())
    monkeypatch.setattr(market_data, "get_info", lambda ticker: {"marketCap": None, "trailingPE": None, "priceToBook": None})
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", {"ticker": "AAPL"})
    assert "not enough data" in out.lower()


def test_dispatch_score_company_surfaces_market_data_error(app, monkeypatch):
    def _boom(ticker):
        raise market_data.MarketDataError("market data temporarily unavailable: boom")

    monkeypatch.setattr(market_data, "get_info", _boom)
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", {"ticker": "AAPL"})
    assert "temporarily unavailable" in out


# --------------------------------------------------------------------------
# dispatch_scorer_tool -- run_backtest
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backtest_cache(monkeypatch):
    monkeypatch.setattr(backtest, "_cache", {})


def test_dispatch_run_backtest_success(app, monkeypatch):
    monkeypatch.setattr(market_data, "get_price_near_date", lambda ticker, target_date: 100.0)
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 120.0)
    with app.test_request_context():
        out = dispatch_scorer_tool("run_backtest", {"tickers": ["AAPL"], "years_ago": 1})
    assert "AAPL" in out
    assert "score" in out
    assert "Correlation" in out


def test_dispatch_run_backtest_clamps_years_ago(app, monkeypatch):
    monkeypatch.setattr(market_data, "get_price_near_date", lambda ticker, target_date: 100.0)
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 120.0)
    with app.test_request_context():
        out = dispatch_scorer_tool("run_backtest", {"tickers": ["AAPL"], "years_ago": 99})
    assert "5 year(s) back" in out


def test_dispatch_run_backtest_bad_years_ago_is_a_string(app):
    with app.test_request_context():
        out = dispatch_scorer_tool("run_backtest", {"years_ago": "soon"})
    assert "whole number" in out.lower()


def test_dispatch_run_backtest_reports_per_row_errors(app, monkeypatch):
    def _boom(ticker, target_date):
        raise market_data.MarketDataError("no price history")

    monkeypatch.setattr(market_data, "get_price_near_date", _boom)
    with app.test_request_context():
        out = dispatch_scorer_tool("run_backtest", {"tickers": ["AAPL"]})
    assert "no price history" in out
    assert "not enough valid data" in out.lower()


# --------------------------------------------------------------------------
# dispatch_scorer_tool -- generic contract (unknown tool / bad json / never raises)
# --------------------------------------------------------------------------

def test_dispatch_unknown_tool_is_a_string(app):
    with app.test_request_context():
        assert "Unknown tool" in dispatch_scorer_tool("frobnicate", {})


def test_dispatch_bad_json_arguments_is_a_string(app):
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", "{not json")
    assert "parse" in out.lower()


def test_dispatch_non_dict_arguments_do_not_raise(app):
    with app.test_request_context():
        out = dispatch_scorer_tool("score_company", [1, 2, 3])
    assert "which ticker" in out.lower()


# --------------------------------------------------------------------------
# Rate-limit parity: the web route and the chat tool share one bucket
# --------------------------------------------------------------------------

@pytest.fixture
def rate_limited_app():
    """Mirrors tests/test_assistant_rate_limit.py's `rate_limited_app`: a
    testing app with rate limiting actually enabled and tiny score/backtest
    limits, so a couple of calls are enough to exhaust a bucket."""
    application = create_app("testing")
    application.config["RATELIMIT_ENABLED"] = True
    application.config["QR_SCORE_RATE_LIMIT"] = "1 per hour"
    application.config["QR_BACKTEST_RATE_LIMIT"] = "1 per hour"
    limiter.init_app(application)
    yield application


def test_score_route_and_tool_share_one_rate_limit_bucket(rate_limited_app, monkeypatch):
    monkeypatch.setattr(edgar, "get_fundamentals", lambda ticker, as_of_date=None: _fundamentals())
    monkeypatch.setattr(market_data, "get_info", lambda ticker: _market_info())

    client = rate_limited_app.test_client()
    first = client.get("/projects/qr-quant-scraper?ticker=AAPL")
    assert first.status_code == 200

    # The route already used up the "1 per hour" bucket for this IP -- the
    # tool dispatcher, sharing the same action name and IP-keyed bucket,
    # must refuse rather than making its own network call.
    with rate_limited_app.test_request_context():
        out = dispatch_scorer_tool("score_company", {"ticker": "AAPL"})
    assert "hit the hourly limit" in out

    second = client.get("/projects/qr-quant-scraper?ticker=AAPL")
    assert second.status_code == 429


def test_backtest_route_and_tool_share_one_rate_limit_bucket(rate_limited_app, monkeypatch):
    monkeypatch.setattr(backtest, "_cache", {})
    monkeypatch.setattr(market_data, "get_price_near_date", lambda ticker, target_date: 100.0)
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 120.0)

    client = rate_limited_app.test_client()

    # Exhaust the shared "1 per hour" bucket through the tool first this
    # time, to prove the sharing works in both directions.
    with rate_limited_app.test_request_context():
        out = dispatch_scorer_tool("run_backtest", {"tickers": ["AAPL"]})
    assert "Correlation" in out

    resp = client.get("/projects/qr-quant-scraper/backtest")
    assert resp.status_code == 429
