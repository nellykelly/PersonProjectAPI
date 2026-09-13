"""Add job_discovery_runs for real Adzuna quota tracking

Revision ID: 5b8e0c3a9f21
Revises: a17c9f2b6d41
Create Date: 2026-09-13 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5b8e0c3a9f21'
down_revision = 'a17c9f2b6d41'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'job_discovery_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ran_at', sa.DateTime(), nullable=False),
        sa.Column('adzuna_calls', sa.Integer(), nullable=False),
        sa.Column('fetched', sa.Integer(), nullable=False),
        sa.Column('new_listings', sa.Integer(), nullable=False),
        sa.Column('scored', sa.Integer(), nullable=False),
        sa.Column('truncated', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_job_discovery_runs_ran_at'), ['ran_at'], unique=False
        )


def downgrade():
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_discovery_runs_ran_at'))
    op.drop_table('job_discovery_runs')
