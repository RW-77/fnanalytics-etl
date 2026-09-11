from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from etl.db.models import EventWindow, Match, MatchStatStatus


STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"


def mark_event_window_started(event_window: EventWindow, now: datetime) -> None:
    event_window.status = STATUS_PROCESSING
    event_window.last_processing_start = now


def mark_event_window_succeeded(event_window: EventWindow, now: datetime) -> None:
    event_window.status = STATUS_PROCESSED
    event_window.last_processed = now


def mark_event_window_failed(event_window: EventWindow, now: datetime) -> None:
    event_window.status = STATUS_FAILED
    event_window.last_failed = now


def mark_match_started(match: Match, now: datetime) -> None:
    match.status = STATUS_PROCESSING
    match.last_processing_start = now


def mark_match_succeeded(match: Match, now: datetime) -> None:
    match.status = STATUS_PROCESSED
    match.last_processed = now


def mark_match_failed(match: Match, now: datetime) -> None:
    match.status = STATUS_FAILED
    match.last_failed = now


def read_stat_status(match_id: str, session: Session) -> dict[str, int | None]:
    """Return ``{stat_name: parser_version}`` for a match's materialized stats.

    The reconciler treats a stat as stale when it is absent from this map, its
    value is ``None`` (attempted but never succeeded), or its value is below the
    registry's current version for that stat.
    """
    rows = session.execute(
        select(MatchStatStatus.stat_name, MatchStatStatus.parser_version)
        .where(MatchStatStatus.match_id == match_id)
    ).all()
    return {stat_name: parser_version for stat_name, parser_version in rows}


def mark_stat_processed(
    match_id: str,
    stat_name: str,
    parser_version: int,
    session: Session,
    now: datetime,
) -> None:
    """Upsert a (match, stat) row as processed at ``parser_version``."""
    stmt = pg_insert(MatchStatStatus).values(
        match_id=match_id,
        stat_name=stat_name,
        parser_version=parser_version,
        status=STATUS_PROCESSED,
        last_processed=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MatchStatStatus.match_id, MatchStatStatus.stat_name],
        set_={
            "parser_version": stmt.excluded.parser_version,
            "status": stmt.excluded.status,
            "last_processed": stmt.excluded.last_processed,
        },
    )
    session.execute(stmt)


def mark_stat_failed(
    match_id: str,
    stat_name: str,
    session: Session,
    now: datetime,
) -> None:
    """Record a failed (match, stat) attempt.

    ``parser_version`` is deliberately left untouched — NULL on a first-ever
    attempt, otherwise the last successful version — so the reconciler keeps
    seeing the stat as stale and retries it on the next run.
    """
    stmt = pg_insert(MatchStatStatus).values(
        match_id=match_id,
        stat_name=stat_name,
        parser_version=None,
        status=STATUS_FAILED,
        last_failed=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MatchStatStatus.match_id, MatchStatStatus.stat_name],
        set_={
            "status": stmt.excluded.status,
            "last_failed": stmt.excluded.last_failed,
        },
    )
    session.execute(stmt)
