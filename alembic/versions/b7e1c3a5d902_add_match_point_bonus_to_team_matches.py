"""add match_point_bonus to event_window_team_matches

Revision ID: b7e1c3a5d902
Revises: c7e2f1a4b9d0
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7e1c3a5d902"
down_revision: Union[str, Sequence[str], None] = "c7e2f1a4b9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "event_window_team_matches",
        sa.Column(
            "match_point_bonus",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("event_window_team_matches", "match_point_bonus")
