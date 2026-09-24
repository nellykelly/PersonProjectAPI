"""Add observability columns to assistant_queries

Revision ID: b7d3e5f1a9c2
Revises: 9c1e4b7a2d60
Create Date: 2026-09-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d3e5f1a9c2'
down_revision = '9c1e4b7a2d60'
branch_labels = None
depends_on = None


def upgrade():
    # All nullable, no server default: rows logged before this migration
    # simply have no answer for these questions, and the stats page treats
    # NULL as "not recorded" rather than as False. The existing `model`
    # column already records which model answered, so nothing new for that.
    with op.batch_alter_table('assistant_queries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('request_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('fell_back', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('cache_hit', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('guard_flagged', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('guard_score', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('guard_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('busy', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('deadline_hit', sa.Boolean(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_assistant_queries_request_id'), ['request_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('assistant_queries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_queries_request_id'))
        batch_op.drop_column('deadline_hit')
        batch_op.drop_column('busy')
        batch_op.drop_column('guard_error')
        batch_op.drop_column('guard_score')
        batch_op.drop_column('guard_flagged')
        batch_op.drop_column('cache_hit')
        batch_op.drop_column('fell_back')
        batch_op.drop_column('request_id')
