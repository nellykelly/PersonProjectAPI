from app.extensions import db
from app.models import Character
from app.services import world_cache


def test_get_live_world_only_returns_live_characters(app, db):
    db.session.add_all(
        [
            Character(
                session_id="s1",
                first_name="A",
                last_name="One",
                appearance_id="sky",
                head_type_id="round_tan",
                body_type_id="regular",
                hand_type_id="bare",
                status="live",
            ),
            Character(
                session_id="s2",
                first_name="B",
                last_name="Two",
                appearance_id="rose",
                head_type_id="round_tan",
                body_type_id="regular",
                hand_type_id="bare",
                status="pending",
            ),
        ]
    )
    db.session.commit()

    world = world_cache.get_live_world()
    names = [c["first_name"] for c in world]
    assert names == ["A"]


def test_cache_reflects_new_character_only_after_invalidation(app, db):
    world_cache.get_live_world()  # populate cache with an empty world

    db.session.add(
        Character(
            session_id="s1",
            first_name="A",
            last_name="One",
            appearance_id="sky",
            head_type_id="round_tan",
            body_type_id="regular",
            hand_type_id="bare",
            status="live",
        )
    )
    db.session.commit()

    stale = world_cache.get_live_world()
    assert stale == []  # cache still holds the old empty snapshot

    world_cache.invalidate_world_cache()
    fresh = world_cache.get_live_world()
    assert len(fresh) == 1
    assert fresh[0]["first_name"] == "A"
