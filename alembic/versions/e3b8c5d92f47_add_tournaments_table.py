"""add tournaments table

Revision ID: e3b8c5d92f47
Revises: d2a4b6c8e0f2
Create Date: 2026-05-25 12:00:00.000000

Introduces a ``tournaments`` table that holds per-tournament metadata
not derivable from the event-window ID alone — primarily the
human-readable ``title``. The loader upserts rows here via
``INSERT … ON CONFLICT DO NOTHING`` so manual edits stick across
re-ingests.

No foreign key from ``event_windows.tournament_id`` for now; the column
remains a free string. Add the FK in a follow-up migration once the
catalog is well-populated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3b8c5d92f47"
down_revision: Union[str, Sequence[str], None] = "d2a4b6c8e0f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tournaments",
        sa.Column("tournament_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("season_code", sa.String(length=4), nullable=True),
        sa.PrimaryKeyConstraint("tournament_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("tournaments")
