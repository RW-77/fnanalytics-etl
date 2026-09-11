"""drop region_code/season_code from event_windows and add the events FK

Revision ID: a8d3e1b5f927
Revises: f4b9c8e0d513
Create Date: 2026-05-26 10:00:00.000000

Final step of normalizing ``region_code`` and ``season_code`` onto
``events``. This migration:

1. Verifies every ``event_windows.event_id`` has a matching row in
   ``events`` (raises if not — better to fail loudly here than have the
   FK addition fail with a less-helpful Postgres error).
2. Drops the now-redundant ``region_code`` and ``season_code`` columns
   from ``event_windows``.
3. Adds the foreign key ``event_windows.event_id → events.event_id``.

The FK uses ``ON DELETE RESTRICT`` (Postgres' default; written
explicitly for clarity) so that deleting an event refuses while any of
its event windows still exist. If you want cascading deletion of all
event windows (and via their existing cascades, all match data) when an
event is removed, change ``ondelete=`` to ``"CASCADE"``.
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a8d3e1b5f927"
down_revision: Union[str, Sequence[str], None] = "f4b9c8e0d513"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Sanity check: every event_windows.event_id has a matching events row.
    #    Step 1's backfill should have ensured this, but if anything was
    #    inserted between Step 1 and this migration without going through
    #    ensure_event, we want to know now.
    #
    #    Skipped in offline mode (``alembic upgrade … --sql``) because
    #    there is no live connection to run the query against.
    if not context.is_offline_mode():
        bind = op.get_bind()
        orphans = bind.execute(
            sa.text(
                """
                SELECT DISTINCT ew.event_id
                FROM event_windows ew
                LEFT JOIN events e ON e.event_id = ew.event_id
                WHERE ew.event_id IS NOT NULL
                  AND e.event_id IS NULL
                """
            )
        ).fetchall()
        if orphans:
            ids = ", ".join(repr(row[0]) for row in orphans)
            raise RuntimeError(
                f"Cannot add events FK: {len(orphans)} event_window event_id(s) "
                f"have no matching events row: {ids}. Run the metadata-only "
                "backfill (ingest_event_window_metadata) on the affected windows "
                "first, then re-run this migration."
            )

    # 2. Drop the now-redundant columns. IF EXISTS so re-running after
    #    a partial state doesn't error.
    op.execute(
        "ALTER TABLE event_windows DROP COLUMN IF EXISTS region_code"
    )
    op.execute(
        "ALTER TABLE event_windows DROP COLUMN IF EXISTS season_code"
    )

    # 3. Add the FK. Explicit name so downgrade can drop it deterministically.
    op.create_foreign_key(
        "event_windows_event_id_fkey",
        "event_windows",
        "events",
        ["event_id"],
        ["event_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse order: drop FK first, then restore columns.
    op.execute(
        "ALTER TABLE event_windows DROP CONSTRAINT IF EXISTS "
        "event_windows_event_id_fkey"
    )

    op.add_column(
        "event_windows",
        sa.Column("season_code", sa.String(length=4), nullable=True),
    )
    op.add_column(
        "event_windows",
        sa.Column("region_code", sa.String(length=4), nullable=True),
    )

    # Best-effort backfill of the restored columns by joining events.
    # Won't reproduce any rows whose event_id was NULL — those didn't have
    # a region/season encoded in the old layout either.
    op.execute(
        """
        UPDATE event_windows ew
        SET region_code = e.region_code,
            season_code = e.season_code
        FROM events e
        WHERE e.event_id = ew.event_id
        """
    )
