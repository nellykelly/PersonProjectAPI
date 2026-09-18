"""Add job_listings.graded_by

Revision ID: 70a30b312ade
Revises: fce390538cdc
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '70a30b312ade'
down_revision = 'fce390538cdc'
branch_labels = None
depends_on = None


def upgrade():
    # 'llm' or 'keyword' once a listing has a match_grade -- NULL while
    # still ungraded. Lets a keyword pre-score (cheap, no Groq call) stay
    # visually and programmatically distinct from a real LLM grade, so a
    # cheap heuristic never gets silently trusted as much as the real
    # thing. See app.services.job_discovery._keyword_prescore.
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('graded_by', sa.String(length=16), nullable=True))


def downgrade():
    with op.batch_alter_table('job_listings', schema=None) as batch_op:
        batch_op.drop_column('graded_by')
