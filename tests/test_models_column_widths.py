"""Guards every VARCHAR column against the values the app can actually write.

This exists because the test suite runs on SQLite, which ignores VARCHAR
length limits completely, while production runs on Postgres, which
enforces them. A column declared too narrow is therefore invisible to
every other test in this suite and only shows up as a 500 in production:

  * PipelineRun.stage was String(10) and 'test_uniqueness' is 15 chars,
    which broke the whole pipeline mid-run.
  * Instrument.instrument_type was String(4) and 'stock' is 5, which
    broke opening any stock position.

Both got through a green test run. Rather than keep discovering these one
outage at a time, this checks the declared width of every string column
against the widest value the code can put in it.

Nothing here touches a database -- it's the model metadata against the
constants the app writes from -- so it catches the problem under SQLite
just as well as it would under Postgres.
"""
import pytest

from app import models
from app.services import risk_models, validators

# Columns whose values come from a fixed, known set. The list on the right
# is the full set of values the app can write, so max(len) is the real
# minimum width for that column.
ENUMERABLE_COLUMNS = [
    (models.Instrument, "instrument_type", ["stock", "call", "put"]),
    (models.Instrument, "exercise_style", ["american", "european"]),
    (models.Instrument, "settlement_type", ["physical", "cash"]),
    (models.Strategy, "status", ["open", "closed"]),
    (models.Strategy, "name", ["Single Leg"]),
    (models.Leg, "side", ["buy", "sell"]),
    (models.Leg, "status", ["open", "closed"]),
    (models.RiskRequest, "status", ["pending", "complete", "failed"]),
    (models.RiskRequest, "scope", ["leg", "position", "book"]),
    (models.Character, "status", list(models.CHARACTER_STATUSES)),
    (models.Character, "appearance_id", sorted(validators.APPEARANCE_IDS)),
    (models.Character, "head_type_id", sorted(validators.HEAD_TYPE_IDS)),
    (models.Character, "body_type_id", sorted(validators.BODY_TYPE_IDS)),
    (models.Character, "hand_type_id", sorted(validators.HAND_TYPE_IDS)),
    (models.PipelineRun, "stage", list(models.PIPELINE_STAGES)),
    (models.PipelineRun, "status", ["pass", "fail"]),
    # Every key the model registry can hand back, so registering a model
    # with a longer key than the column holds fails here rather than in
    # production. Read from the registry itself, not a copy of it.
    (models.RiskRequest, "model_key", [m.key for m in risk_models.list_models()]),
]

# Columns holding free text, bounded by a validator or a known format
# rather than an enum. The number is the longest value that can get past
# whatever guards the column.
BOUNDED_COLUMNS = [
    (models.Character, "first_name", validators.MAX_NAME_PART_LENGTH),
    (models.Character, "last_name", validators.MAX_NAME_PART_LENGTH),
    (models.Character, "icebreaker_answer_food", validators.MAX_ICEBREAKER_ANSWER_LENGTH),
    (models.Character, "icebreaker_answer_movie", validators.MAX_ICEBREAKER_ANSWER_LENGTH),
    (models.Character, "icebreaker_answer_hobby", validators.MAX_ICEBREAKER_ANSWER_LENGTH),
    (models.Character, "icebreaker_answer_weekend", validators.MAX_ICEBREAKER_ANSWER_LENGTH),
    # session_id is a str(uuid4()), which is always exactly 36 characters.
    (models.Character, "session_id", 36),
    (models.Strategy, "session_id", 36),
    (models.TimedSquaresScore, "session_id", 36),
    # OCC code worst case: a 10-char ticker (Instrument.underlying_ticker's
    # own width) + 6-digit expiry + C/P + 8-digit strike.
    (models.Instrument, "code", 10 + 6 + 1 + 8),
    # sanitize_arcade_name truncates to MAX_ARCADE_NAME_LENGTH itself, so
    # this just confirms the column can actually hold what that constant
    # promises rather than duplicating the number as a bare literal.
    (models.TimedSquaresScore, "player_name", validators.MAX_ARCADE_NAME_LENGTH),
]


def _column_length(model, column_name):
    return model.__table__.c[column_name].type.length


@pytest.mark.parametrize(
    "model, column_name, values",
    ENUMERABLE_COLUMNS,
    ids=[f"{m.__name__}.{c}" for m, c, _ in ENUMERABLE_COLUMNS],
)
def test_enumerable_column_fits_every_value_it_can_hold(model, column_name, values):
    limit = _column_length(model, column_name)
    widest = max(values, key=len)
    assert len(widest) <= limit, (
        f"{model.__name__}.{column_name} is String({limit}) but must hold "
        f"{widest!r} ({len(widest)} chars). Postgres will reject this even "
        f"though SQLite accepts it."
    )


@pytest.mark.parametrize(
    "model, column_name, max_input_length",
    BOUNDED_COLUMNS,
    ids=[f"{m.__name__}.{c}" for m, c, _ in BOUNDED_COLUMNS],
)
def test_bounded_column_fits_its_validator_limit(model, column_name, max_input_length):
    limit = _column_length(model, column_name)
    assert max_input_length <= limit, (
        f"{model.__name__}.{column_name} is String({limit}) but accepts input "
        f"up to {max_input_length} chars. Postgres will reject the longest "
        f"values even though SQLite accepts them."
    )


def test_every_string_column_is_covered_by_this_file():
    """Fails when a new String column is added without adding it above,
    so the guard can't silently fall behind the schema."""
    covered = {(m.__name__, c) for m, c, _ in ENUMERABLE_COLUMNS}
    covered |= {(m.__name__, c) for m, c, _ in BOUNDED_COLUMNS}

    # Columns whose width genuinely can't be exceeded by app input.
    exempt = {
        ("Instrument", "underlying_ticker"),  # validated against TICKER_WHITELIST
        ("PriceCache", "ticker"),  # same whitelist
    }

    uncovered = []
    for mapper in models.db.Model.registry.mappers:
        model = mapper.class_
        for column in model.__table__.c:
            if not isinstance(column.type, models.db.String):
                continue
            # db.Text subclasses String but carries no length, and Postgres
            # never truncates it -- only columns with a declared width can
            # hit the failure this file exists to catch.
            if getattr(column.type, "length", None) is None:
                continue
            key = (model.__name__, column.name)
            if key not in covered and key not in exempt:
                uncovered.append(f"{key[0]}.{key[1]}")

    assert not uncovered, (
        "New String column(s) not covered by a width check: "
        + ", ".join(sorted(uncovered))
        + ". Add them to ENUMERABLE_COLUMNS or BOUNDED_COLUMNS (or exempt them)."
    )
