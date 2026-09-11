"""add_mode_id_to_matches

Revision ID: f3a9c1d47b52
Revises: b7e1c3a5d902
Create Date: 2026-08-26 00:00:00.000000

Adds ``matches.mode_id`` — the fnapi ``/v1/maps`` mode id for a match's map,
resolved from ``map_path`` at ingest (see ``etl/parsing/map_modes.py``). It
completes the join to ``maps(build_major, build_minor, mode_id)`` so the replay
viewer can find per-map assets instead of assuming BR.

Existing rows are backfilled here so already-processed tournaments link without
a reprocess: the two competitive Reload maps by their (build-stable) mapPath,
everything else to ``'br'`` — the historical default. These values mirror
``map_modes.MAP_PATH_TO_MODE_ID``; keep them in sync if that lookup changes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c1d47b52'
down_revision: Union[str, Sequence[str], None] = 'b7e1c3a5d902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('mode_id', sa.String(50), nullable=True))

    op.execute(
        "UPDATE matches SET mode_id = 'reload-elitestronghold' "
        "WHERE map_path = '/dcab7512-4874-bbe0-f383-9ab040fcd9fc/MatchMist'"
    )
    op.execute(
        "UPDATE matches SET mode_id = 'reload-slurp' "
        "WHERE map_path = '/f4032749-42c4-7fe9-7fa2-c78076f34f54/DashBerry'"
    )
    op.execute("UPDATE matches SET mode_id = 'br' WHERE mode_id IS NULL")


def downgrade() -> None:
    op.drop_column('matches', 'mode_id')
