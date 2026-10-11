"""add match event counts table

Revision ID: 38375fccc0f0
Revises: b0dcd51bc3d6
Create Date: 2026-10-10 22:30:09.191098

Adds ``match_event_counts`` — how many events each of a match's raw logs holds,
one row per (match, log) (the ``event_counts`` registry stat). The website sums
it for the homepage's all-time event total. Starts empty; the reconciler fills
it (``process_tournaments --all`` for every match already ingested).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38375fccc0f0'
down_revision: Union[str, Sequence[str], None] = 'b0dcd51bc3d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('match_event_counts',
    sa.Column('match_id', sa.String(length=50), nullable=False),
    sa.Column('log_type', sa.String(length=50), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['match_id'], ['matches.match_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('match_id', 'log_type')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('match_event_counts')
