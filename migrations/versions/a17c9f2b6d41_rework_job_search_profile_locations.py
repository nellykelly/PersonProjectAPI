"""Rework job_search_profiles: location -> locations, remote_only -> include_remote

Revision ID: a17c9f2b6d41
Revises: ec8d3b5da35f
Create Date: 2026-09-13 16:00:00.000000

`location` becomes `locations` (now a newline-separated list of places
searched in parallel, not one filter) and `remote_only` becomes
`include_remote` (now an additive nationwide sweep alongside `locations`,
not an exclusive toggle that replaces them). Pure renames -- existing
values carry over unchanged; app/services/job_discovery.py reseeds the
content on the next settings save.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a17c9f2b6d41'
down_revision = 'ec8d3b5da35f'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.alter_column('location', new_column_name='locations')
        batch_op.alter_column('remote_only', new_column_name='include_remote')


def downgrade():
    with op.batch_alter_table('job_search_profiles', schema=None) as batch_op:
        batch_op.alter_column('locations', new_column_name='location')
        batch_op.alter_column('include_remote', new_column_name='remote_only')
