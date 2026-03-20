import json
from datetime import datetime

from etl.api.osirion_client import fetch_by_event_window


def parse_event_window_metadata(event_window_id):
    event_window_path = f"data/raw/event_window_{event_window_id}"
    try:
        with (
            open(f"{event_window_path}/info.json", "r") as f1,
            open(f"{event_window_path}/matches.json") as f2
        ):
            event_window_info = json.load(f1)
            event_window_matches = json.load(f2)["matches"]
    except FileNotFoundError:
        raise ValueError(f"Event window files not found.")

    event_window_matches.sort(key=lambda e: e["info"]["startTimestamp"])
    total_matches = len(event_window_matches)

    if total_matches == 0:
        raise ValueError(f"No matches found for event window {event_window_id}")

    first_match = event_window_matches[0]
    last_match = event_window_matches[-1]

    start_time = first_match["info"].get("startTimestamp")
    if start_time is not None:
        start_time = datetime.fromtimestamp(start_time / 1e6)
    end_time = last_match["info"].get("endTimestamp")
    if end_time is not None:
        end_time = datetime.fromtimestamp(end_time / 1e6)

    return {
        "event_window_id": event_window_id,
        "start_time": start_time,
        "end_time": end_time,
        "total_matches": total_matches
    }


def parse_event_matches(event_window_id) -> list[dict]:
    event_window_matches_path = f"data/raw/event_window_{event_window_id}/matches.json"
    with open(event_window_matches_path, "r") as f:
        matches = json.load(f)["matches"]

        print(f"Found {len(matches)} matches to process\n")

    return matches


def parse_event_weapons(event_window_id) -> list[dict]:
    matches = parse_event_matches(event_window_id)

    # Track unique weapons across all matches
    seen_weapons = {}
    
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
