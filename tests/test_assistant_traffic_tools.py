"""The Site Traffic Analytics assistant tool -- a single public, read-only
summary of the same aggregate numbers `/projects/network-sniffer` renders,
with no authorization gate and no rate limit beyond what the page already
has, matching `test_assistant_timedsquares_tools.py`'s coverage shape for
a tool of this kind.
"""
import json

from app.services import net_monitor
from app.services.assistant.traffic_tools import (
    build_traffic_tools,
    dispatch_traffic_tool,
)


# --------------------------------------------------------------------------
# build_traffic_tools -- schema shape
# --------------------------------------------------------------------------

def test_build_traffic_tools_returns_one_schema():
    tools = build_traffic_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == {"get_traffic_summary"}


def test_schema_shape_matches_the_tool_calling_contract():
    tool = build_traffic_tools()[0]
    assert tool["type"] == "function"
    fn = tool["function"]
    assert fn["name"] == "get_traffic_summary"
    params = fn["parameters"]
    assert params["type"] == "object"
    assert params["required"] == []
    assert params["additionalProperties"] is False


def test_build_traffic_tools_hands_out_a_fresh_copy():
    a = build_traffic_tools()
    a[0]["function"]["name"] = "mutated"
    assert build_traffic_tools()[0]["function"]["name"] == "get_traffic_summary"


# --------------------------------------------------------------------------
# dispatch_traffic_tool
# --------------------------------------------------------------------------

def test_get_traffic_summary_empty_buffer_is_a_friendly_string():
    net_monitor.reset_for_tests()
    out = dispatch_traffic_tool("get_traffic_summary", {})
    assert isinstance(out, str)
    assert "no" in out.lower()


def test_get_traffic_summary_reports_volume_and_latency():
    net_monitor.reset_for_tests()
    net_monitor.log_inbound("GET", "/about", 200, 12.5, endpoint="about.index")
    net_monitor.log_inbound("GET", "/about", 200, 15.0, endpoint="about.index")
    net_monitor.log_outbound("market_data", "GET", "https://query1.finance.yahoo.com/x", 200, 40.0)

    out = dispatch_traffic_tool("get_traffic_summary", {})
    assert "2 inbound" in out
    assert "1 outbound" in out
    assert "Inbound latency" in out
    assert "about.index" in out


def test_get_traffic_summary_reports_error_rate():
    net_monitor.reset_for_tests()
    net_monitor.log_inbound("GET", "/x", 500, 5.0, endpoint="x")
    net_monitor.log_inbound("GET", "/y", 404, 5.0, endpoint="y")

    out = dispatch_traffic_tool("get_traffic_summary", {})
    assert "4xx" in out
    assert "5xx" in out


def test_dispatch_unknown_tool_is_a_string():
    assert "Unknown tool" in dispatch_traffic_tool("frobnicate", {})


def test_dispatch_bad_json_arguments_is_a_string():
    out = dispatch_traffic_tool("get_traffic_summary", "{not json")
    assert isinstance(out, str)
    assert "parse" in out.lower()


def test_dispatch_accepts_json_string_arguments():
    net_monitor.reset_for_tests()
    net_monitor.log_inbound("GET", "/about", 200, 10.0, endpoint="about.index")
    out = dispatch_traffic_tool("get_traffic_summary", json.dumps({}))
    assert "1 inbound" in out


def test_dispatch_no_authorized_kwarg_required():
    # Unlike dispatch_job_tool, this tool has no authorization gate at all
    # -- calling it with just (name, arguments) must work.
    net_monitor.reset_for_tests()
    out = dispatch_traffic_tool("get_traffic_summary", {})
    assert isinstance(out, str)
    assert "didn't work" not in out
