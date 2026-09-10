"""The Hera chat layer: tool dispatch, the bounded loop, the HTTP boundary.

Deterministic turns come from the shared `SCRIPTED_BACKEND` (kind
"scripted"); `FAMILY_LLM_BACKEND` selects it. The autouse fixture resets
its queue.
"""
import json
import subprocess
import sys

import pytest
from werkzeug.security import generate_password_hash

from app import models
from app.extensions import db
from app.services.assistant.backends import SCRIPTED_BACKEND
from app.services.family import chat as chat_svc
from app.services.family.tools import build_family_tools, dispatch_family_tool


@pytest.fixture(autouse=True)
def _reset_scripted():
    SCRIPTED_BACKEND.reset()
    yield
    SCRIPTED_BACKEND.reset()


@pytest.fixture()
def members(app):
    with app.app_context():
        db.create_all()
        n = models.FamilyMember(slug="m1", name="Nelson", accent="leaf")
        s = models.FamilyMember(slug="m2", name="Savannah", accent="bloom")
        db.session.add_all([n, s])
        db.session.commit()
        yield n, s


@pytest.fixture()
def family_client(app, client, members):
    app.config["FAMILY_PASSWORD_HASH"] = generate_password_hash("pw")
    app.config["FAMILY_LLM_BACKEND"] = "scripted"
    client.post("/family/unlock", data={"password": "pw"})
    yield client
    app.config["FAMILY_PASSWORD_HASH"] = None


# ---------- tools ----------

def test_build_family_tools_full_set():
    names = {t["function"]["name"] for t in build_family_tools()}
    assert names == {
        "add_event", "list_events", "update_event", "delete_event",
        "add_grocery_item", "add_grocery_items", "list_grocery",
        "toggle_grocery_item", "remove_grocery_item", "start_new_grocery_order",
        "archive_grocery_order", "copy_grocery_order_forward",
        "remember", "forget", "list_memories",
    }


def test_dispatch_add_grocery_items_batch(members):
    n, _ = members
    out = dispatch_family_tool(
        "add_grocery_items",
        {"items": [
            {"name": "milk", "quantity": "1/2 gal"},
            {"name": "eggs", "quantity": "12 ct"},
            {"name": "  "},          # skipped
            {"name": "butter"},
        ]},
        member=n,
    )
    assert "Added 3 item(s)" in out and "1 skipped" in out
    names = sorted(i.name for i in models.FamilyGroceryItem.query.all())
    assert names == ["butter", "eggs", "milk"]


def test_dispatch_archive_grocery_order(members):
    n, _ = members
    dispatch_family_tool("add_grocery_item", {"name": "milk"}, member=n)
    out = dispatch_family_tool("archive_grocery_order", {}, member=n)
    assert "saved to history" in out
    assert models.FamilyGroceryOrder.query.filter_by(status="open").count() == 0
    assert models.FamilyGroceryOrder.query.filter_by(status="archived").count() == 1


@pytest.mark.parametrize("name, args", [
    ("frobnicate", {}),
    ("list_events", "{bad json"),
    ("delete_event", {}),                 # missing event_id -> KeyError
    ("delete_event", {"event_id": 999}),  # FamilyError, no such event
    ("add_event", {"title": "x"}),        # missing date -> FamilyError
])
def test_dispatch_family_tool_never_raises(members, name, args):
    n, _ = members
    out = dispatch_family_tool(name, args, member=n)
    assert isinstance(out, str)


def test_dispatch_add_event_writes_attributed(members):
    n, s = members
    out = dispatch_family_tool(
        "add_event", {"title": "Dentist", "date": "2026-09-20", "time": "09:30"}, member=s
    )
    assert "Dentist" in out
    ev = models.FamilyCalendarEvent.query.one()
    assert ev.created_by_id == s.id


# ---------- the loop ----------

def test_scripted_tool_turn_then_narration(app, members):
    n, _ = members
    app.config["FAMILY_LLM_BACKEND"] = "scripted"
    SCRIPTED_BACKEND.push(
        {"tool_calls": [{"id": "c1", "name": "add_grocery_item",
                         "arguments": json.dumps({"name": "coffee"})}]},
        {"text": "On the list."},
    )
    r = chat_svc.answer(n, "add coffee", config=app.config)
    assert r.reply == "On the list."
    assert models.FamilyGroceryItem.query.filter_by(name="coffee").count() == 1
    # both turns persisted
    assert models.FamilyChatMessage.query.filter_by(member_id=n.id).count() == 2


def test_rejects_empty_and_overlong(app, members):
    n, _ = members
    app.config["FAMILY_LLM_BACKEND"] = "scripted"
    with pytest.raises(chat_svc.FamilyChatInputError):
        chat_svc.answer(n, "   ", config=app.config)
    with pytest.raises(chat_svc.FamilyChatInputError):
        chat_svc.answer(n, "x" * 9000, config=app.config)


def test_offline_raises_unavailable(app, members):
    n, _ = members
    app.config["FAMILY_LLM_BACKEND"] = "groq"
    app.config["FAMILY_GROQ_API_KEY"] = ""
    with pytest.raises(chat_svc.FamilyChatUnavailable):
        chat_svc.answer(n, "hi", config=app.config)


# ---------- the HTTP boundary ----------

def test_api_chat_happy_path(family_client):
    SCRIPTED_BACKEND.push({"text": "Hi Nelson."})
    r = family_client.post("/family/api/chat", json={"member_id": "m1", "message": "hey"})
    assert r.status_code == 200
    body = r.get_json()
    assert body == {"reply": "Hi Nelson.", "error": False}


def test_api_chat_bad_member(family_client):
    r = family_client.post("/family/api/chat", json={"member_id": "zzz", "message": "hi"})
    assert r.status_code == 400 and r.get_json()["error"] is True


def test_api_chat_empty_message(family_client):
    r = family_client.post("/family/api/chat", json={"member_id": "m1", "message": " "})
    assert r.status_code == 400


def test_api_chat_fails_soft_offline(family_client, app):
    app.config["FAMILY_LLM_BACKEND"] = "groq"
    app.config["FAMILY_GROQ_API_KEY"] = ""
    r = family_client.post("/family/api/chat", json={"member_id": "m1", "message": "hi"})
    assert r.status_code == 503
    assert r.get_json()["error"] is True
    assert b"Traceback" not in r.data


def test_api_chat_requires_unlock(app, client, members):
    app.config["FAMILY_LLM_BACKEND"] = "scripted"
    app.config["FAMILY_PASSWORD_HASH"] = generate_password_hash("pw")  # configured...
    try:
        r = client.post("/family/api/chat", json={"member_id": "m1", "message": "hi"})
        assert r.status_code == 302   # ...but not unlocked -> bounced to the gate
    finally:
        app.config["FAMILY_PASSWORD_HASH"] = None


def test_threads_do_not_bleed_between_members(family_client):
    SCRIPTED_BACKEND.push({"text": "one"}, {"text": "two"})
    family_client.post("/family/api/chat", json={"member_id": "m1", "message": "nelson only"})
    family_client.post("/family/api/chat", json={"member_id": "m2", "message": "sav only"})
    m1_thread = family_client.get("/family/chat?member=m1").data
    m2_thread = family_client.get("/family/chat?member=m2").data
    assert b"nelson only" in m1_thread and b"nelson only" not in m2_thread
    assert b"sav only" in m2_thread and b"sav only" not in m1_thread


# ---------- assistant loop still works ----------

def test_assistant_tool_loop_unchanged():
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_assistant_job_tools.py::test_authorized_loop_writes_and_sums_tokens"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout[-1500:]


# ---------- no copyrighted corpus / secret in the repo ----------

def test_no_corpus_or_secret_in_tracked_files():
    """Two real things must never enter a tracked file: the extracted
    copyrighted dialogue corpus (`hera-lines.md`, staging root) and the
    runtime Groq key.

    The corpus is guarded two ways -- neither of which is the literal
    string "hera-lines", because that appears legitimately as prose in the
    README, the build spec, and this test's own source (all describing the
    guard). Checking for it there would make the test flag the
    documentation of itself. Instead:
      1. no file *named* like the corpus is tracked;
      2. if the real corpus is readable from the staging root, a
         distinctive verbatim phrase from it appears in no tracked file --
         this is what catches someone pasting dialogue into persona.py.
         Skipped (not failed) when the staging-root file isn't reachable,
         same as the key check below.

    The key is read from the environment at run time (same source as
    app/config.py); writing it as a literal here would make this test the
    leak it exists to catch.
    """
    import os
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=repo_root
    ).stdout.split()

    CORPUS_BASENAMES = {"hera-lines.md", "personality_hera_canon.md"}
    corpus_tracked = [p for p in tracked if Path(p).name in CORPUS_BASENAMES]
    assert not corpus_tracked, f"corpus file(s) are tracked: {corpus_tracked}"

    banned: list[str] = []
    key = os.environ.get("FAMILY_GROQ_API_KEY")
    if key:
        banned.append(key)

    # A verbatim phrase from the real corpus, if it's on disk outside the
    # repo. Take a long-ish line so an incidental collision is implausible.
    corpus_path = repo_root.parent / "hera-lines.md"
    if corpus_path.is_file():
        for line in corpus_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            phrase = line.strip()
            if len(phrase) >= 60:
                banned.append(phrase)
                break

    if not banned:
        pytest.skip("neither the Groq key nor the staging-root corpus is available to check against")

    hits = []
    for path in tracked:
        if path.endswith((".png", ".jpg", ".ico", ".pdf", ".woff2", ".svg")):
            continue
        try:
            text = (repo_root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in banned:
            if needle in text:
                label = "GROQ KEY" if needle == key else "corpus phrase"
                hits.append(f"{path}: {label}")
    assert not hits, hits
