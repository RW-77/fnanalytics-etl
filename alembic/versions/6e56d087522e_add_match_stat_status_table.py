"""add match_stat_status table

Revision ID: 6e56d087522e
Revises: a4431bea9c5d
Create Date: 2026-07-03 11:32:07.653991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e56d087522e'
down_revision: Union[str, Sequence[str], None] = 'a4431bea9c5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "match_stat_status",
        sa.Column("match_id", sa.String(length=50), nullable=False),
        sa.Column("stat_name", sa.String(length=50), nullable=False),
        sa.Column("parser_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_processed", sa.DateTime(), nullable=True),
        sa.Column("last_failed", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["match_id"], ["matches.match_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("match_id", "stat_name"),
    )
    op.create_index("idx_mss_stat", "match_stat_status", ["stat_name"])

    # Seed existing processed matches as "done at v1" for every current asset
    # (6 relational stats + timeline) so the reconciler treats them as current
    # instead of reprocessing everything — including re-uploading every timeline.
    # All processed matches were verified to have all six relational stats.
    op.execute(
        """
        INSERT INTO match_stat_status (match_id, stat_name, parser_version, status, last_processed)
        SELECT m.match_id, a.stat_name, 1, 'processed', m.last_processed
        FROM matches m
        CROSS JOIN (VALUES
            ('damage'), ('elims'), ('shots'),
            ('damage_contribution'), ('assists'), ('shot_attempts'),
            ('timeline')
        ) AS a(stat_name)
        WHERE m.status = 'processed'
        ON CONFLICT (match_id, stat_name) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_mss_stat", table_name="match_stat_status")
    op.drop_table("match_stat_status")
