import argparse
import traceback
from datetime import datetime, timezone

from sqlalchemy import select

import etl.db.loader as db_loader
import etl.storage.loader as s3_loader

from etl.fetching.match_data_fetching import (
    ensure_match_raw,
    ensure_event_window_raw,
    ensure_event_window_leaderboard_raw,
)
from etl.parsing.event_parsing import (
    parse_event_metadata,
    parse_event_window_matches,
    parse_event_window_metadata,
    parse_tournament_metadata,
)
from etl.parsing.leaderboard import build_leaderboard_rows, build_leaderboard_player_rows
from etl.parsing.tournament_classification import get_region_code

from etl.orch.runner import process_match_relational, process_match_timeline
from etl.orch.registry import STATS, TIMELINE_NAME, TIMELINE_VERSION, desired_versions

from etl.db.session import get_session
from etl.db.models import EventWindow, Match
from etl.db.status import (
    mark_event_window_failed,
    mark_event_window_started,
    mark_event_window_succeeded,
    mark_match_failed,
    mark_match_started,
    mark_match_succeeded,
    read_stat_status,
    mark_stat_processed,
    mark_stat_failed,
)
from etl.types import RawEventWindowData


def get_existing_event_window_ids() -> list[str]:
    with get_session() as session:
        return list(
            session.scalars(
                select(EventWindow.event_window_id).order_by(EventWindow.event_window_id)
            )
        )


def process_match(match_id: str, event_window_id: str, force: bool = False) -> bool:
    """Reconcile one match: materialize only the assets whose version is stale.

    Compares each asset's current version (:func:`desired_versions`) against
    what ``match_stat_status`` records for this match and (re)materializes only
    the difference. ``force=True`` treats every asset as stale. A fully-current
    match is a cheap no-op — one indexed status read, no raw fetch.
    """
    desired = desired_versions()
    with get_session() as session:
        actual = read_stat_status(match_id, session)

    stale = {
        name
        for name, version in desired.items()
        if force or actual.get(name) is None or actual and actual[name] < version
    }
    if not stale:
        print(f"⏭️  Match {match_id} already current, skipping")
        return True

    print(f"\n{'='*60}\nReconciling match {match_id}: {sorted(stale)}\n{'='*60}\n")

    try:
        raw = ensure_match_raw(match_id)
    except Exception as e:
        print(f"❌ Failed to fetch raw for match {match_id}: {e}")
        traceback.print_exc()
        return False

    relational_stale = stale & set(STATS)
    ok = True

    # Phase A — relational stats, one transaction (atomic batch).
    if relational_stale:
        try:
            now = datetime.now(timezone.utc)
            with get_session() as session, session.begin():
                match = process_match_relational(
                    raw, event_window_id, session, relational_stale
                )
                mark_match_started(match, now)
                for name in relational_stale:
                    mark_stat_processed(match_id, name, STATS[name].version, session, now)
        except Exception as e:
            ok = False
            print(f"❌ Relational load failed for match {match_id}: {e}")
            traceback.print_exc()
            _record_stat_failures(match_id, relational_stale)

    # Phase B — timeline asset, NO db transaction open (slow S3 upload).
    if TIMELINE_NAME in stale:
        try:
            process_match_timeline(raw, event_window_id)
            with get_session() as session, session.begin():
                mark_stat_processed(
                    match_id,
                    TIMELINE_NAME,
                    TIMELINE_VERSION,
                    session,
                    datetime.now(timezone.utc),
                )
        except Exception as e:
            ok = False
            print(f"❌ Timeline upload failed for match {match_id}: {e}")
            traceback.print_exc()
            try:
                deleted = s3_loader.cleanup_match_timeline(match_id)
                if deleted:
                    print(f"Cleaned up {deleted} orphaned movement chunks for {match_id}")
            except Exception as cleanup_err:
                print(f"⚠️  Failed to clean up movement chunks for {match_id}: {cleanup_err}")
            _record_stat_failures(match_id, {TIMELINE_NAME})

    # Roll up the coarse Match.status for reporting / the website.
    with get_session() as session, session.begin():
        match = session.get(Match, match_id)
        if match is not None:
            mark = mark_match_succeeded if ok else mark_match_failed
            mark(match, datetime.now(timezone.utc))

    if ok:
        print(f"\n✅ Reconciled match {match_id}: {sorted(stale)}\n")
    return ok


def _record_stat_failures(match_id: str, names) -> None:
    """Best-effort per-stat failure marking. No-op if the Match row does not
    exist yet (a brand-new match whose creation rolled back), since
    ``match_stat_status`` has a foreign key to ``matches``.
    """
    try:
        now = datetime.now(timezone.utc)
        with get_session() as session, session.begin():
            if session.get(Match, match_id) is None:
                return
            for name in names:
                mark_stat_failed(match_id, name, session, now)
    except Exception as e:
        print(f"⚠️  Could not record stat failures for {match_id}: {e}")


def ingest_event_window_metadata(
    event_window_id: str,
    raw: RawEventWindowData | None = None,
    refresh: bool = False,
) -> None:
    """Upsert Event, Tournament, and EventWindow rows for *event_window_id*.

    Performs no match-level work and does not touch processing-status
    columns. Use this when you want to refresh the derived classification
    fields (region, season, tournament link, title, day_index) across
    many event windows without re-ingesting their matches.

    ``raw`` is accepted as an optimization: callers that already have the
    raw payload in hand can skip the second S3 round trip by passing it.
    ``refresh`` only applies when ``raw`` is not supplied — see
    :func:`ensure_event_window_raw`.
    """
    print(f"Ingesting metadata for event window: {event_window_id}")

    if raw is None:
        raw = ensure_event_window_raw(event_window_id, refresh=refresh)

    parsed_event = parse_event_metadata(raw)
    parsed_tournament = parse_tournament_metadata(raw)
    parsed_event_window = parse_event_window_metadata(raw, parsed_tournament)

    # Parents before children so future FKs (events ← event_windows,
    # tournaments ← event_windows) are satisfied at flush time.
    with get_session() as session, session.begin():
        db_loader.load_event(parsed_event, session)
        if parsed_tournament is not None:
            db_loader.load_tournament(parsed_tournament, session)
        db_loader.load_event_window_metadata(parsed_event_window, session)

    print(f"✅ Metadata ingested for {event_window_id}")


def ingest_event_window_leaderboard(
    event_window_id: str,
    refresh: bool = False,
) -> None:
    """Fetch, build, and load the tournament leaderboard for *event_window_id*.

    Mirrors :func:`ingest_event_window_metadata`: a self-contained window-level
    ingest. Player flags (``event_window_players``) load from the leaderboard
    entries unconditionally; team standings (``event_window_teams`` /
    ``event_window_team_matches``) additionally need scoring rules and are
    skipped — without failing — for windows aged out of fnapi's listing.
    Independent of match processing — the per-game stats come pre-aggregated
    from the leaderboard endpoint.
    """
    print(f"Ingesting leaderboard for event window: {event_window_id}")

    region = get_region_code(event_window_id)
    raw = ensure_event_window_leaderboard_raw(event_window_id, region, refresh=refresh)

    # Player flags come from the leaderboard entries, which are cached even for
    # windows whose scoring rules have aged out — so load them unconditionally.
    player_rows = build_leaderboard_player_rows(raw.entries, event_window_id)
    with get_session() as session, session.begin():
        db_loader.load_event_window_players(player_rows, event_window_id, session)

    # Team standings need scoring rules; skip them (without failing) when the
    # window has aged out of fnapi's listing and has no scoring.json.
    if raw.scoring_rules is None:
        print(
            f"✅ Player flags ingested for {event_window_id}: {len(player_rows)} "
            f"players (no scoring rules — team standings skipped)"
        )
        return

    team_rows, match_rows = build_leaderboard_rows(
        raw.entries, raw.scoring_rules, event_window_id, raw.match_point_rule
    )
    with get_session() as session, session.begin():
        db_loader.load_event_window_teams(team_rows, event_window_id, session)
        db_loader.load_event_window_team_matches(match_rows, event_window_id, session)

    print(
        f"✅ Leaderboard ingested for {event_window_id}: "
        f"{len(team_rows)} teams, {len(match_rows)} team-matches, "
        f"{len(player_rows)} players"
    )


def process_event_window(
    event_window_id: str,
    force: bool = False,
    refresh: bool = False,
) -> dict:
    """Full pipeline: metadata ingestion followed by per-match reconciliation.

    There is no coarse "already processed" short-circuit — every match is
    handed to the reconciler, which cheaply skips the ones whose assets are all
    current. That is what lets a re-run pick up a newly-added stat or a version
    bump without touching everything else.

    ``force`` and ``refresh`` are orthogonal: ``force`` re-materializes stats
    for matches we already know about, ``refresh`` re-pulls the window's match
    list from the API in case it has grown (a live event).
    """
    print(f"\n{'#'*60}")
    print(f"Processing Event Window: {event_window_id}")
    print(f"{'#'*60}\n")

    started_at = datetime.now(timezone.utc)

    # Fetch raw once; reuse for both metadata ingestion and match iteration.
    raw = ensure_event_window_raw(event_window_id, refresh=refresh)

    # Phase 1 — metadata. Same code path the backfill uses.
    ingest_event_window_metadata(event_window_id, raw=raw)

    # Phase 2 — mark the EventWindow as processing-started now that the row
    # is guaranteed to exist.
    with get_session() as session, session.begin():
        event_window = session.get_one(EventWindow, event_window_id)
        mark_event_window_started(event_window, started_at)

    # Phase 3 — leaderboard standings, before match processing. Independent of
    # match parsing (the per-game stats come pre-aggregated from the leaderboard
    # endpoint), so a failure here — e.g. a window fnapi doesn't list — is logged
    # but does not fail the window's match processing.
    try:
        ingest_event_window_leaderboard(event_window_id, refresh=refresh)
    except LookupError as leaderboard_error:
        # Event older than fnapi's rolling listing — no leaderboard to ingest.
        print(f"⏭️  No fnapi leaderboard for {event_window_id}: {leaderboard_error}")
    except Exception as leaderboard_error:
        print(f"⚠️  Leaderboard ingest failed for {event_window_id}: {leaderboard_error}")
        traceback.print_exc()

    # Phase 4 — matches.
    matches = parse_event_window_matches(raw)
    results = {"total": len(matches), "successful": 0, "failed": 0}

    try:
        for match in matches:
            match_id = match["info"]["matchId"]
            success = process_match(match_id, event_window_id, force=force)
            results["successful" if success else "failed"] += 1

        completed_at = datetime.now(timezone.utc)
        with get_session() as session, session.begin():
            event_window = session.get_one(EventWindow, event_window_id)
            event_window.processed_matches = results["successful"]
            if results["failed"] == 0:
                mark_event_window_succeeded(event_window, completed_at)
            else:
                mark_event_window_failed(event_window, completed_at)

        return results

    except Exception as e:
        with get_session() as session, session.begin():
            event_window = session.get(EventWindow, event_window_id)
            if event_window is not None:
                event_window.processed_matches = results["successful"]
                mark_event_window_failed(event_window, datetime.now(timezone.utc))

        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    # Reconcile a set of event windows. With the reconciler, --force off only
    # (re)materializes stale/new assets; every already-current match is skipped.
    parser = argparse.ArgumentParser(
        description="Reconcile a hardcoded set of event windows into the database."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Re-fetch each window's info/matches from Osirion, ignoring (and "
            "overwriting) the S3 cache. Use during a live event, whose match "
            "list is still growing."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-materialize every stat for every match, even if already current.",
    )
    args = parser.parse_args()

    """
    event_window_ids: list[str] = [
        "S33_FNCSMajor1_Final_Day1_EU",
        "S33_FNCSMajor1_Final_Day2_EU",
        "S33_FNCSMajor1_Final_Day1_NAC",
        "S33_FNCSMajor1_Final_Day2_NAC",
        "S34_FNCSMajor2_Final_Day1_EU",
        "S34_FNCSMajor2_Final_Day2_EU",
        "S34_FNCSMajor2_Final_Day1_NAC",
        "S34_FNCSMajor2_Final_Day2_NAC",
        "S36_FNCSMajor3_Final_Day1_EU",
        "S36_FNCSMajor3_Final_Day2_EU",
        "S36_FNCSMajor3_Final_Day1_NAC",
        "S36_FNCSMajor3_Final_Day2_NAC",
        "Dinosauron_Day1",
        "Dinosauron_Day2",
        "S39_FNCSDivisionalCup_Division1_Week6Final_EU",
        "S39_FNCSDivisionalCup_Division1_Week6Final_NAC",
        "S40_FNCSMajor1_Final_Day1_EU",
        "S40_FNCSMajor1_Final_Day2_EU",
        "S40_FNCSMajor1_Final_Day1_NAC",
        "S40_FNCSMajor1_Final_Day2_NAC",
        "S40_FNCSDivisionalCup_Division1_Week3Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week3Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week4Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week4Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week5Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week5Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week2Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week2Final_NAC",
        "Bratwurst_Finals_Day3",
        "S41_PerformanceEvaluation_Event1Round2_EU",
        "S41_PerformanceEvaluation_Event1Round2_NAC",
        "S41_FNCSDivisionalCup_Division1_Week1Final_EU",
        "S41_FNCSDivisionalCup_Division1_Week1Final_NAC",
        "S41_PerformanceEvaluation_Event2Round2_EU",
        "S41_PerformanceEvaluation_Event2Round2_NAC",
        "S41_FNCSDivisionalCup_Division1_Week2Final_EU",
        "S41_FNCSDivisionalCup_Division1_Week2Final_NAC",
        "S41_FNCSDivisionalCup_Division1_Week3Final_EU",
        "S41_FNCSDivisionalCup_Division1_Week3Final_NAC",
        "S41_PerformanceEvaluation_Event4Round2_NAC",
        "S41_PerformanceEvaluation_Event4Round2_EU",
        "S41_FNCSDivisionalCup_Division1_Week4Final_NAC",
        "S41_FNCSDivisionalCup_Division1_Week4Final_EU",
        "S41_PerformanceEvaluation_Event5Round2_EU",
        "S41_PerformanceEvaluation_Event5Round2_NAC",
        "S41_PerformanceEvaluation_Event6Round2_EU",
        "S41_PerformanceEvaluation_Event6Round2_NAC",
        "S41_FNCSMajor2_Final_Day1_EU",
        "S41_FNCSMajor2_Final_Day1_NAC",
        "S41_FNCSMajor2_Final_Day2_EU",
        "S41_FNCSMajor2_Final_Day2_NAC",
        "S41_PerformanceEvaluation_Event9Round2_EU"
        "S41_PerformanceEvaluation_Event9Round2_NAC",
        "S41_FNCSLastChanceMajor_Final_EU"
        "S41_FNCSLastChanceMajor_Final_NAC"
        "S39_ReloadEliteSeries1Final_NAC",
        "S39_ReloadEliteSeries2Final_NAC",
        "S40_ReloadEliteSeries3Final_NAC",
        "S41_ReloadEliteSeries4Final_NAC",
        "S39_ReloadEliteSeries1Final_EU",
        "S39_ReloadEliteSeries2Final_EU",
        "S40_ReloadEliteSeries3Final_EU",
        "S41_ReloadEliteSeries4Final_EU",
        "Escargo_Day4", # EWC Final,
        "S42_PerformanceEvaluation_Event1Round2_NAC",
        "S42_PerformanceEvaluation_Event1Round2_EU",
        "S42_FNCSDivisionalCup_Division1_Week1Final_EU",
        "S42_FNCSDivisionalCup_Division1_Week1Final_NAC",
        "S42_PerformanceEvaluation_Event2Round2_EU",
        "S42_PerformanceEvaluation_Event2Round2_NAC",
        "S42_FNCSDivisionalCup_Division1_Week2Final_EU",
        "S42_FNCSDivisionalCup_Division1_Week2Final_NAC",
        "S42_PerformanceEvaluation_Event3Round2_EU",
        "S42_PerformanceEvaluation_Event3Round2_NAC",
    ]
    """

    event_window_ids: list[str] = [
    ]
    
    statuses: dict[str, dict] = {}
    for event_window_id in event_window_ids:
        statuses[event_window_id] = process_event_window(
            event_window_id, force=args.force, refresh=args.refresh
        )

    print("\nReconcile summary:")
    for event_window_id, status in statuses.items():
        print(f"- {event_window_id}: {status}")
