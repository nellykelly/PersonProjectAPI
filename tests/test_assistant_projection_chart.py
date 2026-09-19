"""The chat route's projection-chart enrichment (app/blueprints/assistant/
routes.py `_projection_chart`): when the model calls `get_projection`, the
JSON response carries a real actual/projected/95%-band chart built from
`market_warehouse.get_projection_chart_data` -- the same service call and
default-filling `market_warehouse.routes.api_analyze_stream`'s `_enrich()`
already uses for the SSE stock-analysis demo, so a chart pulled from the
main chat and one streamed from that demo agree.
"""
import json

from app.extensions import db
from app.services.assistant.backends import SCRIPTED_BACKEND


def _set_scripted(app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"


def test_chat_endpoint_attaches_a_projection_chart_when_the_tool_is_called(client, app, warehouse_db):
    with app.app_context():
        db.create_all()
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db

    _set_scripted(app)
    SCRIPTED_BACKEND.reset()
    SCRIPTED_BACKEND.push(
        {
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "get_projection",
                    "arguments": json.dumps(
                        {"ticker": "AAPL", "method": "mean_reversion", "lookback_days": 30}
                    ),
                }
            ]
        },
        {"text": "Here's the AAPL projection."},
    )

    resp = client.post(
        "/api/assistant/chat",
        json={"message": "can you graph AAPL's projection", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["error"] is False
    assert len(body["charts"]) == 1
    chart = body["charts"][0]
    assert chart["kind"] == "projection"
    assert chart["ticker"] == "AAPL"
    assert chart["projected"] == [108.0]
    assert chart["lower"] == [104.0]
    assert chart["upper"] == [112.0]
    assert chart["r_squared"] == 0.55
    assert len(chart["actual"]) > 0

    SCRIPTED_BACKEND.reset()


def test_chat_endpoint_omits_the_chart_for_an_unsupported_ticker(client, app, warehouse_db):
    # TSLA has no precomputed projection in the fixture warehouse -- the
    # model's own text already explains that; the route must not attach a
    # chart with nothing real to draw.
    with app.app_context():
        db.create_all()
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db

    _set_scripted(app)
    SCRIPTED_BACKEND.reset()
    SCRIPTED_BACKEND.push(
        {
            "tool_calls": [
                {"id": "call_1", "name": "get_projection", "arguments": json.dumps({"ticker": "TSLA"})}
            ]
        },
        {"text": "TSLA has no precomputed projection."},
    )

    resp = client.post(
        "/api/assistant/chat",
        json={"message": "graph TSLA's projection", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["charts"] == []

    SCRIPTED_BACKEND.reset()


def test_chat_endpoint_survives_a_malformed_projection_tool_arguments_shape(client, app, warehouse_db):
    # A raw, non-JSON-parseable arguments string comes back from
    # _extract_tool_trace as a plain string, not a dict -- the chart
    # enrichment must swallow that, not 500 the whole turn.
    with app.app_context():
        db.create_all()
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db

    _set_scripted(app)
    SCRIPTED_BACKEND.reset()
    SCRIPTED_BACKEND.push(
        {
            "tool_calls": [
                {"id": "call_1", "name": "get_projection", "arguments": "{not json"}
            ]
        },
        {"text": "Couldn't parse that ticker."},
    )

    resp = client.post(
        "/api/assistant/chat",
        json={"message": "graph it", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["error"] is False
    assert body["charts"] == []

    SCRIPTED_BACKEND.reset()
