from app.extensions import db
from app.models import Character
from app.services import queue


def test_testing_config_runs_jobs_synchronously(app):
    # TestingConfig sets PIPELINE_STAGE_DELAY_SECONDS = 0 and queue.py
    # makes RQ execute jobs inline (is_async=False) under TESTING --
    # by the time enqueue_character_join() returns, the job already ran.
    with app.app_context():
        assert queue.get_queue().is_async is False


def test_enqueue_character_join_runs_the_real_pipeline_synchronously(app, db):
    character = Character(
        session_id="s1",
        first_name="Nelson",
        last_name="Koskela",
        appearance_id="sky",
        head_type_id="round_tan",
        body_type_id="regular",
        hand_type_id="bare",
        icebreaker_answer_food="Tacos",
        icebreaker_answer_movie="Inception",
        icebreaker_answer_hobby="Reading",
        icebreaker_answer_weekend="Hiking",
    )
    db.session.add(character)
    db.session.commit()

    queue.enqueue_character_join(character.id)

    db.session.refresh(character)
    assert character.status == "live"


def test_redis_connection_is_fakeredis_when_no_redis_url_configured(app):
    with app.app_context():
        conn = queue.get_redis_connection()
        assert type(conn).__module__.startswith("fakeredis")
