import json
from datetime import datetime, timezone
from sqlalchemy import select

import etl.api.osirion_client as osr
import etl.db.loader as loader

from etl.fetching.match_data_fetching import ensure_match_raw, ensure_event_window_raw
from etl.parsing.match_parsing import parse_match
from etl.parsing.event_parsing import parse_event_window_metadata, parse_event_window_matches

from etl.db.models import (
    EventWindow,
    Match,
    get_session, 
    reinit_db, 
    get_engine
)


'''
My current folder structure for my fortnite-tournament-logs bucket, which houses any data from the Osirion API, is split into:

matches/
    832ceecc424df110d58e3e96d3dff834/
        info.json
        eliminationEvents.json
        ...
event_windows/
    S33_FNCSMajor1_Final_Day1_EU/
        info.json
        matches.json
'''

def process_match(match_id: str, event_window_id: str, force: bool = False) -> bool:

    if not force:
        with get_session() as session:
            match = session.get(Match, match_id)
            if match and match.processed and not force:
                print(f"⏭️  Match {match_id} already processed, skipping...")
                return True

    raw = ensure_match_raw(match_id)
    parsed = parse_match(raw)

    # First process the match itself
    try:
        with get_session() as session, session.begin():

            print(f"\n{'='*60}")
            print(f"Processing match: {match_id}")
            print(f"{'='*60}\n")
            
            print(f"Check that all event logs are fetched for match {match_id}...")

            loader.load_match(parsed, event_window_id, session)
            
            print(f"\n✅ Successfully processed match {match_id}\n")
            return True

    except Exception as e:
        print(f"\n❌ Error processing match {match_id}: {e}")

        import traceback
        traceback.print_exc()

        return False


def process_event_window(event_window_id: str, force: bool):
    """
    Process an entire event window
    """
    print(f"\n{'#'*60}")
    print(f"Processing Event Window: {event_window_id}")
    print(f"{'#'*60}\n")

    if not force:
        with get_session() as session:
            event_window = session.get(EventWindow, event_window_id)
            if event_window and event_window.processed:
                print(f"Event window {event_window_id} already processed, skipping...")
                return {}

    # fetch event window raw logs if needed (info.json and matches.json)
    raw = ensure_event_window_raw(event_window_id)
    # parse event window data
    event_window_data = parse_event_window_metadata(raw)
    matches = parse_event_window_matches(raw)

    with get_session() as session, session.begin():

        # Check if event window exists
        # idiomatically fetch by primary key
        event_window = session.get(EventWindow, event_window_id)

        # if the event window is not already in the DB
        if event_window is None:
            # print(f"Event window {event_window_id} does not yet exist")
            event_window = loader.load_event_window_metadata(event_window_data, session)

        # mark event window as processing
        event_window.processing = True
        event_window.processed = False
        event_window.failed = False
        event_window.last_processing_start = datetime.now(timezone.utc)


    results = {"total": len(matches), "successful": 0, "failed": 0}

    # get matches
    try:
        # For each match parse
        for match in matches:

            match_id = match["info"]["matchId"]
            success = process_match(match_id, event_window_id, force=force)
            results["successful" if success else "failed"] += 1

        with get_session() as session, session.begin():

            event_window = session.get(EventWindow, event_window_id)

            if event_window:
                event_window.processing = False
                event_window.processed_matches = results["successful"]
                event_window.processed = (results["failed"] == 0)
                event_window.failed = (results["failed"] > 0)

                if event_window.processed:
                    event_window.last_processed = datetime.now(timezone.utc)
                else:
                    event_window.last_failed = datetime.now(timezone.utc)

    except Exception as e:
        with get_session() as session, session.begin():
            event_window = session.get(EventWindow, event_window_id)
            if event_window is not None:
                event_window.processing = False
                event_window.failed = True
                event_window.last_failed = datetime.now(timezone.utc)

    return results


if __name__ == "__main__":
    reinit_db() # Warning: will reinitialize entire DB
    event_window_id = "S33_FNCSMajor1_Final_Day1_EU"
    process_event_window(event_window_id, force=True)
