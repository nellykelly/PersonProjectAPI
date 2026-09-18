"""risk_requests.leg_id nullable for book-scoped requests

Revision ID: c17d555c8581
Revises: e3b6a8d1f905
Create Date: 2026-09-15 08:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c17d555c8581'
down_revision = 'e3b6a8d1f905'
branch_labels = None
depends_on = None


def upgrade():
    # A book-scoped RiskRequest (submit_risk_request(book=True)) has always
    # been supposed to write with both strategy_id and leg_id NULL -- see
    # models.py's RiskRequest docstring, and 4a52c59eb3a2 already relaxed
    # this same column to nullable=True once. Somewhere in the SQLite
    # batch-recreate chain since then it silently reverted to NOT NULL
    # (every book-scoped request has been failing with a real
    # sqlite3.IntegrityError, caught and reported as "That didn't work
    # (IntegrityError)" by dispatch_trading_tool's generic handler --
    # found live while exercising the assistant's autonomous stock-analysis
    # demo, which is the first thing to actually call book=True end-to-end
    # against a populated book). Re-asserting it explicitly here, rather
    # than digging further into which prior batch rebuild dropped it, is
    # the safe fix either way.
    with op.batch_alter_table('risk_requests', schema=None) as batch_op:
        batch_op.alter_column('leg_id',
               existing_type=sa.INTEGER(),
               nullable=True)


def downgrade():
    with op.batch_alter_table('risk_requests', schema=None) as batch_op:
        batch_op.alter_column('leg_id',
               existing_type=sa.INTEGER(),
               nullable=False)
