"""Add job_search_profiles.lever_boards (Lever watchlist)

Revision ID: fce390538cdc
Revises: ba48b698e478
Create Date: 2026-09-16 00:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fce390538cdc'
down_revision = 'ba48b698e478'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('lever_boards', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.drop_column('lever_boards')
