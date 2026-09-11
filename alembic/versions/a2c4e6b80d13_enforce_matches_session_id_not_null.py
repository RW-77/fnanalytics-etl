"""enforce_matches_session_id_not_null

Revision ID: a2c4e6b80d13
Revises: f1a2b3c4d5e6
Create Date: 2026-08-12 00:00:00.000000

Enforce NOT NULL on matches.session_id. Run only after
``python -m etl.jobs.backfill_match_session_id`` has filled existing rows — the
guard below fails with a clear message if any row is still NULL, rather than
letting the ALTER error out cryptically.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2c4e6b80d13'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    null_count = conn.execute(
        sa.text("SELECT count(*) FROM matches WHERE session_id IS NULL")
    ).scalar()
    if null_count:
        raise RuntimeError(
            f"{null_count} matches row(s) have NULL session_id. Run "
            "`python -m etl.jobs.backfill_match_session_id` before enforcing NOT NULL."
        )
    op.alter_column(
        'matches', 'session_id', existing_type=sa.String(length=50), nullable=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'matches', 'session_id', existing_type=sa.String(length=50), nullable=True
    )
