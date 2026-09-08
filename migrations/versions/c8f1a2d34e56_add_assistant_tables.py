"""add content_chunks + assistant_queries (personal AI assistant)

`content_chunks.embedding` is pgvector `vector(384)` on PostgreSQL (prod)
and JSON elsewhere -- and the `CREATE EXTENSION` / HNSW index are guarded
to PostgreSQL, so `flask db upgrade` still works on a SQLite dev DB even
though retrieval itself needs Postgres (same shape as /pipeline-analytics).

Constraints are named explicitly (matching f4e9c2a7b810) so a later
migration can ALTER/DROP them by name on Postgres.

Revision ID: c8f1a2d34e56
Revises: b7e2c4a19f30
Create Date: 2026-09-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f1a2d34e56'
down_revision = 'b7e2c4a19f30'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == 'postgresql'

    if is_pg:
        op.execute('CREATE EXTENSION IF NOT EXISTS vector')
        from pgvector.sqlalchemy import Vector
        embedding_type = Vector(384)
    else:
        embedding_type = sa.JSON()

    op.create_table(
        'content_chunks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('token_estimate', sa.Integer(), nullable=False),
        sa.Column('embedding', embedding_type, nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_content_chunks'),
        sa.UniqueConstraint('source', 'chunk_index', name='uq_content_chunks_source_idx'),
    )
    with op.batch_alter_table('content_chunks', schema=None) as batch_op:
        batch_op.create_index('ix_content_chunks_source', ['source'], unique=False)

    if is_pg:
        # Approximate-NN index for the `<=>` cosine operator used by
        # app/services/assistant/store.py. HNSW builds on an empty table
        # fine; `reindex` populates it afterwards.
        op.execute(
            'CREATE INDEX ix_content_chunks_embedding ON content_chunks '
            'USING hnsw (embedding vector_cosine_ops)'
        )

    op.create_table(
        'assistant_queries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('ip_hash', sa.String(length=64), nullable=True),
        sa.Column('is_admin', sa.Boolean(), nullable=False),
        sa.Column('backend', sa.String(length=16), nullable=True),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('prompt_tokens_est', sa.Integer(), nullable=True),
        sa.Column('completion_tokens_est', sa.Integer(), nullable=True),
        sa.Column('n_chunks', sa.Integer(), nullable=True),
        sa.Column('n_sources', sa.Integer(), nullable=True),
        sa.Column('question', sa.Text(), nullable=True),
        sa.Column('reply', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        # token-free heuristic classification for /assistant/stats
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('sentiment', sa.String(length=12), nullable=True),
        sa.Column('is_frustrated', sa.Boolean(), nullable=True),
        sa.Column('profanity_count', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(length=24), nullable=True),
        sa.Column('reply_kind', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_assistant_queries'),
    )
    with op.batch_alter_table('assistant_queries', schema=None) as batch_op:
        batch_op.create_index('ix_assistant_queries_created_at', ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('assistant_queries', schema=None) as batch_op:
        batch_op.drop_index('ix_assistant_queries_created_at')
    op.drop_table('assistant_queries')

    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP INDEX IF EXISTS ix_content_chunks_embedding')
    with op.batch_alter_table('content_chunks', schema=None) as batch_op:
        batch_op.drop_index('ix_content_chunks_source')
    op.drop_table('content_chunks')
