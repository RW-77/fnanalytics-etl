"""add_build_version_to_matches_and_maps_table

Revision ID: 8a4f2c1d9b7e
Revises: f55049c4af42
Create Date: 2026-06-09 00:00:00.000000

What this migration does
------------------------
1. Adds ``build_major`` and ``build_minor`` (nullable Integer) to ``matches``.
   These are populated by reprocessing existing matches or by the patch script
   and are used to look up the correct map assets for the replay viewer.

2. Creates the ``maps`` catalog table.
   One row per (build_major, build_minor, mode_id) that has been successfully
   mirrored to S3 by ``etl/jobs/sync_maps.py``. The primary purpose is a fast
   "do we have assets for this match?" check — the replay viewer queries this
   before attempting to render, so it can display a graceful error instead of
   a broken canvas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a4f2c1d9b7e'
down_revision: Union[str, Sequence[str], None] = 'f55049c4af42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add build version columns to matches
    # ------------------------------------------------------------------
    op.add_column('matches', sa.Column('build_major', sa.Integer(), nullable=True))
    op.add_column('matches', sa.Column('build_minor', sa.Integer(), nullable=True))

    # ------------------------------------------------------------------
    # 2. Create maps catalog table
    # ------------------------------------------------------------------
    op.create_table(
        'maps',
        sa.Column('build_major',     sa.Integer(),      nullable=False),
        sa.Column('build_minor',     sa.Integer(),      nullable=False),
        sa.Column('mode_id',         sa.String(50),     nullable=False),
        sa.Column('image_key',       sa.String(300),    nullable=True),
        sa.Column('definition_key',  sa.String(300),    nullable=True),
        sa.Column('synced_at',       sa.DateTime(),     nullable=False),
        sa.PrimaryKeyConstraint('build_major', 'build_minor', 'mode_id'),
    )
    op.create_index('idx_map_build', 'maps', ['build_major', 'build_minor'])


def downgrade() -> None:
    # ------------------------------------------------------------------
    # 2. Drop maps table
    # ------------------------------------------------------------------
    op.drop_index('idx_map_build', table_name='maps')
    op.drop_table('maps')

    # ------------------------------------------------------------------
    # 1. Remove build version columns from matches
    # ------------------------------------------------------------------
    op.drop_column('matches', 'build_minor')
    op.drop_column('matches', 'build_major')
