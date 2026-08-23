import pytest

from app.models import Character
from app.services import validators
from app.services.validators import LastNameCollision, ValidationError

ANSWERS = {"food": "Tacos", "movie": "Inception", "hobby": "Reading", "weekend": "Hiking"}


def _answers(**overrides):
    data = dict(ANSWERS)
    data.update(overrides)
    return data


# ---------- sanitize_name_part ----------


def test_sanitize_name_part_accepts_a_normal_name():
    assert validators.sanitize_name_part("Nelson", "First name") == "Nelson"


def test_sanitize_name_part_strips_surrounding_whitespace():
    assert validators.sanitize_name_part("  Nelson  ", "First name") == "Nelson"


def test_sanitize_name_part_allows_hyphen_and_apostrophe():
    assert validators.sanitize_name_part("Smith-Jones", "Last name") == "Smith-Jones"
    assert validators.sanitize_name_part("O'Brien", "Last name") == "O'Brien"


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_sanitize_name_part_rejects_missing_or_blank(bad):
    with pytest.raises(ValidationError, match="required"):
        validators.sanitize_name_part(bad, "First name")


def test_sanitize_name_part_rejects_too_long():
    with pytest.raises(ValidationError, match="30 characters"):
        validators.sanitize_name_part("A" * 31, "First name")


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "Robert'); DROP TABLE characters;--",
        "Nelson123",
        "Nelson;",
        "Nel$on",
    ],
)
def test_sanitize_name_part_rejects_html_sql_and_symbols(bad):
    with pytest.raises(ValidationError):
        validators.sanitize_name_part(bad, "First name")


def test_sanitize_name_part_does_not_itself_reject_blocked_words():
    # profanity is check_no_profanity's job, not sanitize_name_part's --
    # sanitize_name_part only enforces format/hygiene.
    assert validators.sanitize_name_part("damn", "First name") == "damn"


# ---------- check_no_profanity ----------


def test_check_no_profanity_accepts_a_clean_name():
    validators.check_no_profanity("Nelson", "First name")  # should not raise


def test_check_no_profanity_rejects_blocked_words():
    with pytest.raises(ValidationError, match="disallowed"):
        validators.check_no_profanity("damn", "First name")


def test_check_no_profanity_works_on_a_sentence_not_just_a_single_token():
    with pytest.raises(ValidationError, match="disallowed"):
        validators.check_no_profanity("what the damn point is", "Icebreaker answer")


# ---------- check_no_injection_patterns (Security Scan stage) ----------


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "<img src=x>",
        "</textarea>",
        "Robert'); DROP TABLE characters;--",
        "1; --",
        "UNION SELECT password FROM users",
        "SELECT secret FROM accounts",
    ],
)
def test_check_no_injection_patterns_rejects_known_patterns(bad):
    with pytest.raises(ValidationError, match="disallowed pattern"):
        validators.check_no_injection_patterns(bad, "Icebreaker answer")


def test_check_no_injection_patterns_accepts_normal_text():
    validators.check_no_injection_patterns("Tacos and a good book by the beach", "Icebreaker answer")  # should not raise


def test_check_no_injection_patterns_accepts_empty_or_none():
    validators.check_no_injection_patterns("", "Icebreaker answer")
    validators.check_no_injection_patterns(None, "Icebreaker answer")


# ---------- validate_appearance_id ----------


def test_validate_appearance_id_accepts_known_option():
    assert validators.validate_appearance_id("crimson") == "crimson"


def test_validate_appearance_id_rejects_unknown_option():
    with pytest.raises(ValidationError):
        validators.validate_appearance_id("not-a-real-appearance")


def test_validate_appearance_id_rejects_none():
    with pytest.raises(ValidationError):
        validators.validate_appearance_id(None)


# ---------- the 4 fixed icebreaker questions ----------


def test_exactly_four_fixed_icebreaker_questions_exist():
    assert len(validators.FIXED_ICEBREAKER_QUESTIONS) == 4


def test_fixed_icebreaker_questions_have_unique_ids_and_field_names():
    ids = [q["id"] for q in validators.FIXED_ICEBREAKER_QUESTIONS]
    field_names = [q["field_name"] for q in validators.FIXED_ICEBREAKER_QUESTIONS]
    assert len(set(ids)) == len(ids)
    assert len(set(field_names)) == len(field_names)


# ---------- sanitize_icebreaker_answer ----------


def test_sanitize_icebreaker_answer_accepts_a_normal_sentence():
    assert validators.sanitize_icebreaker_answer("Tacos, always.") == "Tacos, always."


def test_sanitize_icebreaker_answer_strips_surrounding_whitespace():
    assert validators.sanitize_icebreaker_answer("  Pizza  ") == "Pizza"


@pytest.mark.parametrize("bad", [None, "", "   "])
def test_sanitize_icebreaker_answer_rejects_missing_or_blank(bad):
    with pytest.raises(ValidationError, match="required"):
        validators.sanitize_icebreaker_answer(bad)


def test_sanitize_icebreaker_answer_rejects_too_long():
    with pytest.raises(ValidationError, match="80 characters"):
        validators.sanitize_icebreaker_answer("A" * 81)


@pytest.mark.parametrize(
    "bad",
    [
        "<script>alert(1)</script>",
        "Robert'); DROP TABLE characters;--",
        "Tacos <b>always</b>",
        'Tacos "always"',
        "Tacos; DROP TABLE",
    ],
)
def test_sanitize_icebreaker_answer_rejects_disallowed_characters(bad):
    with pytest.raises(ValidationError, match="letters, numbers"):
        validators.sanitize_icebreaker_answer(bad)


def test_sanitize_icebreaker_answer_allows_basic_punctuation():
    assert validators.sanitize_icebreaker_answer("Coffee & a good book (weekends only) - always!") == (
        "Coffee & a good book (weekends only) - always!"
    )


# ---------- sanitize_icebreaker_answers (all 4 at once) ----------


def test_sanitize_icebreaker_answers_accepts_a_complete_set():
    cleaned = validators.sanitize_icebreaker_answers(ANSWERS)
    assert cleaned == ANSWERS


def test_sanitize_icebreaker_answers_requires_every_fixed_question():
    incomplete = _answers(hobby="")
    with pytest.raises(ValidationError, match="required"):
        validators.sanitize_icebreaker_answers(incomplete)


def test_sanitize_icebreaker_answers_rejects_bad_charset_in_any_answer():
    with pytest.raises(ValidationError):
        validators.sanitize_icebreaker_answers(_answers(movie="<script>alert(1)</script>"))


# ---------- collision checks (need the DB) ----------


def _make_character(db, first, last, status="live"):
    character = Character(
        session_id="test-session",
        first_name=first,
        last_name=last,
        appearance_id="sky",
        head_type_id="round_tan",
        body_type_id="regular",
        hand_type_id="bare",
        status=status,
    )
    db.session.add(character)
    db.session.commit()
    return character


def test_full_name_collision_is_case_insensitive_and_blocks(app, db):
    with app.app_context():
        _make_character(db, "Nelson", "Koskela")
        with pytest.raises(ValidationError, match="already exists"):
            validators.check_full_name_collision("nelson", "KOSKELA")


def test_full_name_collision_ignores_failed_characters(app, db):
    with app.app_context():
        _make_character(db, "Nelson", "Koskela", status="failed")
        validators.check_full_name_collision("Nelson", "Koskela")  # should not raise


def test_no_full_name_collision_for_different_names(app, db):
    with app.app_context():
        _make_character(db, "Nelson", "Koskela")
        validators.check_full_name_collision("Nelson", "Smith")  # should not raise


def test_full_name_collision_excludes_the_given_character_id(app, db):
    with app.app_context():
        character = _make_character(db, "Nelson", "Koskela")
        # Without exclude_character_id, a persisted character always
        # "collides" with itself.
        validators.check_full_name_collision("Nelson", "Koskela", exclude_character_id=character.id)


def test_last_name_collision_found(app, db):
    with app.app_context():
        _make_character(db, "Alice", "Koskela")
        found = validators.find_last_name_collision("koskela")
        assert found is not None
        assert found.first_name == "Alice"


def test_last_name_collision_none_when_no_match(app, db):
    with app.app_context():
        assert validators.find_last_name_collision("Nobody") is None


# ---------- validate_head_type_id / validate_body_type_id / validate_hand_type_id ----------


def test_validate_head_type_id_accepts_known_option():
    assert validators.validate_head_type_id("round_tan") == "round_tan"


def test_validate_head_type_id_rejects_unknown_option():
    with pytest.raises(ValidationError):
        validators.validate_head_type_id("not-a-real-head")


def test_validate_body_type_id_accepts_known_option():
    assert validators.validate_body_type_id("regular") == "regular"


def test_validate_body_type_id_rejects_unknown_option():
    with pytest.raises(ValidationError):
        validators.validate_body_type_id("not-a-real-body")


def test_validate_hand_type_id_accepts_known_option():
    assert validators.validate_hand_type_id("bare") == "bare"


def test_validate_hand_type_id_rejects_unknown_option():
    with pytest.raises(ValidationError):
        validators.validate_hand_type_id("not-a-real-hand")


# ---------- truncate_for_storage ----------


def test_truncate_for_storage_strips_and_caps():
    assert validators.truncate_for_storage("  Nelson  ", 40) == "Nelson"
    assert validators.truncate_for_storage("x" * 200, 40) == "x" * 40


def test_truncate_for_storage_turns_none_into_an_empty_string():
    # Not None: the columns are NOT NULL, and a missing field has to reach
    # the Sanitize stage as something that stage can fail on, rather than
    # blowing up the INSERT before any pipeline run exists.
    assert validators.truncate_for_storage(None, 40) == ""


def test_truncate_for_storage_never_makes_an_over_long_value_valid():
    # The reason the raw caps sit above the validated limits: a 500-char
    # name truncates to 40, which sanitize_name_part still rejects for
    # being over 30. If the cap were 30 this would silently pass.
    stored = validators.truncate_for_storage("N" * 500, validators.MAX_RAW_NAME_PART_LENGTH)
    with pytest.raises(ValidationError):
        validators.sanitize_name_part(stored, "First name")


# ---------- prepare_join_submission (storage only, not validation) ----------

DEFAULT_TYPES = ("round_tan", "regular", "bare")


def test_prepare_join_submission_success_with_no_collisions(app, db):
    with app.app_context():
        result = validators.prepare_join_submission("Nelson", "Koskela", "sky", *DEFAULT_TYPES, ANSWERS)
        assert result == ("Nelson", "Koskela", "sky", "round_tan", "regular", "bare", ANSWERS)


def test_prepare_join_submission_raises_last_name_collision_by_default(app, db):
    with app.app_context():
        _make_character(db, "Alice", "Koskela")
        with pytest.raises(LastNameCollision):
            validators.prepare_join_submission("Nelson", "Koskela", "sky", *DEFAULT_TYPES, ANSWERS)


def test_prepare_join_submission_confirm_bypasses_last_name_collision(app, db):
    with app.app_context():
        _make_character(db, "Alice", "Koskela")
        result = validators.prepare_join_submission(
            "Nelson", "Koskela", "sky", *DEFAULT_TYPES, ANSWERS, confirm_last_name_collision=True
        )
        assert result == ("Nelson", "Koskela", "sky", "round_tan", "regular", "bare", ANSWERS)


def test_prepare_join_submission_does_not_block_a_full_name_collision(app, db):
    # Deliberate: uniqueness is checked later, by the pipeline's own
    # Test:Uniqueness stage (see test_pipeline.py), not at submission
    # time -- two visitors submitting the same name should both be
    # accepted here and race through the pipeline instead.
    with app.app_context():
        _make_character(db, "Nelson", "Koskela")
        result = validators.prepare_join_submission(
            "Nelson", "Koskela", "sky", *DEFAULT_TYPES, ANSWERS, confirm_last_name_collision=True
        )
        assert result == ("Nelson", "Koskela", "sky", "round_tan", "regular", "bare", ANSWERS)


# Every one of the following used to raise here, at submission time, which
# meant the pipeline never ran at all for a bad submission. They now pass
# straight through to storage on purpose -- the stage that owns each rule
# is what fails, visibly, once the run starts. The matching
# "...and then fails at <stage>" tests live in test_pipeline_world.py.


def test_prepare_join_submission_passes_injection_through_to_the_pipeline(app, db):
    with app.app_context():
        result = validators.prepare_join_submission(
            "Nelson", "Koskela", "sky", *DEFAULT_TYPES, _answers(movie="<script>alert(1)</script>")
        )
        assert result[6]["movie"] == "<script>alert(1)</script>"


def test_prepare_join_submission_passes_a_missing_answer_through_to_the_pipeline(app, db):
    with app.app_context():
        result = validators.prepare_join_submission(
            "Nelson", "Koskela", "sky", *DEFAULT_TYPES, _answers(food="")
        )
        assert result[6]["food"] == ""


def test_prepare_join_submission_passes_profanity_through_to_the_pipeline(app, db):
    with app.app_context():
        result = validators.prepare_join_submission(
            "Nelson", "Koskela", "sky", *DEFAULT_TYPES, _answers(food="damn good tacos")
        )
        assert result[6]["food"] == "damn good tacos"


def test_prepare_join_submission_passes_an_invalid_head_type_through_to_the_pipeline(app, db):
    with app.app_context():
        result = validators.prepare_join_submission(
            "Nelson", "Koskela", "sky", "not-real", "regular", "bare", ANSWERS
        )
        assert result[3] == "not-real"


def test_prepare_join_submission_truncates_to_storage_limits(app, db):
    with app.app_context():
        result = validators.prepare_join_submission(
            "N" * 500, "K" * 500, "sky", *DEFAULT_TYPES, _answers(food="f" * 500)
        )
        assert result[0] == "N" * validators.MAX_RAW_NAME_PART_LENGTH
        assert result[1] == "K" * validators.MAX_RAW_NAME_PART_LENGTH
        assert result[6]["food"] == "f" * validators.MAX_RAW_ICEBREAKER_ANSWER_LENGTH
