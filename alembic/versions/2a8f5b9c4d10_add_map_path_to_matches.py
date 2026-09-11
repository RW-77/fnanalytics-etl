"""add map_path to matches

Revision ID: 2a8f5b9c4d10
Revises: f5d71454a332
Create Date: 2026-05-13 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2a8f5b9c4d10"
down_revision: Union[str, Sequence[str], None] = "f5d71454a332"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("matches", sa.Column("map_path", sa.String(), nullable=True))
    op.alter_column(
        "matches",
        "start_time",
        existing_type=sa.DateTime(),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "matches",
        "start_time",
        existing_type=sa.DateTime(),
        nullable=False,
    )
    op.drop_column("matches", "map_path")
