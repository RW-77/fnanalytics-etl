"""make event_window_players.epic_username nullable

The tournament leaderboard endpoint reports ``username: null`` for some listed
accounts, so the per-window snapshot must allow a missing display name. The
name can still be resolved from ``match_players`` by ``epic_id`` when needed.

Revision ID: c7e2f1a4b9d0
Revises: e9d4c2b7a1f3
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2f1a4b9d0'
down_revision: Union[str, Sequence[str], None] = 'e9d4c2b7a1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'event_window_players',
        'epic_username',
        existing_type=sa.String(length=100),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'event_window_players',
        'epic_username',
        existing_type=sa.String(length=100),
        nullable=False,
    )
