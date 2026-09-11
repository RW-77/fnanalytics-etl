"""add_event_window_players_table

Revision ID: e9d4c2b7a1f3
Revises: a2c4e6b80d13
Create Date: 2026-08-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9d4c2b7a1f3'
down_revision: Union[str, Sequence[str], None] = 'a2c4e6b80d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('event_window_players',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_window_id', sa.String(length=100), nullable=False),
    sa.Column('epic_id', sa.String(length=100), nullable=False),
    sa.Column('epic_username', sa.String(length=100), nullable=False),
    sa.Column('flag_token', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['event_window_id'], ['event_windows.event_window_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ewp_epic', 'event_window_players', ['epic_id'], unique=False)
    op.create_index('idx_ewp_window', 'event_window_players', ['event_window_id'], unique=False)
    op.create_index('idx_ewp_window_epic', 'event_window_players', ['event_window_id', 'epic_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_ewp_window_epic', table_name='event_window_players')
    op.drop_index('idx_ewp_window', table_name='event_window_players')
    op.drop_index('idx_ewp_epic', table_name='event_window_players')
    op.drop_table('event_window_players')
