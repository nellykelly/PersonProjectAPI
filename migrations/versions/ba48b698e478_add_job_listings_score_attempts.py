"""Add job_listings.score_attempts

Revision ID: ba48b698e478
Revises: c17d555c8581
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ba48b698e478'
down_revision = 'c17d555c8581'
branch_labels = None
depends_on = None


def upgrade():
    # Tracks how many times a listing has been sent to the scorer, so
    # `flask job-tracker rescore` (see app.services.job_discovery
    # .rescore_pending_listings) can retry listings that failed to score
    # -- most often because Groq's daily token quota was exhausted mid-run
    # -- without retrying a systematically unparseable one forever.
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('score_attempts', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.drop_column('score_attempts')
