"""migrate status schema

Revision ID: f5d71454a332
Revises: ff0f0922aa66
Create Date: 2026-05-10 21:17:53.745286

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5d71454a332'
down_revision: Union[str, Sequence[str], None] = 'ff0f0922aa66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "event_windows",
        "discovered_at",
        new_column_name="created_at",
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )

    op.add_column(
        "event_windows",
        sa.Column("status", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE event_windows
        SET status = CASE
            WHEN failed THEN 'failed'
            WHEN processed THEN 'processed'
            WHEN processing THEN 'processing'
            ELSE 'pending'
        END
        """
    )
    op.alter_column(
        "event_windows",
        "status",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.drop_column("event_windows", "processing")
    op.drop_column("event_windows", "processed")
    op.drop_column("event_windows", "failed")

    op.add_column(
        "matches",
        sa.Column("status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("last_processing_start", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("last_processed", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "matches",
        sa.Column("last_failed", sa.DateTime(), nullable=True),
    )
    op.execute(
        """
        UPDATE matches
        SET status = CASE
            WHEN failed THEN 'failed'
            WHEN processed THEN 'processed'
            WHEN processing THEN 'processing'
            ELSE 'pending'
        END
        """
    )
    op.alter_column(
        "matches",
        "status",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "matches",
        "event_window_id",
        existing_type=sa.String(length=50),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
    op.alter_column(
        "matches",
        "duration",
        existing_type=sa.DateTime(),
        type_=sa.Interval(),
        existing_nullable=True,
        postgresql_using=(
            "CASE "
            "WHEN duration IS NULL THEN NULL "
            "ELSE interval '1 second' * (extract(epoch from duration) / 1000.0) "
            "END"
        ),
    )
    op.drop_column("matches", "processing")
    op.drop_column("matches", "processed")
    op.drop_column("matches", "failed")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "matches",
        sa.Column("failed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "matches",
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "matches",
        sa.Column("processing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE matches
        SET
            processing = (status = 'processing'),
            processed = (status = 'processed'),
            failed = (status = 'failed')
        """
    )
    op.alter_column(
        "matches",
        "duration",
        existing_type=sa.Interval(),
        type_=sa.DateTime(),
        existing_nullable=True,
        postgresql_using=(
            "CASE "
            "WHEN duration IS NULL THEN NULL "
            "ELSE to_timestamp(extract(epoch from duration) * 1000.0) "
            "END"
        ),
    )
    op.alter_column(
        "matches",
        "event_window_id",
        existing_type=sa.String(length=100),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
    op.drop_column("matches", "last_failed")
    op.drop_column("matches", "last_processed")
    op.drop_column("matches", "last_processing_start")
    op.drop_column("matches", "status")
    op.alter_column("matches", "processing", server_default=None)
    op.alter_column("matches", "processed", server_default=None)
    op.alter_column("matches", "failed", server_default=None)

    op.add_column(
        "event_windows",
        sa.Column("failed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "event_windows",
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "event_windows",
        sa.Column("processing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE event_windows
        SET
            processing = (status = 'processing'),
            processed = (status = 'processed'),
            failed = (status = 'failed')
        """
    )
    op.drop_column("event_windows", "status")
    op.alter_column(
        "event_windows",
        "created_at",
        new_column_name="discovered_at",
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )
    op.alter_column("event_windows", "processing", server_default=None)
    op.alter_column("event_windows", "processed", server_default=None)
    op.alter_column("event_windows", "failed", server_default=None)
