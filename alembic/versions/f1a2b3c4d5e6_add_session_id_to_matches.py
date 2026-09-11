"""add_session_id_to_matches

Revision ID: f1a2b3c4d5e6
Revises: d7c3f9a21b84
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'd7c3f9a21b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable: existing rows have no value until backfilled from the S3 info
    # logs (jobs/backfill_match_session_id.py), and non-server-recorded matches
    # may legitimately lack a serverId.
    op.add_column('matches', sa.Column('session_id', sa.String(length=50), nullable=True))
    op.create_index('idx_match_session', 'matches', ['session_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_match_session', table_name='matches')
    op.drop_column('matches', 'session_id')
