"""add the /family suite tables

The private household section: two members, a calendar (with a small JSON
recurrence rule), grocery orders + their items, shared memories Hera keeps
across chats, and per-member chat threads. Free text is unbounded (Text);
only fixed vocab is bounded String -- grocery order status ('open' |
'archived', 8), chat message role ('user' | 'assistant' | 'tool', 9),
member slug ('m1' | 'm2', 2) and accent -- each sized to the longest
literal plus headroom. No pgvector / no Postgres-only DDL, so no is_pg
guard. Every NOT NULL column has a Python-side default in the model so a
dev SQLite file heals itself; whole new tables like these appear via
db.create_all() in dev regardless.

The two members are seeded here: m1 = Nelson, m2 = Savannah.

Revision ID: d3a1f7c05e92
Revises: c8f1a2d34e56
Create Date: 2026-09-09 00:00:00.000000

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3a1f7c05e92'
down_revision = 'c8f1a2d34e56'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'family_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('slug', sa.String(length=8), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('accent', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_family_members'),
        sa.UniqueConstraint('slug', name='uq_family_members_slug'),
    )

    op.create_table(
        'family_calendar_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('starts_on', sa.Date(), nullable=False),
        sa.Column('starts_at', sa.Time(), nullable=True),
        sa.Column('ends_at', sa.Time(), nullable=True),
        sa.Column('all_day', sa.Boolean(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('recurrence', sa.JSON(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['family_members.id'],
            name='fk_family_calendar_events_created_by_id_family_members',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_family_calendar_events'),
    )
    with op.batch_alter_table('family_calendar_events', schema=None) as batch_op:
        batch_op.create_index(
            'ix_family_calendar_events_created_by_id', ['created_by_id'], unique=False
        )

    op.create_table(
        'family_grocery_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('archived_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['family_members.id'],
            name='fk_family_grocery_orders_created_by_id_family_members',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_family_grocery_orders'),
    )
    with op.batch_alter_table('family_grocery_orders', schema=None) as batch_op:
        batch_op.create_index(
            'ix_family_grocery_orders_created_by_id', ['created_by_id'], unique=False
        )
        batch_op.create_index(
            'ix_family_grocery_orders_created_at', ['created_at'], unique=False
        )

    op.create_table(
        'family_grocery_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('quantity', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('got', sa.Boolean(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('added_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['order_id'], ['family_grocery_orders.id'],
            name='fk_family_grocery_items_order_id_family_grocery_orders',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['added_by_id'], ['family_members.id'],
            name='fk_family_grocery_items_added_by_id_family_members',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_family_grocery_items'),
    )
    with op.batch_alter_table('family_grocery_items', schema=None) as batch_op:
        batch_op.create_index(
            'ix_family_grocery_items_order_id', ['order_id'], unique=False
        )
        batch_op.create_index(
            'ix_family_grocery_items_added_by_id', ['added_by_id'], unique=False
        )

    op.create_table(
        'family_memories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['created_by_id'], ['family_members.id'],
            name='fk_family_memories_created_by_id_family_members',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_family_memories'),
    )
    with op.batch_alter_table('family_memories', schema=None) as batch_op:
        batch_op.create_index(
            'ix_family_memories_created_by_id', ['created_by_id'], unique=False
        )
        batch_op.create_index(
            'ix_family_memories_created_at', ['created_at'], unique=False
        )

    op.create_table(
        'family_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tool_calls', sa.JSON(), nullable=True),
        sa.Column('tool_call_id', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['member_id'], ['family_members.id'],
            name='fk_family_chat_messages_member_id_family_members',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_family_chat_messages'),
    )
    with op.batch_alter_table('family_chat_messages', schema=None) as batch_op:
        batch_op.create_index(
            'ix_family_chat_messages_member_id', ['member_id'], unique=False
        )
        batch_op.create_index(
            'ix_family_chat_messages_created_at', ['created_at'], unique=False
        )

    # Seed the two household members.
    now = datetime.now(timezone.utc)
    members = sa.table(
        'family_members',
        sa.column('slug', sa.String),
        sa.column('name', sa.String),
        sa.column('accent', sa.String),
        sa.column('created_at', sa.DateTime),
    )
    op.bulk_insert(
        members,
        [
            {'slug': 'm1', 'name': 'Nelson', 'accent': 'leaf', 'created_at': now},
            {'slug': 'm2', 'name': 'Savannah', 'accent': 'bloom', 'created_at': now},
        ],
    )


def downgrade():
    with op.batch_alter_table('family_chat_messages', schema=None) as batch_op:
        batch_op.drop_index('ix_family_chat_messages_created_at')
        batch_op.drop_index('ix_family_chat_messages_member_id')
    op.drop_table('family_chat_messages')

    with op.batch_alter_table('family_memories', schema=None) as batch_op:
        batch_op.drop_index('ix_family_memories_created_at')
        batch_op.drop_index('ix_family_memories_created_by_id')
    op.drop_table('family_memories')

    with op.batch_alter_table('family_grocery_items', schema=None) as batch_op:
        batch_op.drop_index('ix_family_grocery_items_added_by_id')
        batch_op.drop_index('ix_family_grocery_items_order_id')
    op.drop_table('family_grocery_items')

    with op.batch_alter_table('family_grocery_orders', schema=None) as batch_op:
        batch_op.drop_index('ix_family_grocery_orders_created_at')
        batch_op.drop_index('ix_family_grocery_orders_created_by_id')
    op.drop_table('family_grocery_orders')

    with op.batch_alter_table('family_calendar_events', schema=None) as batch_op:
        batch_op.drop_index('ix_family_calendar_events_created_by_id')
    op.drop_table('family_calendar_events')

    op.drop_table('family_members')
