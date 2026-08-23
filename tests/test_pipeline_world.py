from app.extensions import db as db_ext
from app.models import Character, PipelineRun

ALL_STAGES = ["sanitize", "security_scan", "test_uniqueness", "test_profanity", "build", "deploy", "verify"]

VALID_JOIN_PAYLOAD = {
    "first_name": "Nelson",
    "last_name": "Koskela",
    "appearance_id": "sky",
    "head_type_id": "round_tan",
    "body_type_id": "regular",
    "hand_type_id": "bare",
    "icebreaker_answer_food": "Tacos",
    "icebreaker_answer_movie": "Inception",
    "icebreaker_answer_hobby": "Reading",
    "icebreaker_answer_weekend": "Hiking",
}


def _payload(**overrides):
    data = dict(VALID_JOIN_PAYLOAD)
    data.update(overrides)
    return data


def test_index_loads(client):
    resp = client.get("/projects/pipeline-world")
    assert resp.status_code == 200
    assert b"Pipeline World" in resp.data


def test_index_shows_the_seven_stage_columns(client):
    resp = client.get("/projects/pipeline-world")
    for label in ("Sanitize", "Security Scan", "Uniqueness", "Profanity", "Build", "Deploy", "Verify"):
        assert label.encode() in resp.data


def test_index_shows_all_four_fixed_icebreaker_questions(client):
    resp = client.get("/projects/pipeline-world")
    for text in ("favorite food", "favorite movie", "hobby you enjoy", "ideal weekend"):
        assert text.encode() in resp.data


def test_town_loads(client):
    resp = client.get("/projects/pipeline-world/town")
    assert resp.status_code == 200
    assert b"Production Town" in resp.data


def test_town_links_back_to_pipeline_world(client):
    resp = client.get("/projects/pipeline-world/town")
    assert b"/projects/pipeline-world" in resp.data


def test_index_links_to_town(client):
    resp = client.get("/projects/pipeline-world")
    assert b"/projects/pipeline-world/town" in resp.data


def test_api_recent_runs_reflects_a_completed_join(client, db):
    client.post("/projects/pipeline-world/join", data=_payload())
    resp = client.get("/projects/pipeline-world/api/recent-runs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    row = data["rows"][0]
    assert row["character"]["first_name"] == "Nelson"
    assert set(row["stages"].keys()) == set(ALL_STAGES)
    for stage in ALL_STAGES:
        assert row["stages"][stage]["status"] == "pass"
        assert row["stages"][stage]["duration_seconds"] >= 0


def test_pipeline_analytics_loads_and_reports_postgres_required(client):
    resp = client.get("/projects/pipeline-world/pipeline-analytics")
    assert resp.status_code == 200
    assert b"Postgres" in resp.data


def test_join_success_runs_synchronously_to_live(client, db):
    resp = client.post("/projects/pipeline-world/join", data=_payload())
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["character"]["status"] == "live"

    texts = {ib["text"] for ib in data["character"]["icebreakers"]}
    assert "Favorite food: Tacos" in texts
    assert "Favorite movie: Inception" in texts
    assert "Hobby: Reading" in texts
    assert "Ideal weekend: Hiking" in texts

    character = db_ext.session.get(Character, data["character"]["id"])
    assert character.status == "live"
    assert character.world_x is not None
    assert character.head_type_id == "round_tan"
    assert character.body_type_id == "regular"
    assert character.hand_type_id == "bare"
    assert character.icebreaker_answer_food == "Tacos"
    assert character.icebreaker_answer_movie == "Inception"
    assert character.icebreaker_answer_hobby == "Reading"
    assert character.icebreaker_answer_weekend == "Hiking"


def test_join_rejects_invalid_appearance(client):
    resp = client.post("/projects/pipeline-world/join", data=_payload(appearance_id="not-real"))
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_join_rejects_invalid_head_type(client):
    resp = client.post("/projects/pipeline-world/join", data=_payload(head_type_id="not-real"))
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_join_rejects_invalid_body_type(client):
    resp = client.post("/projects/pipeline-world/join", data=_payload(body_type_id="not-real"))
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_join_rejects_invalid_hand_type(client):
    resp = client.post("/projects/pipeline-world/join", data=_payload(hand_type_id="not-real"))
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_join_rejects_missing_icebreaker_answer(client):
    resp = client.post("/projects/pipeline-world/join", data=_payload(icebreaker_answer_hobby=""))
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_join_rejects_injection_in_icebreaker_answer(client):
    resp = client.post(
        "/projects/pipeline-world/join",
        data=_payload(icebreaker_answer_movie="<script>alert(1)</script>"),
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_join_last_name_collision_requires_confirmation(client, db):
    client.post("/projects/pipeline-world/join", data=_payload(first_name="Alice"))

    resp = client.post("/projects/pipeline-world/join", data=_payload(first_name="Nelson", appearance_id="rose"))
    data = resp.get_json()
    assert data["ok"] is False
    assert data["needs_confirmation"] is True
    assert "Koskela" in data["message"]

    # not created yet
    assert Character.query.filter_by(first_name="Nelson", last_name="Koskela").first() is None


def test_join_confirm_flag_bypasses_last_name_collision(client, db):
    client.post("/projects/pipeline-world/join", data=_payload(first_name="Alice"))

    resp = client.post(
        "/projects/pipeline-world/join",
        data=_payload(first_name="Nelson", appearance_id="rose", confirm_last_name_collision="1"),
    )
    data = resp.get_json()
    assert data["ok"] is True
    assert data["character"]["status"] == "live"


def test_join_accepts_a_full_name_collision_but_it_fails_later_at_test_uniqueness(client, db):
    # Uniqueness is no longer checked at submission time -- only by the
    # pipeline's own Test:Uniqueness stage, once the job actually runs
    # (see validators.validate_join_request's docstring). The join
    # request itself is accepted; the *pipeline run* is what fails.
    client.post("/projects/pipeline-world/join", data=_payload())

    resp = client.post(
        "/projects/pipeline-world/join",
        data=_payload(appearance_id="rose", confirm_last_name_collision="1"),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["character"]["status"] == "failed"

    character = db_ext.session.get(Character, data["character"]["id"])
    assert "already exists" in character.failure_reason
    runs = PipelineRun.query.filter_by(character_id=character.id).order_by(PipelineRun.started_at).all()
    assert runs[-1].stage == "test_uniqueness"
    assert runs[-1].status == "fail"


def test_api_character_status(client, db):
    resp = client.post("/projects/pipeline-world/join", data=_payload())
    character_id = resp.get_json()["character"]["id"]

    status_resp = client.get(f"/projects/pipeline-world/api/character/{character_id}")
    assert status_resp.status_code == 200
    assert status_resp.get_json()["character"]["status"] == "live"


def test_api_world_lists_live_characters(client, db):
    client.post("/projects/pipeline-world/join", data=_payload())
    resp = client.get("/projects/pipeline-world/api/world")
    data = resp.get_json()
    assert data["ok"] is True
    assert any(c["first_name"] == "Nelson" for c in data["characters"])
