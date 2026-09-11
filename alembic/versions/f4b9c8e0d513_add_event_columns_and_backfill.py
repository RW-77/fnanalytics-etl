"""add event metadata columns and backfill from event_windows

Revision ID: f4b9c8e0d513
Revises: e3b8c5d92f47
Create Date: 2026-05-25 14:00:00.000000

Step 1 of the three-step normalization moving region_code, season_code,
and (new) image_key onto ``events``.

After this migration:
* ``events`` has ``region_code``, ``season_code``, ``image_key`` columns.
* ``events`` is backfilled with one row per distinct ``event_id`` seen in
  ``event_windows``, copying ``region_code`` / ``season_code`` over.
* ``event_windows`` is unchanged — both tables redundantly hold the
  classification metadata until step 3 drops it from event_windows.

The backfill uses ``MIN()`` over event_windows because all windows of a
given event are expected to agree on region/season; this is a sanity-safe
aggregation that produces the same value as ``MAX()`` when they do agree
and a deterministic pick when (somehow) they don't.

``ON CONFLICT DO NOTHING`` makes the backfill rerunnable. If you need to
recompute, ``DELETE FROM events`` before re-applying — events is not
populated by anything else at this point.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4b9c8e0d513"
down_revision: Union[str, Sequence[str], None] = "e3b8c5d92f47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add new columns to events. All nullable so existing rows
    #    (currently zero of them, but defensive against future state)
    #    don't need backfilled defaults.
    op.add_column(
        "events",
        sa.Column("region_code", sa.String(length=4), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("season_code", sa.String(length=4), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("image_key", sa.String(), nullable=True),
    )

    # 2. Backfill events from event_windows. One Event row per distinct
    #    non-null event_id; the MIN() picks a deterministic value per
    #    group. ON CONFLICT DO NOTHING makes this idempotent.
    op.execute(
        """
        INSERT INTO events (event_id, region_code, season_code)
        SELECT
            event_id,
            MIN(region_code) AS region_code,
            MIN(season_code) AS season_code
        FROM event_windows
        WHERE event_id IS NOT NULL
        GROUP BY event_id
        ON CONFLICT (event_id) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the three columns. We don't try to un-backfill — the rows in
    ``events`` are harmless if left behind, and you can ``TRUNCATE
    events`` manually if you really want a clean slate.
    """
    op.drop_column("events", "image_key")
    op.drop_column("events", "season_code")
    op.drop_column("events", "region_code")
