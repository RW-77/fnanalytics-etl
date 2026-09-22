import json
from datetime import datetime

from etl.api.osirion_client import fetch_event_window_matches
from etl.types import RawEventWindowData
from etl.parsing.tournament.classification import classify_event_window_id
from etl.parsing.tournament.metadata import (
    EventMetadata,
    TournamentMetadata,
    resolve_tournament_metadata,
)


def _event_id_from_raw(raw: RawEventWindowData) -> str:
    """Pull the Osirion ``eventId`` out of the cached event-window payloads.

    The info payload looks like ``{"tournaments": [{"eventId": "...", ...}, ...]}``.
    Multiple entries can appear but all share the same ``eventId`` in practice.

    Osirion occasionally serves an empty ``{"tournaments": []}`` info payload
    for a window that nonetheless has matches (seen on
    ``S41_PerformanceEvaluation_Event5Round2_*``). Every match carries the same
    ``eventId`` on its own info block, so fall back to that rather than failing
    the whole window.
    """
    tournaments = raw.info.get("tournaments") or []
    if tournaments:
        return tournaments[0]["eventId"]

    for match in raw.matches:
        event_id = (match.get("info") or {}).get("eventId")
        if event_id:
            return event_id

    raise ValueError(
        f"No eventId in the info or matches payloads for "
        f"{raw.event_window_id}; cannot determine event_id."
    )


def parse_event_metadata(raw: RawEventWindowData) -> EventMetadata:
    """Extract the fields that belong on the ``events`` table.

    ``region_code`` and ``season_code`` come from the same event-window-id
    classification that drives tournament parsing — they're facts about
    the event, materialized for queryability.

    ``image_key`` is derived by convention (``<event_id>.jpg``). Images
    are uploaded to S3 by hand under that key; the column tells the
    website where to look. If the file isn't uploaded yet, the row still
    carries the expected key and the website is responsible for handling
    the missing-object case.
    """
    classification = classify_event_window_id(raw.event_window_id)
    event_id = _event_id_from_raw(raw)
    return EventMetadata(
        event_id=event_id,
        region_code=classification.region_code if classification else None,
        season_code=classification.season_code if classification else None,
        image_key=f"{event_id}.jpg",
    )


def parse_tournament_metadata(raw: RawEventWindowData) -> TournamentMetadata | None:
    """Resolve the ``tournaments``-table fields, or ``None`` if not classifiable.

    Combines rule-derived title (``tournament_metadata.TITLE_RULES``) with
    manual overrides (``TOURNAMENT_REGISTRY``). Returns ``None`` for event
    windows that no classification rule matches — the caller decides what
    to do with that (skip the tournament upsert, error, log).
    """
    classification = classify_event_window_id(raw.event_window_id)
    if classification is None:
        return None
    return resolve_tournament_metadata(classification)


def parse_event_window_metadata(
    raw: RawEventWindowData,
    tournament_meta: TournamentMetadata | None = None,
) -> dict:
    """Extract the fields that belong on the ``event_windows`` table.

    ``region_code`` and ``season_code`` have been promoted to ``events``;
    they no longer appear here. ``tournament_id`` is kept because event
    windows belong to a tournament — that's the link the website uses.

    Pass *tournament_meta* (already resolved by the caller) to apply any
    ``force_day_index_null`` override without a redundant registry lookup.
    """
    classification = classify_event_window_id(raw.event_window_id)

    event_window_matches = raw.matches
    event_id = _event_id_from_raw(raw)

    event_window_matches.sort(key=lambda e: e["info"]["startTimestamp"])
    total_matches = len(event_window_matches)

    if total_matches == 0:
        raise ValueError(f"No matches found for event window {raw.event_window_id}")

    first_match = event_window_matches[0]
    last_match = event_window_matches[-1]

    start_time = first_match["info"].get("startTimestamp")
    if start_time is not None:
        start_time = datetime.fromtimestamp(start_time / 1e6)
    end_time = last_match["info"].get("endTimestamp")
    if end_time is not None:
        end_time = datetime.fromtimestamp(end_time / 1e6)

    day_index = classification.day_index if classification else None
    if tournament_meta is not None and tournament_meta.force_day_index_null:
        day_index = None

    return {
        "event_window_id": raw.event_window_id,
        "event_id": event_id,
        "start_time": start_time,
        "end_time": end_time,
        "total_matches": total_matches,
        "tournament_id": classification.tournament_id if classification else None,
        "day_index": day_index,
    }


def parse_event_window_matches(raw: RawEventWindowData) -> list[dict]:
    matches = raw.matches
    print(f"Found {len(matches)} matches to process\n")

    return matches


def parse_event_window_weapons(event_window_id) -> list[dict]:
    matches = parse_event_window_matches(event_window_id)

    # Track unique weapons across all matches
    seen_weapons = set()
    
    # Non-weapon types to filter out
    excluded_types = {
        "PICKAXE", 
        "BUILDING", 
        "LOOT", 
        "SHIELD_HEAL", 
        "EDIT_TOOL",
        "MOVEMENT", 
        "HEALTH_HEAL", 
        "BOTH_HEAL"
    }

    for match in matches:
        match_info = match["info"]
        match_id = match_info["matchId"]
        weapons_path = f"data/raw/match_{match_id}/weapons.json"

        try:
            with open(weapons_path, "r") as f:
                match_weapons = json.load(f)["weapons"]
        except FileNotFoundError:
            continue

        for weapon in match_weapons:
            weapon_type = weapon.get("weaponType")
            weapon_id = weapon.get("weaponId")
            
            # Skip non-weapons and weapons we've already seen
            if weapon_type in excluded_types or weapon_id in seen_weapons:
                continue
            
            # Store the weapon info
            seen_weapons[weapon_id] = {
                "weapon_id": weapon_id,
                "weapon_type": weapon_type
            }

    return list(seen_weapons.values())
