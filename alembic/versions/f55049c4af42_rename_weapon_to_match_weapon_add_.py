"""rename_weapon_to_match_weapon_add_weapon_definitions

Revision ID: f55049c4af42
Revises: b6c4a92e0758
Create Date: 2026-06-08 11:59:37.261604

What this migration does
------------------------
1. Creates the new ``match_weapons`` table (weapons observed in a specific
   match, sourced from GetMatchWeapons). Replaces the old ``weapons`` table
   for the "which weapons appeared in this match" use-case.

2. Drops and recreates the ``weapons`` table as a global weapon *definitions*
   catalog (synced from fnapi.osirion.gg/v1/weapons). The old table had an
   integer surrogate PK and was scoped to event windows — the new one has the
   API weapon ID as a natural string PK and holds stats, images, etc.
   The old rows are dropped intentionally: they were event-window-scoped
   weapon usage records that can't be meaningfully mapped to the new schema.

3. Widens ``weapon_id`` / ``weapon_type`` on ``damage_dealt_events`` and
   ``elimination_events`` to match the new column lengths.

4. Marks ``event_windows.event_id`` NOT NULL (it always was in practice)
   and re-creates the FK to ``events`` without the explicit RESTRICT name so
   it uses PostgreSQL's default naming convention.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f55049c4af42'
down_revision: Union[str, Sequence[str], None] = 'b6c4a92e0758'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create match_weapons (new table; no data migration needed)
    # ------------------------------------------------------------------
    op.create_table(
        'match_weapons',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('match_id', sa.String(length=50), nullable=False),
        sa.Column('weapon_id', sa.String(length=200), nullable=False),
        sa.Column('weapon_type', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['match_id'], ['matches.match_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_match_weapon_match',  'match_weapons', ['match_id'],              unique=False)
    op.create_index('idx_match_weapon_weapon', 'match_weapons', ['weapon_id'],             unique=False)
    op.create_index('idx_match_weapon_unique', 'match_weapons', ['weapon_id', 'match_id'], unique=True)

    # ------------------------------------------------------------------
    # 2. Replace the weapons table
    #
    # The old table was an event-window-scoped weapon usage table with an
    # integer surrogate PK. The new table is a global weapon catalog with
    # the API weapon ID as a natural string PK. The schemas are too
    # different to migrate in place, so we drop and recreate.
    #
    # Old rows are intentionally discarded — they were event-window weapon
    # usage records, a role now served by match_weapons.
    # ------------------------------------------------------------------
    op.drop_index('idx_weapon_type',         table_name='weapons')
    op.drop_index('idx_weapon_event_window', table_name='weapons')
    op.drop_index('idx_weapon_weapon_id',    table_name='weapons')
    op.drop_index('idx_weapon_unique',       table_name='weapons')
    op.drop_constraint('weapons_event_window_id_fkey', 'weapons', type_='foreignkey')
    op.drop_table('weapons')

    op.create_table(
        'weapons',
        sa.Column('id',           sa.String(length=200), nullable=False),
        sa.Column('name',         sa.String(length=200), nullable=True),
        sa.Column('description',  sa.String(length=1000), nullable=True),
        sa.Column('weapon_type',  sa.String(length=100), nullable=True),
        sa.Column('rarity',       sa.String(length=50),  nullable=True),
        sa.Column('ammo',         sa.String(length=100), nullable=True),
        sa.Column('gameplay_tags', sa.JSON(),             nullable=True),
        sa.Column('image_key',       sa.String(length=300), nullable=True),
        sa.Column('small_image_key', sa.String(length=300), nullable=True),
        sa.Column('image_url',       sa.String(length=500), nullable=True),
        sa.Column('small_image_url', sa.String(length=500), nullable=True),
        sa.Column('dmg_pb',                sa.Float(),   nullable=True),
        sa.Column('firing_rate',           sa.Float(),   nullable=True),
        sa.Column('clip_size',             sa.Integer(), nullable=True),
        sa.Column('reload_time',           sa.Float(),   nullable=True),
        sa.Column('bullets_per_cartridge', sa.Integer(), nullable=True),
        sa.Column('spread',                sa.Float(),   nullable=True),
        sa.Column('spread_downsights',     sa.Float(),   nullable=True),
        sa.Column('damage_zone_critical',  sa.Float(),   nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at',  sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_weapon_type',   'weapons', ['weapon_type'], unique=False)
    op.create_index('idx_weapon_rarity', 'weapons', ['rarity'],      unique=False)

    # ------------------------------------------------------------------
    # 3. Widen weapon_id / weapon_type on event tables
    # ------------------------------------------------------------------
    op.alter_column('damage_dealt_events', 'weapon_id',
                    existing_type=sa.VARCHAR(length=100),
                    type_=sa.String(length=200),
                    existing_nullable=False)
    op.alter_column('damage_dealt_events', 'weapon_type',
                    existing_type=sa.VARCHAR(length=50),
                    type_=sa.String(length=100),
                    existing_nullable=True)
    op.alter_column('elimination_events', 'weapon_id',
                    existing_type=sa.VARCHAR(length=100),
                    type_=sa.String(length=200),
                    existing_nullable=False)
    op.alter_column('elimination_events', 'weapon_type',
                    existing_type=sa.VARCHAR(length=50),
                    type_=sa.String(length=100),
                    existing_nullable=True)

    # ------------------------------------------------------------------
    # 4. event_windows.event_id: mark NOT NULL, recreate FK
    # ------------------------------------------------------------------
    op.alter_column('event_windows', 'event_id',
                    existing_type=sa.VARCHAR(length=100),
                    nullable=False)
    op.drop_constraint('event_windows_event_id_fkey', 'event_windows', type_='foreignkey')
    op.create_foreign_key(None, 'event_windows', 'events', ['event_id'], ['event_id'])


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 4. Revert event_windows.event_id
    # ------------------------------------------------------------------
    op.drop_constraint(None, 'event_windows', type_='foreignkey')
    op.create_foreign_key(
        'event_windows_event_id_fkey', 'event_windows', 'events',
        ['event_id'], ['event_id'], ondelete='RESTRICT',
    )
    op.alter_column('event_windows', 'event_id',
                    existing_type=sa.VARCHAR(length=100),
                    nullable=True)

    # ------------------------------------------------------------------
    # 3. Restore original column widths on event tables
    # ------------------------------------------------------------------
    op.alter_column('elimination_events', 'weapon_type',
                    existing_type=sa.String(length=100),
                    type_=sa.VARCHAR(length=50),
                    existing_nullable=True)
    op.alter_column('elimination_events', 'weapon_id',
                    existing_type=sa.String(length=200),
                    type_=sa.VARCHAR(length=100),
                    existing_nullable=False)
    op.alter_column('damage_dealt_events', 'weapon_type',
                    existing_type=sa.String(length=100),
                    type_=sa.VARCHAR(length=50),
                    existing_nullable=True)
    op.alter_column('damage_dealt_events', 'weapon_id',
                    existing_type=sa.String(length=200),
                    type_=sa.VARCHAR(length=100),
                    existing_nullable=False)

    # ------------------------------------------------------------------
    # 2. Restore old weapons table (no data recovery — rows were dropped)
    # ------------------------------------------------------------------
    op.drop_index('idx_weapon_rarity', table_name='weapons')
    op.drop_index('idx_weapon_type',   table_name='weapons')
    op.drop_table('weapons')

    op.create_table(
        'weapons',
        sa.Column('id',              sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('weapon_id',       sa.VARCHAR(length=100), nullable=False),
        sa.Column('weapon_type',     sa.VARCHAR(length=50),  nullable=False),
        sa.Column('event_window_id', sa.VARCHAR(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ['event_window_id'], ['event_windows.event_window_id'],
            name='weapons_event_window_id_fkey',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_weapon_type',         'weapons', ['weapon_type'],                    unique=False)
    op.create_index('idx_weapon_event_window', 'weapons', ['event_window_id'],                unique=False)
    op.create_index('idx_weapon_weapon_id',    'weapons', ['weapon_id'],                      unique=False)
    op.create_index('idx_weapon_unique',       'weapons', ['weapon_id', 'event_window_id'],   unique=True)

    # ------------------------------------------------------------------
    # 1. Drop match_weapons
    # ------------------------------------------------------------------
    op.drop_index('idx_match_weapon_unique', table_name='match_weapons')
    op.drop_index('idx_match_weapon_weapon', table_name='match_weapons')
    op.drop_index('idx_match_weapon_match',  table_name='match_weapons')
    op.drop_table('match_weapons')
