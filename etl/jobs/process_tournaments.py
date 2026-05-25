from datetime import datetime, timezone
from sqlalchemy import select
import etl.db.loader as db_loader
import etl.storage.loader as s3_loader
import etl.db.schema as schema

from etl.fetching.match_data_fetching import ensure_match_raw, ensure_event_window_raw
from etl.parsing.match_parsing import parse_match_relational, parse_match_timeline
from etl.parsing.event_parsing import parse_event_window_metadata, parse_event_window_matches
from etl.parsing.basic import parse_match_metadata
from etl.parsing.tournament_classification import (
    classify_event_window_id,
    parse_event_window_attributes,
)
from etl.parsing.tournament_metadata import resolve_tournament_metadata

from etl.db.session import get_session
from etl.db.models import (
    EventWindow,
    Match,
)
from etl.db.status import (
    STATUS_PROCESSED,
    mark_event_window_failed,
    mark_event_window_started,
    mark_event_window_succeeded,
    mark_match_failed,
    mark_match_started,
    mark_match_succeeded,
)


def get_existing_event_window_ids() -> list[str]:
    with get_session() as session:
        return list(
            session.scalars(
                select(EventWindow.event_window_id).order_by(EventWindow.event_window_id)
            )
        )


def process_match(match_id: str, event_window_id: str, force: bool = False) -> bool:
    existing_processed_event_window_id = None

    if not force:
        with get_session() as session:
            match = session.get(Match, match_id)
            if match and match.status == STATUS_PROCESSED:
                if match.event_window_id == event_window_id:
                    print(f"⏭️  Match {match_id} already processed, skipping...")
                    return True

                existing_processed_event_window_id = match.event_window_id
                print(
                    f"⚠️  Match {match_id} is processed under "
                    f"{existing_processed_event_window_id}; reconciling with raw metadata."
                )

    try:
        started_at = datetime.now(timezone.utc)

        # with get_session() as session, session.begin():
            # pass

        print(f"\n{'='*60}")
        print(f"Processing match: {match_id}")
        print(f"{'='*60}\n")
        
        print(f"Check that all event logs are fetched for match {match_id}...")

        # ensure all raw match data is fetched into and loaded from S3
        raw = ensure_match_raw(match_id)
        match_metadata = parse_match_metadata(raw)

        if match_metadata["event_window_id"] != event_window_id:
            raise ValueError(
                f"Match {match_id} belongs to event window "
                f"{match_metadata['event_window_id']}, not {event_window_id}."
            )

        if existing_processed_event_window_id is not None:
            with get_session() as session, session.begin():
                db_loader.load_match_metadata(match_metadata, session)

            print(
                f"\n✅ Reconciled match {match_id} from "
                f"{existing_processed_event_window_id} to {event_window_id}\n"
            )
            return True

        # this is only one parsing type of the pipeline now
        parsed_relational = parse_match_relational(raw)
        parsed_timeline = parse_match_timeline(raw)

        with get_session() as session, session.begin():
            match = db_loader.load_match_relational(parsed_relational, event_window_id, session)
            mark_match_started(match, started_at)
            s3_loader.load_match_timeline(parsed_timeline, event_window_id, session)
            mark_match_succeeded(match, datetime.now(timezone.utc))

        print(f"\n✅ Successfully processed match {match_id}\n")
        return True

    except Exception as e:
        print(f"\n❌ Error processing match {match_id}: {e}")

        import traceback
        traceback.print_exc()

        with get_session() as session, session.begin():
            match = session.get(Match, match_id)
            if match is not None:
                mark_match_failed(match, datetime.now(timezone.utc))

        return False


def process_event_window(event_window_id: str, force: bool = False):
    """
    Process an entire event window
    """
    print(f"\n{'#'*60}")
    print(f"Processing Event Window: {event_window_id}")
    print(f"{'#'*60}\n")

    # force process check (always comes first)
    if not force:
        with get_session() as session:
            event_window = session.get(EventWindow, event_window_id)
            if event_window and event_window.status == STATUS_PROCESSED:
                print(f"Event window {event_window_id} already processed, skipping...")
                return {"status": "already_processed"}

    started_at = datetime.now(timezone.utc)

    # fetch event window raw logs if needed (info.json and matches.json)
    raw = ensure_event_window_raw(event_window_id)

    # parse event window data
    event_window_data = parse_event_window_metadata(raw)
    matches = parse_event_window_matches(raw)

    classification = classify_event_window_id(event_window_id)

    with get_session() as session, session.begin():
        if classification is not None:
            tournament_metadata = resolve_tournament_metadata(classification)
            db_loader.ensure_tournament(tournament_metadata, session)
        event_window = db_loader.load_event_window_metadata(event_window_data, session)
        mark_event_window_started(event_window, started_at)

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
    event_window_ids = [
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
        "S40_FNCSMajor1_Final_Day1_EU",
        "S40_FNCSMajor1_Final_Day2_EU",
        "S40_FNCSMajor1_Final_Day1_NAC",
        "S40_FNCSMajor1_Final_Day2_NAC",
        "S40_FNCSDivisionalCup_Division1_Week5Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week5Final_EU",
    ]
    """
    event_window_ids = [
    ]
    """
    # event_window_ids = get_existing_event_window_ids()

    # if not event_window_ids:
        # print("No event windows found in the database; nothing to reprocess.")
        # raise SystemExit(0)

    # schema.reinit_db()

    statuses: dict[str, dict] = {}
    for event_window_id in event_window_ids:
        statuses[event_window_id] = process_event_window(event_window_id, force=True)

    print("\nReprocessing summary:")
    for event_window_id, status in statuses.items():
        print(f"- {event_window_id}: {status}")
