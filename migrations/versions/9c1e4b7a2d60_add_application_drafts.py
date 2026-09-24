"""Add application_drafts

Revision ID: 9c1e4b7a2d60
Revises: 815f662762a6
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c1e4b7a2d60'
down_revision = '815f662762a6'
branch_labels = None
depends_on = None


def upgrade():
    # One drafter -> reviewer -> revise pass per row over a tracked
    # application (see app.services.application_drafter). Text only --
    # nothing in this table is ever submitted anywhere.
    op.create_table(
        'application_drafts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('application_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('phase', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('posting_text', sa.Text(), nullable=False),
        sa.Column('fit_verdict', sa.Text(), nullable=True),
        sa.Column('strengths', sa.Text(), nullable=True),
        sa.Column('gaps', sa.Text(), nullable=True),
        sa.Column('headline', sa.Text(), nullable=True),
        sa.Column('resume_summary', sa.Text(), nullable=True),
        sa.Column('resume_bullets', sa.Text(), nullable=True),
        sa.Column('cover_letter', sa.Text(), nullable=True),
        sa.Column('why_company', sa.Text(), nullable=True),
        sa.Column('short_pitch', sa.Text(), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=True),
        sa.Column('groq_prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('groq_completion_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['application_id'], ['job_applications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('application_drafts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_application_drafts_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_application_drafts_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('application_drafts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_application_drafts_created_at'))
        batch_op.drop_index(batch_op.f('ix_application_drafts_application_id'))
    op.drop_table('application_drafts')
