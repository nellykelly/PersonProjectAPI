"""widen characters.icebreaker_answer_* to varchar(120) for raw storage

Join submissions are now persisted before any content check runs -- the
pipeline's own Sanitize stage is what rejects bad input, and a stage
needs a row to run against (see validators.prepare_join_submission).
That means these columns hold unvalidated visitor text, so each one has
to be wider than the 80-character limit sanitize_icebreaker_answer
enforces: at exactly 80 an over-long answer would be truncated down into
a *passing* value on the way in, and Sanitize would approve something
the visitor never actually submitted.

Widening only -- no backfill, no constraint tightening, so this is safe
against a populated table (unlike the add-a-NOT-NULL-column migrations
elsewhere in this directory).

Revision ID: c1d4e7a9b230
Revises: d153b8773f7a
Create Date: 2026-08-23 09:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d4e7a9b230'
down_revision = 'd153b8773f7a'
branch_labels = None
depends_on = None

_ANSWER_COLUMNS = (
    'icebreaker_answer_food',
    'icebreaker_answer_movie',
    'icebreaker_answer_hobby',
    'icebreaker_answer_weekend',
)


def upgrade():
    with op.batch_alter_table('characters', schema=None) as batch_op:
        for column in _ANSWER_COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.VARCHAR(length=80),
                type_=sa.String(length=120),
                existing_nullable=True,
            )


def downgrade():
    # Narrowing back would truncate any stored answer longer than 80 --
    # only reachable for a submission that already failed Sanitize, but
    # Postgres refuses the ALTER outright rather than silently cutting,
    # so trim first and then narrow.
    for column in _ANSWER_COLUMNS:
        op.execute(
            f"UPDATE characters SET {column} = LEFT({column}, 80) "
            f"WHERE {column} IS NOT NULL AND LENGTH({column}) > 80"
        )
    with op.batch_alter_table('characters', schema=None) as batch_op:
        for column in _ANSWER_COLUMNS:
            batch_op.alter_column(
                column,
                existing_type=sa.String(length=120),
                type_=sa.VARCHAR(length=80),
                existing_nullable=True,
            )
