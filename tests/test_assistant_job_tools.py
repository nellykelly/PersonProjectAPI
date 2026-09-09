"""Hera Phase 2 -- the job-tracker tool layer and its privilege boundary.

The load-bearing claim under test: the tools are handed to the model only
when the caller is the signed-in owner *and* the /job-tracker gate is
unlocked this session. For anyone else `build_job_tools` returns `[]`, the
orchestrator makes a plain single call, and no write can happen -- proven
here at the unit, orchestrator, and HTTP layers.

Deterministic tool calls come from `ScriptedBackend` (kind "scripted"): a
queue of pre-programmed turns, each `{"text": ...}` or
`{"tool_calls": [...]}`.
"""
import json

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import JobApplication, JobApplicationEvent, AssistantQuery
from app.services import assistant, job_tracker
from app.services.assistant import prompts
from app.services.assistant.authz import can_use_job_tools, is_owner
from app.services.assistant.backends import SCRIPTED_BACKEND
from app.services.assistant.job_tools import build_job_tools, dispatch_job_tool

JT_PASSWORD = "unlock-me-please"


@pytest.fixture(autouse=True)
def _reset_scripted_backend():
    SCRIPTED_BACKEND.reset()
    yield
    SCRIPTED_BACKEND.reset()


@pytest.fixture()
def db_ready(app):
    with app.app_context():
        db.create_all()
        assistant.reindex()
        yield


@pytest.fixture()
def owner_unlocked_client(app, make_user, login):
    """A test client that is (a) logged in as the ADMIN_USERNAME account and
    (b) has passed the /job-tracker password gate -- i.e. `can_use_job_tools()`
    is true for its requests. Backend is forced to "scripted"."""
    app.config["ADMIN_USERNAME"] = "owner"
    app.config["JOB_TRACKER_PASSWORD_HASH"] = generate_password_hash(JT_PASSWORD)
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    with app.app_context():
        db.create_all()
        assistant.reindex()
    username = make_user("owner")
    c = login(username)
    resp = c.post("/job-tracker/unlock", data={"password": JT_PASSWORD})
    assert resp.status_code == 302, resp.data
    yield c
    app.config["ADMIN_USERNAME"] = ""
    app.config["JOB_TRACKER_PASSWORD_HASH"] = None


# --------------------------------------------------------------------------
# build_job_tools -- the chokepoint
# --------------------------------------------------------------------------

def test_build_job_tools_is_empty_unless_authorized():
    assert build_job_tools(False) == []


def test_build_job_tools_authorized_returns_the_full_schema_set():
    tools = build_job_tools(True)
    names = {t["function"]["name"] for t in tools}
    assert names == {
        "add_application",
        "update_application",
        "set_application_status",
        "list_applications",
        "find_application",
        "ghost_stale_applications",
    }
    assert "delete_application" not in names


def test_status_enum_tracks_the_model_tuple():
    from app.models import JOB_APPLICATION_STATUSES

    add = next(t for t in build_job_tools(True) if t["function"]["name"] == "add_application")
    assert add["function"]["parameters"]["properties"]["status"]["enum"] == list(
        JOB_APPLICATION_STATUSES
    )


def test_build_job_tools_hands_out_a_fresh_copy():
    a = build_job_tools(True)
    a[0]["function"]["name"] = "mutated"
    assert build_job_tools(True)[0]["function"]["name"] == "add_application"


# --------------------------------------------------------------------------
# can_use_job_tools -- the predicate
# --------------------------------------------------------------------------

def test_predicate_false_when_admin_username_unset(app):
    app.config["ADMIN_USERNAME"] = ""
    with app.test_request_context("/api/assistant/chat"):
        assert is_owner() is False
        assert can_use_job_tools() is False


def test_predicate_false_for_anonymous_even_with_unlock_flag(app):
    from flask import session

    app.config["ADMIN_USERNAME"] = "owner"
    with app.test_request_context("/api/assistant/chat"):
        session["job_tracker_unlocked"] = True
        assert is_owner() is False  # nobody is signed in
        assert can_use_job_tools() is False


def test_predicate_true_only_after_login_and_unlock(owner_unlocked_client, app):
    # Proven transitively: owner_unlocked_client got here only by logging in
    # AND unlocking, and the route test below shows a write goes through.
    # Here we check the "logged in but NOT unlocked" half is still closed.
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    SCRIPTED_BACKEND.push(
        {"tool_calls": [{"id": "c1", "name": "add_application",
                         "arguments": json.dumps({"company": "Locked", "role": "X"})}]},
        {"text": "should not get here"},
    )
    owner_unlocked_client.get("/job-tracker/lock")  # drop the unlock flag
    resp = owner_unlocked_client.post(
        "/api/assistant/chat", json={"message": "add Locked X", "history": []}
    )
    assert resp.status_code == 200
    with app.app_context():
        assert JobApplication.query.filter_by(company_name="Locked").first() is None


# --------------------------------------------------------------------------
# dispatch_job_tool -- routes to the service, never raises
# --------------------------------------------------------------------------

def test_dispatch_add_writes_row_and_assistant_sourced_event(db_ready, app):
    with app.app_context():
        out = dispatch_job_tool(
            "add_application",
            {"company": "Plaid", "role": "Backend SWE", "status": "Applied"},
            authorized=True,
        )
        assert "Plaid" in out
        row = JobApplication.query.filter_by(company_name="Plaid").one()
        ev = JobApplicationEvent.query.filter_by(application_id=row.id).one()
        assert ev.action == "create"
        assert ev.source == "assistant"


def test_dispatch_refuses_and_does_not_write_when_unauthorized(db_ready, app):
    with app.app_context():
        out = dispatch_job_tool(
            "add_application",
            {"company": "Nope", "role": "X"},
            authorized=False,
        )
        assert "isn't available" in out
        assert JobApplication.query.count() == 0


def test_dispatch_unknown_tool_is_a_string(db_ready, app):
    with app.app_context():
        assert "Unknown tool" in dispatch_job_tool("frobnicate", {}, authorized=True)


def test_dispatch_bad_json_arguments_is_a_string(db_ready, app):
    with app.app_context():
        assert "parse" in dispatch_job_tool("list_applications", "{not json", authorized=True)


@pytest.mark.parametrize(
    "name, args, needle",
    [
        ("add_application", {"company": "OnlyName"}, "required"),
        ("find_application", {"company": "Ghost Corp"}, "No application matches"),
        ("set_application_status", {"company": "Acme", "status": "Bananas"}, "status"),
    ],
)
def test_dispatch_job_tracker_errors_come_back_as_text(db_ready, app, name, args, needle):
    with app.app_context():
        job_tracker.create_application(
            {"company_name": "Acme", "role_title": "Engineer I"}, source="web"
        )
        out = dispatch_job_tool(name, args, authorized=True)
        assert isinstance(out, str)
        assert needle.lower() in out.lower()


def test_dispatch_ambiguous_locator_is_a_string(db_ready, app):
    with app.app_context():
        for role in ("SWE I", "SWE II"):
            job_tracker.create_application(
                {"company_name": "Ramp", "role_title": role}, source="web"
            )
        out = dispatch_job_tool("find_application", {"company": "Ramp"}, authorized=True)
        assert isinstance(out, str)
        assert "ramp" in out.lower()


def test_dispatch_update_reports_when_nothing_to_change(db_ready, app):
    with app.app_context():
        job_tracker.create_application(
            {"company_name": "Notion", "role_title": "AI Platform"}, source="web"
        )
        out = dispatch_job_tool("update_application", {"company": "Notion"}, authorized=True)
        assert "nothing to change" in out.lower()


def test_dispatch_ghost_stale_applications_moves_old_applied_rows(db_ready, app):
    from datetime import timedelta

    from app.models import JobApplication, utcnow

    with app.app_context():
        old = job_tracker.create_application(
            {"company_name": "Stale Corp", "role_title": "SWE"}, source="web"
        )
        old.status_updated_at = utcnow() - timedelta(days=25)
        db.session.commit()
        fresh = job_tracker.create_application(
            {"company_name": "Fresh Corp", "role_title": "SWE"}, source="web"
        )

        out = dispatch_job_tool(
            "ghost_stale_applications", {"weeks": 2.5}, authorized=True
        )
        assert "Stale Corp" in out
        assert job_tracker.get_application(old.id).status == "Ghosted"
        assert job_tracker.get_application(fresh.id).status == "Applied"
        ev = JobApplicationEvent.query.filter_by(
            application_id=old.id, field_name="status"
        ).one()
        assert ev.new_value == "Ghosted" and ev.source == "assistant"


def test_dispatch_ghost_stale_uses_the_config_default_when_weeks_omitted(db_ready, app):
    with app.app_context():
        app.config["JOB_TRACKER_GHOST_AFTER_WEEKS"] = 2.5
        out = dispatch_job_tool("ghost_stale_applications", {}, authorized=True)
        assert "nothing has been sitting" in out.lower()


# --------------------------------------------------------------------------
# orchestrator loop
# --------------------------------------------------------------------------

def test_unauthorized_path_makes_exactly_one_plain_call(db_ready, app):
    with app.app_context():
        app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
        SCRIPTED_BACKEND.push({"text": "a plain answer"})
        ans = assistant.answer("hello", [], config=app.config, job_tools_authorized=False)
        assert ans.reply == "a plain answer"
        assert len(SCRIPTED_BACKEND.calls) == 1
        assert SCRIPTED_BACKEND.calls[0]["tools"] is None


def test_authorized_loop_writes_and_sums_tokens(db_ready, app):
    with app.app_context():
        app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
        SCRIPTED_BACKEND.push(
            {"tool_calls": [{"id": "c1", "name": "add_application",
                             "arguments": json.dumps({"company": "Kalshi",
                                                      "role": "Trading Platform SWE",
                                                      "status": "Phone Screen"})}]},
            {"text": "Done -- Kalshi is in as a phone screen."},
        )
        ans = assistant.answer(
            "add kalshi", [], config=app.config, job_tools_authorized=True
        )
        assert "Kalshi" in ans.reply
        assert ans.prompt_tokens == 2  # 1 per scripted turn, two turns
        assert ans.completion_tokens == 2
        row = JobApplication.query.filter_by(company_name="Kalshi").one()
        assert row.status == "Phone Screen"
        assert JobApplicationEvent.query.filter_by(
            application_id=row.id, source="assistant"
        ).count() >= 1


def test_authorized_loop_stops_at_the_iteration_cap(db_ready, app):
    with app.app_context():
        app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
        for _ in range(8):
            SCRIPTED_BACKEND.push(
                {"tool_calls": [{"id": "x", "name": "list_applications",
                                 "arguments": "{}"}]}
            )
        ans = assistant.answer(
            "loop", [], config=app.config, job_tools_authorized=True
        )
        assert len(SCRIPTED_BACKEND.calls) == 4
        assert ans.reply  # a non-empty fallback, no exception


# --------------------------------------------------------------------------
# the HTTP boundary
# --------------------------------------------------------------------------

def test_anonymous_request_cannot_write(db_ready, app, client):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    # Even if the model *tried* to call a tool, an anon caller gets no tools.
    SCRIPTED_BACKEND.push(
        {"tool_calls": [{"id": "c1", "name": "add_application",
                         "arguments": json.dumps({"company": "Sneaky", "role": "X"})}]},
        {"text": "nope"},
    )
    resp = client.post(
        "/api/assistant/chat",
        json={"message": "add an application for Sneaky", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert set(body) >= {"reply", "sources", "backend", "error"}
    assert body["error"] is False
    with app.app_context():
        assert JobApplication.query.filter_by(company_name="Sneaky").first() is None
    # the anon call never asked for tools
    assert SCRIPTED_BACKEND.calls[0]["tools"] is None


def test_owner_unlocked_request_can_write(owner_unlocked_client, app):
    SCRIPTED_BACKEND.push(
        {"tool_calls": [{"id": "c1", "name": "add_application",
                         "arguments": json.dumps({"company": "Affirm",
                                                  "role": "Card Ledger SWE"})}]},
        {"text": "Added Affirm."},
    )
    resp = owner_unlocked_client.post(
        "/api/assistant/chat",
        json={"message": "add Affirm card ledger swe", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["error"] is False
    with app.app_context():
        row = JobApplication.query.filter_by(company_name="Affirm").one()
        ev = JobApplicationEvent.query.filter_by(application_id=row.id).one()
        assert ev.source == "assistant"
        # the tool call went out on this request
        assert SCRIPTED_BACKEND.calls[0]["tools"], "tools should be offered here"
        q = AssistantQuery.query.order_by(AssistantQuery.id.desc()).first()
        assert q.is_admin is True


def test_response_shape_unchanged_on_the_tool_path(owner_unlocked_client):
    SCRIPTED_BACKEND.push({"text": "just chatting, no tools this turn"})
    resp = owner_unlocked_client.post(
        "/api/assistant/chat", json={"message": "hi Hera", "history": []}
    )
    body = json.loads(resp.data)
    assert set(body) == {"reply", "sources", "backend", "error"}
    assert isinstance(body["sources"], list)


# --------------------------------------------------------------------------
# prompt: no drift when tools are off
# --------------------------------------------------------------------------

def test_build_messages_identical_when_job_tools_false():
    common = dict(question="what does Nelson know about Go?",
                  context_text="[1] bio\nNelson writes Go.", history=[])
    assert prompts.build_messages(**common) == prompts.build_messages(**common, job_tools=False)
    assert prompts.build_messages(**common, is_admin=True) == prompts.build_messages(
        **common, is_admin=True, job_tools=False
    )


def test_build_messages_adds_the_tool_block_when_on():
    msgs = prompts.build_messages(
        question="x", context_text="[1] y", history=[], is_admin=True, job_tools=True
    )
    sys = msgs[0]["content"]
    assert "add_application" in sys and "set_application_status" in sys
    assert "no delete tool" in sys
    assert "confirm" in sys.lower()
