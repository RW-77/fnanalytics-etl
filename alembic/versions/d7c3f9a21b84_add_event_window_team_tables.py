"""add_event_window_team_tables

Revision ID: d7c3f9a21b84
Revises: 3b60b8768696
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7c3f9a21b84'
down_revision: Union[str, Sequence[str], None] = '3b60b8768696'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('event_window_teams',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_window_id', sa.String(length=100), nullable=False),
    sa.Column('team_id', sa.String(length=200), nullable=False),
    sa.Column('team_key', sa.String(length=200), nullable=False),
    sa.Column('final_rank', sa.Integer(), nullable=False),
    sa.Column('final_points', sa.Integer(), nullable=False),
    sa.Column('final_score', sa.BigInteger(), nullable=False),
    sa.Column('percentile', sa.Float(), nullable=False),
    sa.Column('matches', sa.Integer(), nullable=False),
    sa.Column('wins', sa.Integer(), nullable=False),
    sa.Column('kills', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['event_window_id'], ['event_windows.event_window_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ewt_team_key', 'event_window_teams', ['team_key'], unique=False)
    op.create_index('idx_ewt_window', 'event_window_teams', ['event_window_id'], unique=False)
    op.create_index('idx_ewt_window_team', 'event_window_teams', ['event_window_id', 'team_id'], unique=True)

    op.create_table('event_window_team_matches',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('event_window_id', sa.String(length=100), nullable=False),
    sa.Column('team_id', sa.String(length=200), nullable=False),
    sa.Column('session_id', sa.String(length=50), nullable=False),
    sa.Column('game_number', sa.Integer(), nullable=False),
    sa.Column('end_time', sa.DateTime(), nullable=False),
    sa.Column('placement', sa.Integer(), nullable=True),
    sa.Column('team_elims', sa.Integer(), nullable=False),
    sa.Column('victory_royale', sa.Boolean(), nullable=False),
    sa.Column('time_alive', sa.Integer(), nullable=True),
    sa.Column('placement_tiebreaker', sa.Integer(), nullable=True),
    sa.Column('placement_points', sa.Integer(), nullable=False),
    sa.Column('elim_points', sa.Integer(), nullable=False),
    sa.Column('total_points', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['event_window_id'], ['event_windows.event_window_id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_ewtm_session', 'event_window_team_matches', ['session_id'], unique=False)
    op.create_index('idx_ewtm_team', 'event_window_team_matches', ['team_id'], unique=False)
    op.create_index('idx_ewtm_window', 'event_window_team_matches', ['event_window_id'], unique=False)
    op.create_index('idx_ewtm_window_team_session', 'event_window_team_matches', ['event_window_id', 'team_id', 'session_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_ewtm_window_team_session', table_name='event_window_team_matches')
    op.drop_index('idx_ewtm_window', table_name='event_window_team_matches')
    op.drop_index('idx_ewtm_team', table_name='event_window_team_matches')
    op.drop_index('idx_ewtm_session', table_name='event_window_team_matches')
    op.drop_table('event_window_team_matches')
    op.drop_index('idx_ewt_window_team', table_name='event_window_teams')
    op.drop_index('idx_ewt_window', table_name='event_window_teams')
    op.drop_index('idx_ewt_team_key', table_name='event_window_teams')
    op.drop_table('event_window_teams')
