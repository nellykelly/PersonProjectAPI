"""The chat route's chart enrichment (app/blueprints/assistant/routes.py
`_charts_for`): when the model calls `get_traffic_summary`, the JSON
response carries a `charts` list built straight from `net_monitor`'s own
live buckets -- the same "recompute the real data for the frontend rather
than smuggle it through the model's text result" pattern
`market_warehouse.routes.api_analyze_stream`'s `_enrich()` uses for the
projection chart.
"""
import json

from app.extensions import db
from app.services import net_monitor
from app.services.assistant.backends import SCRIPTED_BACKEND


def _set_scripted(app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"


def test_chat_endpoint_attaches_a_traffic_chart_when_the_tool_is_called(client, app):
    with app.app_context():
        db.create_all()
    net_monitor.reset_for_tests()
    net_monitor.log_inbound("GET", "/about", 200, 10.0, endpoint="about.index")
    net_monitor.log_inbound("GET", "/about", 200, 12.0, endpoint="about.index")

    _set_scripted(app)
    SCRIPTED_BACKEND.reset()
    SCRIPTED_BACKEND.push(
        {
            "tool_calls": [
                {"id": "call_1", "name": "get_traffic_summary", "arguments": "{}"}
            ]
        },
        {"text": "Here's the traffic picture."},
    )

    resp = client.post(
        "/api/assistant/chat",
        json={"message": "can you show me the traffic graph", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["error"] is False
    assert body["reply"] == "Here's the traffic picture."
    assert len(body["charts"]) == 1
    chart = body["charts"][0]
    assert chart["title"] == "Request volume over time"
    assert len(chart["labels"]) == len(chart["series"][0]["data"])
    assert {s["label"] for s in chart["series"]} == {"Inbound", "Outbound"}
    assert sum(chart["series"][0]["data"]) == 2  # the two inbound hits above

    SCRIPTED_BACKEND.reset()


def test_chat_endpoint_has_no_chart_when_no_chart_tool_was_called(client, app):
    with app.app_context():
        db.create_all()
    _set_scripted(app)
    SCRIPTED_BACKEND.reset()
    SCRIPTED_BACKEND.push({"text": "just chatting, no tools needed"})

    resp = client.post(
        "/api/assistant/chat",
        json={"message": "hi", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["charts"] == []

    SCRIPTED_BACKEND.reset()
