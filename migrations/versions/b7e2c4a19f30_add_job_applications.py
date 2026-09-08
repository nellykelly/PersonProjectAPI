"""add job_applications and job_application_events

Nelson's private job-application tracker (the gated /job-tracker section).
Free-text columns are unbounded (Text) on purpose -- they hold whatever
is typed into the form or handed over by the assistant, and Postgres must
never silently truncate a posting's notes. Only `status` (and the audit
table's `action`/`source`) are bounded, since they come from fixed
vocabularies.

Revision ID: b7e2c4a19f30
Revises: f4e9c2a7b810
Create Date: 2026-09-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2c4a19f30'
down_revision = 'f4e9c2a7b810'
branch_labels = None
depends_on = None


def upgrade():
    # Constraints named explicitly (matching f4e9c2a7b810's style) so this
    # applies cleanly on both Postgres and SQLite and a later migration can
    # DROP/ALTER them by name rather than fighting an auto-generated one.
    op.create_table(
        'job_applications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_name', sa.Text(), nullable=False),
        sa.Column('role_title', sa.Text(), nullable=False),
        sa.Column('job_posting_url', sa.Text(), nullable=True),
        sa.Column('date_applied', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('status_updated_at', sa.DateTime(), nullable=False),
        sa.Column('source', sa.Text(), nullable=True),
        sa.Column('resume_version', sa.Text(), nullable=True),
        sa.Column('cover_letter_used', sa.Boolean(), nullable=False),
        sa.Column('company_industry', sa.Text(), nullable=True),
        sa.Column('company_size_stage', sa.Text(), nullable=True),
        sa.Column('location_remote_policy', sa.Text(), nullable=True),
        sa.Column('tech_stack', sa.Text(), nullable=True),
        sa.Column('salary_range', sa.Text(), nullable=True),
        sa.Column('match_grade', sa.Integer(), nullable=True),
        sa.Column('match_notes', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_job_applications'),
    )

    op.create_table(
        'job_application_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('application_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=8), nullable=False),
        sa.Column('source', sa.String(length=12), nullable=False),
        sa.Column('field_name', sa.Text(), nullable=True),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['application_id'],
            ['job_applications.id'],
            name='fk_job_application_events_application_id_job_applications',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_job_application_events'),
    )
    with op.batch_alter_table('job_application_events', schema=None) as batch_op:
        batch_op.create_index(
            'ix_job_application_events_application_id',
            ['application_id'],
            unique=False,
        )
        batch_op.create_index(
            'ix_job_application_events_created_at',
            ['created_at'],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table('job_application_events', schema=None) as batch_op:
        batch_op.drop_index('ix_job_application_events_created_at')
        batch_op.drop_index('ix_job_application_events_application_id')
    op.drop_table('job_application_events')
    op.drop_table('job_applications')
