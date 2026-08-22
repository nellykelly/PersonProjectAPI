import pytest

from app.services import backtest, edgar, market_data, quant_score


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


def _market_info(**overrides):
    base = {"marketCap": 5000, "trailingPE": 20.0, "priceToBook": 4.0}
    base.update(overrides)
    return base


# ---------- pure scoring math ----------


def test_compute_metrics_basic_ratios():
    metrics = quant_score.compute_metrics(_fundamentals(), _market_info())
    assert metrics["pe"] == 20.0
    assert metrics["pb"] == 4.0
    assert metrics["debt_to_equity"] == pytest.approx(800 / 1200)
    assert metrics["current_ratio"] == pytest.approx(600 / 300)
    assert metrics["revenue_growth"] == pytest.approx((1000 - 900) / 900)
    assert metrics["net_margin"] == pytest.approx(100 / 1000)
    assert metrics["roe"] == pytest.approx(100 / 1200)


def test_compute_metrics_handles_missing_data_gracefully():
    fundamentals = _fundamentals(Revenues={"current": None, "prior": None})
    metrics = quant_score.compute_metrics(fundamentals, _market_info())
    assert metrics["revenue_growth"] is None
    assert metrics["net_margin"] is None
    # unaffected metrics still compute
    assert metrics["debt_to_equity"] is not None


def test_normalize_direction_and_clamping():
    # lower_better: value at lo -> 100, value at hi -> 0
    assert quant_score._normalize(5, lo=5, hi=40, direction="lower_better") == 100.0
    assert quant_score._normalize(40, lo=5, hi=40, direction="lower_better") == 0.0
    # higher_better: value at lo -> 0, value at hi -> 100
    assert quant_score._normalize(0.5, lo=0.5, hi=3, direction="higher_better") == 0.0
    assert quant_score._normalize(3, lo=0.5, hi=3, direction="higher_better") == 100.0
    # out-of-range values clamp instead of exploding
    assert quant_score._normalize(-100, lo=0.5, hi=3, direction="higher_better") == 0.0
    assert quant_score._normalize(None, lo=0, hi=1, direction="higher_better") is None


def test_compute_overall_renormalizes_when_a_category_is_missing():
    category_scores = {"valuation": 80.0, "leverage": None, "growth": 60.0, "profitability": 40.0}
    weights = {"valuation": 0.25, "leverage": 0.25, "growth": 0.25, "profitability": 0.25}
    overall, used_weights = quant_score.compute_overall(category_scores, weights)

    assert "leverage" not in used_weights
    assert sum(used_weights.values()) == pytest.approx(1.0)
    # equal renormalized weight across the 3 remaining categories
    assert used_weights["valuation"] == pytest.approx(1 / 3)
    assert overall == pytest.approx((80.0 + 60.0 + 40.0) / 3, abs=0.1)


def test_compute_overall_returns_none_when_no_data_available():
    overall, used_weights = quant_score.compute_overall(
        {"valuation": None, "leverage": None, "growth": None, "profitability": None},
        {"valuation": 1.0},
    )
    assert overall is None
    assert used_weights == {}


# ---------- route (edgar/market_data monkeypatched -- no live network) ----------


@pytest.fixture(autouse=True)
def fake_data_sources(monkeypatch):
    monkeypatch.setattr(edgar, "get_fundamentals", lambda ticker, as_of_date=None: _fundamentals())
    monkeypatch.setattr(market_data, "get_info", lambda ticker: _market_info())


def test_qr_index_loads_without_ticker(client):
    resp = client.get("/projects/qr-quant-scraper")
    assert resp.status_code == 200
    assert b"Quant Company Scorer" in resp.data


def test_qr_score_route(client):
    resp = client.get("/projects/qr-quant-scraper?ticker=AAPL")
    assert resp.status_code == 200
    assert b"AAPL" in resp.data
    assert b"overall score" in resp.data


def test_qr_rejects_ticker_off_whitelist(client):
    resp = client.get("/projects/qr-quant-scraper?ticker=NOTATICKER")
    assert resp.status_code == 200
    assert b"not on the supported ticker list" in resp.data


def test_qr_backtest_route(client, monkeypatch):
    monkeypatch.setattr(backtest, "_cache", {})  # avoid leaking state across test runs
    monkeypatch.setattr(market_data, "get_price_near_date", lambda ticker, target_date: 100.0)
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 120.0)

    resp = client.get("/projects/qr-quant-scraper/backtest")
    assert resp.status_code == 200
    assert b"Backtest" in resp.data
