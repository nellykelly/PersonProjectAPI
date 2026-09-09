"""app/services/family/memory.py -- shared, newest-first, capped."""
import pytest

from app import models
from app.extensions import db
from app.services.family import FamilyError
from app.services.family import memory as mem


@pytest.fixture()
def member(app):
    with app.app_context():
        db.create_all()
        m = models.FamilyMember(slug="m1", name="Nelson", accent="leaf")
        db.session.add(m)
        db.session.commit()
        yield m


def test_save_list_forget(member):
    mem.save("bin day is Tuesday", member=member)
    mem.save("Sav likes oat milk")            # Hera-authored
    rows = mem.list_all()
    assert len(rows) == 2
    assert rows[0].content == "Sav likes oat milk"        # newest first
    assert rows[0].created_by_id is None                  # bot-authored
    assert rows[1].created_by_id == member.id
    mem.forget(rows[0].id)
    assert len(mem.list_all()) == 1


def test_save_rejects_empty(member):
    with pytest.raises(FamilyError):
        mem.save("   ", member=member)


def test_forget_missing_raises(member):
    with pytest.raises(FamilyError):
        mem.forget(999)


def test_list_all_respects_limit(member):
    for i in range(5):
        mem.save(f"fact {i}", member=member)
    assert len(mem.list_all(limit=2)) == 2


def test_for_prompt_format_and_cap(app, member):
    for i in range(4):
        mem.save(f"fact {i}", member=member)
    with app.app_context():
        app.config["FAMILY_MEMORY_LIMIT"] = 2
        block = mem.for_prompt()
    assert block.count("\n") == 1                          # 2 lines
    assert block.startswith("- fact 3")                    # newest first
    with app.app_context():
        for row in mem.list_all():
            mem.forget(row.id)
        assert mem.for_prompt() == ""
