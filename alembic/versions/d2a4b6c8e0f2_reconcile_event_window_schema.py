"""reconcile event_windows schema and apply cascade FKs idempotently

Revision ID: d2a4b6c8e0f2
Revises: c1f3d4e5a6b7
Create Date: 2026-05-24 14:00:00.000000

Repair migration: the prior revision failed partway through on databases
where ``Base.metadata.create_all()`` had already materialized some of the
classification columns out-of-band. This one uses ``ADD COLUMN IF NOT
EXISTS`` and drops constraints conditionally so it can run cleanly
regardless of the actual partial state of the database.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d2a4b6c8e0f2"
down_revision: Union[str, Sequence[str], None] = "c1f3d4e5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLUMNS: list[tuple[str, str]] = [
    ("event_id", "VARCHAR(100)"),
    ("tournament_id", "VARCHAR"),
    ("region_code", "VARCHAR(4)"),
    ("season_code", "VARCHAR(4)"),
    ("day_index", "INTEGER"),
]


# (constraint_name, source_table, source_column, target_table, target_column)
CASCADE_FKS: list[tuple[str, str, str, str, str]] = [
    ("match_players_match_id_fkey", "match_players", "match_id", "matches", "match_id"),
    ("damage_dealt_events_match_id_fkey", "damage_dealt_events", "match_id", "matches", "match_id"),
    ("damage_dealt_events_actor_id_fkey", "damage_dealt_events", "actor_id", "match_players", "id"),
    ("damage_dealt_events_recipient_id_fkey", "damage_dealt_events", "recipient_id", "match_players", "id"),
    ("elimination_events_match_id_fkey", "elimination_events", "match_id", "matches", "match_id"),
    ("elimination_events_actor_id_fkey", "elimination_events", "actor_id", "match_players", "id"),
    ("elimination_events_recipient_id_fkey", "elimination_events", "recipient_id", "match_players", "id"),
]


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add classification columns to event_windows, no-op if already present.
    for name, sql_type in COLUMNS:
        op.execute(
            f"ALTER TABLE event_windows ADD COLUMN IF NOT EXISTS {name} {sql_type}"
        )

    # 2. Re-create match-scoped FKs with ON DELETE CASCADE. Drop with
    #    IF EXISTS so this is safe even if the prior migration already
    #    swapped some of them.
    for name, src_table, src_col, tgt_table, tgt_col in CASCADE_FKS:
        op.execute(
            f"ALTER TABLE {src_table} DROP CONSTRAINT IF EXISTS {name}"
        )
        op.create_foreign_key(
            name,
            src_table,
            tgt_table,
            [src_col],
            [tgt_col],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """Downgrade schema."""
    for name, src_table, src_col, tgt_table, tgt_col in reversed(CASCADE_FKS):
        op.execute(
            f"ALTER TABLE {src_table} DROP CONSTRAINT IF EXISTS {name}"
        )
        op.create_foreign_key(
            name,
            src_table,
            tgt_table,
            [src_col],
            [tgt_col],
        )

    for name, _ in reversed(COLUMNS):
        op.execute(f"ALTER TABLE event_windows DROP COLUMN IF EXISTS {name}")
