import json
from datetime import datetime, timezone
from sqlalchemy import select

import etl.api.osirion_client as osr
import etl.db.loader as loader

from etl.api.check_fetched import event_window_fetched, fetch_match_missing
from etl.parsing.cleaning import get_id_to_name_map

from etl.db.models import (
    EventWindow,
    Match,
    MatchPlayer,
    DamageDealtEvent,
    EliminationEvent,
    get_session, reinit_db, get_engine
)
from etl.db.loader import (
    load_event_window_metadata,
    load_match_metadata,
    load_match_players,
    load_damage_dealt_events,
    load_elimination_events
)
from etl.parsing.event_parsing import parse_event_window_metadata, parse_event_matches
from etl.parsing.match_parsing import (
    parse_match_metadata, 
    parse_match_players,
    parse_elims, 
    parse_damage_dealt, 
)


def process_match(match_id: str, event_window_id: str, skip_if_exists: bool = False):
    session = get_session()

    # First process the match itself
    try:
        if skip_if_exists:
            stmt = select(Match).where(Match.match_id == match_id)
            existing = session.scalars(stmt).first()
            if existing:
                print(f"⏭️  Match {match_id} already processed, skipping...")
                return True

        print(f"\n{'='*60}")
        print(f"Processing match: {match_id}")
        print(f"{'='*60}\n")
        
        print(f"Check that all event logs are fetched for match {match_id}...")
        status = fetch_match_missing(match_id)
        
        # Raw data is guaranteed to be fetched by this point
        match_data = parse_match_metadata(match_id)

        # Needs to be added to the database entry
        if event_window_id:
            match_data["event_window_id"] = event_window_id

        players = parse_match_players(match_id)
        elims = parse_elims(match_id)
        damage_dealt = parse_damage_dealt(match_id)

        # Load into database (all in one transaction)
        print("\n💾 Loading into database...")
        loader.load_match_metadata(match_data, session)
        loader.load_match_players(players, match_id, session)
        loader.load_damage_dealt_events(damage_dealt, match_id, session)
        loader.load_elimination_events(elims, match_id, session)
        
        print(f"\n✅ Successfully processed match {match_id}\n")
        return True

    except Exception as e:
        print(f"\n❌ Error processing match {match_id}: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        return False

    finally:
        session.close()


def process_event_window(event_window_id: str):
    """
    Process an entire event window
    """
    session = get_session()

    try:
        print(f"\n{'#'*60}")
        print(f"Processing Event Window: {event_window_id}")
        print(f"{'#'*60}\n")

        # fetch from API and load into data/raw if not already done so
        if not event_window_fetched(event_window_id).get("info"):
            event_window_data_path = osr.fetch_event_window_data(event_window_id)

        event_window_data = parse_event_window_metadata(event_window_id)

        # Check if event window exists
        stmt = select(EventWindow).where(EventWindow.event_window_id == event_window_id)
        event_window = session.scalars(stmt).first()

        if not event_window:
            print(f"Event window {event_window_id} does not yet exist")
            loader.load_event_window_metadata(event_window_data, session)
            print("Got here.")
            session.commit()
            event_window = session.scalars(stmt).first()

            if not event_window:
                raise RuntimeError(f"Failed to create event window {event_window_id}")

        if event_window.processed:
            print(f"⏭️  Event window metadata already processed. Processing matches...")

        event_window.processing = True
        event_window.last_processing_start = datetime.now(timezone.utc)
        session.commit()

    except Exception as e:
        print(f"\n❌ Error loading event window: {e}")
        session.rollback()
        return {"status": "error", "error": str(e)}

    finally:
        session.close()  # Close event window session


    # get matches
    try:
        # First, check if the matches for the current EventWindow have been fetched
        # from Osirion's API
        if not event_window_fetched(event_window_id).get("matches"):
            # If not, fetch them
            print(f"Need to fetch matches for event_window {event_window_id}")
            matches_path = osr.fetch_by_event_window(event_window_id)

        matches = parse_event_matches(event_window_id)

        results = {"total": len(matches), "successful": 0, "failed": 0}

        # For each match parse
        for i, match in enumerate(matches):
            match_id = match["info"]["matchId"]

            success = process_match(match_id, event_window_id, skip_if_exists=True)
            if success:
                results["successful"] += 1
            else:
                results["failed"] += 1

        session = get_session()

        try:
            stmt = select(EventWindow).where(EventWindow.event_window_id == event_window_id)
            event_window = session.scalars(stmt).first()

            if not event_window:
                raise Exception("event window processing failed")

            event_window.processing = False

            if results["failed"] > results["total"] / 2:
                event_window.failed = True
                event_window.last_failed = datetime.now(timezone.utc)
            else:
                event_window.processed = True
                event_window.last_processed = datetime.now(timezone.utc)

            session.commit()

            # Print summary
            print(f"\n{'='*60}")
            print(f"Event Window Complete: {event_window_id}")
            print(f"{'='*60}")
            print(f"Total: {results['total']}")
            print(f"✅ Successful: {results['successful']}")
            print(f"❌ Failed: {results['failed']}")
            print(f"Status: {'PROCESSED' if event_window.processed else 'FAILED'}")
            print(f"{'='*60}\n")
            
            return results

        except Exception as e:
            print(f"\n❌ Error updating event window status: {e}")
            session.rollback()
            return {"status": "error", "error": str(e)}

        finally:
            session.close()


    except Exception as e:
        print(f"\n❌ Error processing matches: {e}")
        import traceback
        traceback.print_exc()
        
        # Mark event window as failed
        session = get_session()
        try:
            stmt = select(EventWindow).where(EventWindow.event_window_id == event_window_id)
            event_window = session.scalars(stmt).first()
            
            if event_window:
                event_window.processing = False
                event_window.failed = True
                event_window.last_failed = datetime.now(timezone.utc)
                session.commit()
        except Exception as cleanup_error:
            print(f"⚠️  Failed to update event window status: {cleanup_error}")
            session.rollback()
        finally:
            session.close()
        
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    reinit_db() # Warning: will reinitialize entire DB
    event_window_id = "S33_FNCSMajor1_Final_Day1_EU"
    process_event_window(event_window_id)
