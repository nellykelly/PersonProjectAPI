"""Add groq_prompt_tokens/groq_completion_tokens to job_discovery_runs

Revision ID: d92f6a1c8e4b
Revises: c4a7e91f0b3d
Create Date: 2026-09-13 19:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd92f6a1c8e4b'
down_revision = 'c4a7e91f0b3d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('groq_prompt_tokens', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('groq_completion_tokens', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('job_discovery_runs', schema=None) as batch_op:
        batch_op.drop_column('groq_completion_tokens')
        batch_op.drop_column('groq_prompt_tokens')
