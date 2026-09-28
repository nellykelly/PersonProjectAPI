"""Add job_applications.posting_summary / graded_by / grade_attempts

Revision ID: e5c2b9d47a13
Revises: d7a3f92c1e58
Create Date: 2026-09-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5c2b9d47a13'
down_revision = 'd7a3f92c1e58'
branch_labels = None
depends_on = None


def upgrade():
    # Lets the hourly rescore cron grade tracker cards, not just Discover
    # listings: posting_summary is what the grader reads, graded_by keeps
    # an LLM grade distinct from a manual one (only 'llm' grades are ever
    # redone), and grade_attempts caps retries like job_listings.score_attempts.
    # Existing grades were all set by hand or promoted from a listing, so
    # they're backfilled as 'manual' -- the cron never overwrites them.
    with op.batch_alter_table('job_applications', schema=None) as batch_op:
        batch_op.add_column(sa.Column('posting_summary', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('graded_by', sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column('grade_attempts', sa.Integer(), nullable=False, server_default='0')
        )
    op.execute("UPDATE job_applications SET graded_by = 'manual' WHERE match_grade IS NOT NULL")


def downgrade():
    with op.batch_alter_table('job_applications', schema=None) as batch_op:
        batch_op.drop_column('grade_attempts')
        batch_op.drop_column('graded_by')
        batch_op.drop_column('posting_summary')
