"""add event window classification fields and ON DELETE CASCADE to match-scoped FKs

Revision ID: c1f3d4e5a6b7
Revises: 2a8f5b9c4d10
Create Date: 2026-05-24 12:00:00.000000

Adds the parse-derived columns the loader now writes to ``event_windows``
(``event_id``, ``tournament_id``, ``region_code``, ``season_code``,
``day_index``), all nullable so the existing rows are left untouched. Then
re-creates the seven match-scoped foreign keys with ``ON DELETE CASCADE``
so that re-ingest can wipe ``match_players`` and have the event rows fall
away with them.

Postgres does not allow altering an FK's ON DELETE behavior in place, so
each constraint is dropped and re-created. The names below
(``<table>_<column>_fkey``) are the defaults Postgres assigns when a
constraint is created inline; if any of them were created with a custom
name, ``alembic upgrade`` will fail on the matching ``drop_constraint``
and you'll need to substitute the real name.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1f3d4e5a6b7"
down_revision: Union[str, Sequence[str], None] = "2a8f5b9c4d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    # 1. New columns on event_windows. All nullable: existing rows have no
    #    classification metadata and we don't want to invent values for them.
    op.add_column(
        "event_windows",
        sa.Column("event_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "event_windows",
        sa.Column("tournament_id", sa.String(), nullable=True),
    )
    op.add_column(
        "event_windows",
        sa.Column("region_code", sa.String(length=4), nullable=True),
    )
    op.add_column(
        "event_windows",
        sa.Column("season_code", sa.String(length=4), nullable=True),
    )
    op.add_column(
        "event_windows",
        sa.Column("day_index", sa.Integer(), nullable=True),
    )

    # 2. Re-create match-scoped FKs with ON DELETE CASCADE.
    for name, src_table, src_col, tgt_table, tgt_col in CASCADE_FKS:
        op.drop_constraint(name, src_table, type_="foreignkey")
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
    # 1. Restore FKs without ON DELETE CASCADE (in reverse order, though
    #    order doesn't actually matter for FK swaps).
    for name, src_table, src_col, tgt_table, tgt_col in reversed(CASCADE_FKS):
        op.drop_constraint(name, src_table, type_="foreignkey")
        op.create_foreign_key(
            name,
            src_table,
            tgt_table,
            [src_col],
            [tgt_col],
        )

    # 2. Drop the new event_windows columns.
    op.drop_column("event_windows", "day_index")
    op.drop_column("event_windows", "season_code")
    op.drop_column("event_windows", "region_code")
    op.drop_column("event_windows", "tournament_id")
    op.drop_column("event_windows", "event_id")
