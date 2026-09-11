"""Backfill ``Match.session_id`` from cached match info logs.

``session_id`` (the info log's ``serverId``) is set at ingest for matches
processed after the column was added, but existing rows whose assets are all
current are skipped by the reconciler and never re-parsed. This one-off job
fills them in by reading each match's cached info log from S3 — no API calls and
no stat reprocessing.
"""

from sqlalchemy import select, update

from etl.db.session import get_session
from etl.db.models import Match
from etl.storage.s3_client import S3TournamentLogStore
from etl.fetching.match_data_fetching import LOGS_BUCKET


def backfill_match_session_id() -> None:
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    with get_session() as session:
        match_ids = list(
            session.scalars(select(Match.match_id).where(Match.session_id.is_(None)))
        )

    print(f"Backfilling session_id for {len(match_ids)} matches...")

    updates: list[tuple[str, str]] = []
    for match_id in match_ids:
        if not bucket.contains_match_log(match_id, "info"):
            print(f"⚠️  No info log for {match_id}; skipping")
            continue
        server_id = bucket.get_match_log(match_id, "info").get("serverId")
        if server_id:
            updates.append((match_id, server_id))

    if updates:
        with get_session() as session, session.begin():
            for match_id, session_id in updates:
                session.execute(
                    update(Match)
                    .where(Match.match_id == match_id)
                    .values(session_id=session_id)
                )

    print(f"✅ Backfilled session_id for {len(updates)} matches")


if __name__ == "__main__":
    backfill_match_session_id()
