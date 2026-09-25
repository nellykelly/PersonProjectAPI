"""Add assistant_eval_runs and assistant_eval_case_results

Revision ID: d7a3f92c1e58
Revises: b7d3e5f1a9c2
Create Date: 2026-09-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7a3f92c1e58'
down_revision = 'b7d3e5f1a9c2'
branch_labels = None
depends_on = None


def upgrade():
    # Gives `flask assistant eval` a memory: one row per run, one row per
    # case within that run. Purely additive -- no existing table touched.
    op.create_table(
        'assistant_eval_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('git_commit', sa.String(length=40), nullable=True),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('total_cases', sa.Integer(), nullable=False),
        sa.Column('passed_cases', sa.Integer(), nullable=False),
        sa.Column('failed_cases', sa.Integer(), nullable=False),
        sa.Column('avg_latency_ms', sa.Float(), nullable=True),
        sa.Column('total_tokens', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('assistant_eval_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_eval_runs_started_at'), ['started_at'], unique=False)

    op.create_table(
        'assistant_eval_case_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('case_name', sa.String(length=120), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('failures', sa.Text(), nullable=True),
        sa.Column('latency_ms', sa.Float(), nullable=False),
        sa.Column('tokens', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(length=32), nullable=True),
        sa.Column('cache_hit', sa.Boolean(), nullable=True),
        sa.Column('guard_flagged', sa.Boolean(), nullable=True),
        sa.Column('fell_back', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['assistant_eval_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('assistant_eval_case_results', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_assistant_eval_case_results_run_id'), ['run_id'], unique=False)


def downgrade():
    with op.batch_alter_table('assistant_eval_case_results', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_eval_case_results_run_id'))
    op.drop_table('assistant_eval_case_results')

    with op.batch_alter_table('assistant_eval_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_assistant_eval_runs_started_at'))
    op.drop_table('assistant_eval_runs')
