"""Add job_search_profiles.company_boards (Greenhouse watchlist)

Revision ID: e3b6a8d1f905
Revises: d92f6a1c8e4b
Create Date: 2026-09-13 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e3b6a8d1f905'
down_revision = 'd92f6a1c8e4b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company_boards', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.drop_column('company_boards')
