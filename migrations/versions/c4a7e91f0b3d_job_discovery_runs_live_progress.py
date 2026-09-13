"""Rework job_discovery_runs for live progress tracking

Revision ID: c4a7e91f0b3d
Revises: 5b8e0c3a9f21
Create Date: 2026-09-13 18:00:00.000000

job_discovery_runs previously got one row written after a run finished,
purely for quota tracking. Now a row is written the instant a run starts
(status="running") and updated as it goes, so /job-tracker/discover can
poll it to show live progress for a run that's moved to a background job
instead of blocking the request. Dropped and recreated rather than
altered column-by-column -- this table has no real historical data yet
(shipped one release ago), and the shape changed enough (new `status`,
`phase`, `progress_current`/`progress_total`, `error_message`, a rename
of `ran_at` to `started_at` plus a new `finished_at`) that a rewrite is
clearer than a chain of renames/alters.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4a7e91f0b3d'
down_revision = '5b8e0c3a9f21'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_job_discovery_runs_ran_at')
    op.drop_table('job_discovery_runs')

    op.create_table(
        'job_discovery_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('phase', sa.Text(), nullable=True),
        sa.Column('progress_current', sa.Integer(), nullable=False),
        sa.Column('progress_total', sa.Integer(), nullable=False),
        sa.Column('adzuna_calls', sa.Integer(), nullable=False),
        sa.Column('fetched', sa.Integer(), nullable=False),
        sa.Column('new_listings', sa.Integer(), nullable=False),
        sa.Column('scored', sa.Integer(), nullable=False),
        sa.Column('truncated', sa.Boolean(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_job_discovery_runs_started_at'), ['started_at'], unique=False
        )


def downgrade():
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_discovery_runs_started_at'))
    op.drop_table('job_discovery_runs')

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
        batch_op.create_index('ix_job_discovery_runs_ran_at', ['ran_at'], unique=False)
