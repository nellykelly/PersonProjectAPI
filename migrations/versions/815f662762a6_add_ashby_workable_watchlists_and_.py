"""Add Ashby/Workable watchlists and remotive_calls tracking

Revision ID: 815f662762a6
Revises: 70a30b312ade
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '815f662762a6'
down_revision = '70a30b312ade'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ashby_boards', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('workable_boards', sa.Text(), nullable=True))
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('remotive_calls', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.drop_column('remotive_calls')
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.drop_column('workable_boards')
        batch_op.drop_column('ashby_boards')
