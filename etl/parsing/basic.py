import os
from platform import machine
import pandas as pd
import numpy as np

from bisect import bisect_left
from collections import defaultdict
from datetime import datetime

from etl.types import RawMatchData


coord3d = tuple[float, float, float]

def calculate_distances(
    coords_pairs: list[tuple[coord3d, coord3d]]
) -> np.ndarray:
    """Calculate 3D distances from coordinate pairs."""
    a_arr = np.array([c[0] for c in coords_pairs], dtype=np.float32)
    r_arr = np.array([c[1] for c in coords_pairs], dtype=np.float32)
    diff = a_arr - r_arr
    return np.sqrt(np.sum(diff**2, axis=1))


def indexed_events(
    reference_events: list[dict],
    player_events: list[dict]
) -> defaultdict[str, dict[str, dict]]:
    """
    Returns for each player, the event belonging to them in `player_events` 
    occuring closest in time to each event in `reference_events`.

    Results will reflect the same order as `reference_events` when passed in.

    This will allow, for a given reference event, instant lookup of all player
    events that occurred.
    """

    target_ts = np.array([e["timestamp"] for e in reference_events])
    sorted_per_player = defaultdict(list)

    # Created sorted list of `player_events` for each player
    for e in player_events:
        sorted_per_player[e["epicId"]].append(e)
    for player_id in sorted_per_player:
        sorted_per_player[player_id].sort(key=lambda e: e["timestamp"])

    cache = defaultdict(dict)
    # operates on all the `player_events` of a player at once
    for player_id, events in sorted_per_player.items():
        event_ts = np.array([e["timestamp"] for e in events])
        indices = np.searchsorted(event_ts, target_ts)
        indices = np.clip(indices, 1, len(event_ts)-1)

        before = event_ts[indices - 1]
        after = event_ts[indices]

        # pick the closer shot (in time)
        choose_after = np.abs(after - target_ts) < np.abs(before - target_ts)
        closest_idxs = np.where(choose_after, indices, indices - 1)

        # Extract the movement events
        closest_player_events = [events[i] for i in closest_idxs]

        # Store results
        cache[player_id]["timestamps"] = event_ts
        cache[player_id]["closest_indices"] = closest_idxs
        # cache[player_id]["closest_events"][i] is the closest event of player_id to the i-th reference event
        cache[player_id]["closest_events"] = closest_player_events

    return cache


def build_zone_timeline(zone_events: list[dict]):

    assert len(zone_events) == 12, f"Expected 12 zone events, got {len(zone_events)}"
    
    zone_timeline = []
    zone_events.sort(key=lambda e: e["currentPhase"])
    
    for i, zone_event in enumerate(zone_events):
        end_time = zone_event["shrinkEndTime"]
        zone_timeline.append(end_time)
    return zone_timeline


def parse_match_metadata(raw: RawMatchData):
    match_info = raw.info
    
    return {
        "match_id": raw.match_id,
        "event_id": match_info["eventId"],
        "event_window_id": match_info["eventWindowId"],
        "start_time": datetime.fromtimestamp(match_info["aircraftStartTime"] / 1e6),
        "end_time": datetime.fromtimestamp(match_info["endTimestamp"] / 1e6) if match_info.get("endTimestamp") else None,
        "gamemode": match_info["gameMode"],
        "duration": datetime.fromtimestamp(match_info["lengthMs"]),
        "player_count": match_info["playerCount"],
        "bus_launch_time": match_info["aircraftStartTime"],
        "map_path": match_info["mapPath"],
    }


def parse_match_players(raw: RawMatchData) -> list[dict]:
    match_players = raw.players
    
    players = []
    for p in match_players:
        if  p["isSpectator"] or p["isBot"]:
            continue

        players.append({
            "epic_id": p["epicId"],
            "epic_username": p["epicUsername"],
        })

    print(f"Parsed {len(players)} players from {len(match_players)} total")
    return players

def parse_elims(raw: RawMatchData):
    print(f"Parsing eliminations for match {raw.match_id}...")
    """
    Time
    Distance
    Weapon
    """
    match_info = raw.info
    elim_events = raw.elimination_events
    zone_events = raw.zone_update_events
    movement_events = raw.movement_events

    match_start = match_info["aircraftStartTime"]
    zone_timeline = build_zone_timeline(zone_events)

    elim_events.sort(key=lambda e: e["timestamp"])
    
    # Filter out self eliminations before building pos_cache
    non_self_elims = [e for e in elim_events if not e.get("selfElimination")]
    pos_cache = indexed_events(non_self_elims, movement_events)

    enriched_elim_events = []
    coord_pairs = []

    for i, ee in enumerate(non_self_elims):
        actor_id = ee["epicId"]
        recipient_id = ee["targetId"]

        ts = ee["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = bisect_left(zone_timeline, ts) + 1
        # problem here
        weapon_id = "WID_Assault_FirePetal_Fast_Athena_UC"
        gun_type = ee["gunType"]

        actor_loc = ee.get("playerLocation")
        # key "playerLocation" is not guaranteed to exist for storm eliminations
        if actor_loc is None:
            if actor_id not in pos_cache:
                continue
            actor_move_event = pos_cache[actor_id]["closest_events"][i]
            # print(json.dumps(actor_move_event, indent=2))
            actor_loc = actor_move_event["movementData"]["location"]

        # key "targetLocation" should always exist
        recipient_loc = ee["targetLocation"]

        coord_pairs.append((
            (actor_loc["x"], actor_loc["y"], actor_loc["z"]),
            (recipient_loc["x"], recipient_loc["y"], recipient_loc["z"])
        ))

        enriched_elim_events.append({
            "timestamp": ts,
            "game_time_seconds": game_time_seconds,
            "zone": zone,
            "weapon_id": weapon_id,
            "actor_id": actor_id,
            "recipient_id": recipient_id,
            "ax": actor_loc["x"],
            "ay": actor_loc["y"],
            "az": actor_loc["z"],
            "rx": recipient_loc["x"],
            "ry": recipient_loc["y"],
            "rz": recipient_loc["z"],
        })

    distances = calculate_distances(coord_pairs)
    for i, event in enumerate(enriched_elim_events):
        event["distance"] = float(distances[i])

    return enriched_elim_events


def parse_hitscan_elims(raw: RawMatchData) -> list[dict]:
    """
    Returns a time-ordered list of elimination events

    Parses shot_events instead of human_elim events
    """

    print(f"Parsing eliminations(2) for match {raw.match_id}...")
    """
    Time
    Distance
    Weapon
    """

    match_info = raw.info
    zone_events = raw.zone_update_events
    movement_events = raw.movement_events
    shot_events = raw.shot_events

    match_start = match_info["aircraftStartTime"]

    zone_timeline = build_zone_timeline(zone_events)
    # filter shots that hit players
    elim_events = [
        e for e in shot_events 
        if (
            e.get("hitPlayer") and
            e.get("hitFatal")
        )
    ]

    elim_events.sort(key=lambda e: e["timestamp"])
    pos_cache = indexed_events(elim_events, movement_events)

    enriched_damage_events = []

    coord_pairs = []
    for i, he in enumerate(elim_events):
        ts = he["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = bisect_left(zone_timeline, ts) + 1

        damage = he["damage"]
        weapon_id = he["weaponId"]

        actor_id = he["epicId"]
        actor_move_event = pos_cache[actor_id]["closest_events"][i]
        actor_loc = actor_move_event["movementData"]["location"]

        recipient_id = he["hitEpicId"]
        recipient_loc = he["location"]

        coord_pairs.append((
            (actor_loc["x"], actor_loc["y"], actor_loc["z"]),
            (recipient_loc["x"], recipient_loc["y"], recipient_loc["z"])
        ))

        enriched_damage_events.append({
            "timestamp": ts,
            "game_time_seconds": game_time_seconds,
            "zone": zone,
            "damage": damage,
            "weapon_id": weapon_id,
            "actor_id": actor_id,
            "recipient_id": recipient_id,
            "ax": actor_loc["x"],
            "ay": actor_loc["y"],
            "az": actor_loc["z"],
            "rx": recipient_loc["x"],
            "ry": recipient_loc["y"],
            "rz": recipient_loc["z"],
        })

    distances = calculate_distances(coord_pairs)
    for i, event in enumerate(enriched_damage_events):
        event["distance"] = float(distances[i])

    return enriched_damage_events


def parse_damage_dealt(raw: RawMatchData):
    print(f"Parsing damage dealt for match {raw.match_id}...")
    """
    Time
    Distance
    Weapon
    """

    match_info = raw.info
    zone_events = raw.zone_update_events
    movement_events = raw.movement_events
    shot_events = raw.shot_events

    match_start = match_info["aircraftStartTime"]

    zone_timeline = build_zone_timeline(zone_events)
    # filter shots that hit players
    hit_events = [e for e in shot_events if e.get("hitPlayer")]

    hit_events.sort(key=lambda e: e["timestamp"])
    pos_cache = indexed_events(hit_events, movement_events)

    enriched_damage_events = []

    coord_pairs = []
    for i, he in enumerate(hit_events):
        ts = he["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = bisect_left(zone_timeline, ts) + 1

        damage = he["damage"]
        weapon_id = he["weaponId"]

        actor_id = he["epicId"]
        actor_move_event = pos_cache[actor_id]["closest_events"][i]
        actor_loc = actor_move_event["movementData"]["location"]

        recipient_id = he["hitEpicId"]
        recipient_loc = he["location"]

        coord_pairs.append((
            (actor_loc["x"], actor_loc["y"], actor_loc["z"]),
            (recipient_loc["x"], recipient_loc["y"], recipient_loc["z"])
        ))

        enriched_damage_events.append({
            "timestamp": ts,
            "game_time_seconds": game_time_seconds,
            "zone": zone,
            "damage": damage,
            "weapon_id": weapon_id,
            "actor_id": actor_id,
            "recipient_id": recipient_id,
            "ax": actor_loc["x"],
            "ay": actor_loc["y"],
            "az": actor_loc["z"],
            "rx": recipient_loc["x"],
            "ry": recipient_loc["y"],
            "rz": recipient_loc["z"],
        })

    distances = calculate_distances(coord_pairs)
    for i, event in enumerate(enriched_damage_events):
        event["distance"] = float(distances[i])

    return enriched_damage_events



if __name__ == "__main__":
    match_id = "832ceecc424df110d58e3e96d3dff834"
    # damage_events = parse_damage_dealt(match_id)
    # print(json.dumps(damage_events, indent=2))
    # elim_events = parse_elims(match_id)
    # print(json.dumps(elim_events, indent=2))
    # assist_events = parse_assists(match_id)
    # print(json.dumps(assist_events, indent=2))
