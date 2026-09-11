"""drop start_time and end_time from events

Revision ID: b6c4a92e0758
Revises: a8d3e1b5f927
Create Date: 2026-05-26 14:00:00.000000

``events`` represents a season-spanning, region-scoped tournament
grouping. A single ``start_time`` / ``end_time`` pair doesn't carry
useful meaning at that granularity — the per-window times on
``event_windows`` and the per-match times on ``matches`` are the
authoritative time fields. Dropping these columns aligns the schema
with the model, which no longer declares them.

The downgrade re-adds the columns as nullable timestamps. Any values
that were in them before this migration are not recoverable from the
downgrade alone — they would need to be re-derived (e.g., from
``event_windows.start_time`` / ``end_time``) by a separate backfill if
you ever need them back.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6c4a92e0758"
down_revision: Union[str, Sequence[str], None] = "a8d3e1b5f927"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS start_time")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS end_time")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "events",
        sa.Column("start_time", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("end_time", sa.DateTime(), nullable=True),
    )
