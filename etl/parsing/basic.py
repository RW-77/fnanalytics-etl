import numpy as np

from datetime import datetime, timedelta

from etl.types import RawMatchData
from etl.parsing.indexing import indexed_events


coord3d = tuple[float, float, float]

def calculate_distances(
    coords_pairs: list[tuple[coord3d, coord3d]]
) -> np.ndarray:
    """Calculate 3D distances from coordinate pairs."""
    a_arr = np.array([c[0] for c in coords_pairs], dtype=np.float32)
    r_arr = np.array([c[1] for c in coords_pairs], dtype=np.float32)
    diff = a_arr - r_arr
    return np.sqrt(np.sum(diff**2, axis=1))


def _sorted_zone_events(zone_events: list[dict]) -> list[dict]:
    """
    Return safe zone updates ordered by the time each phase ends.

    Some payloads include a replay-event `timestamp`, while older cached samples
    only expose shrink timings. We only need stable chronological ordering to
    map a gameplay event onto the phase that was active at that time.
    """
    if not zone_events:
        raise ValueError("Match did not include any safe zone update events.")

    return sorted(
        zone_events,
        key=lambda event: (
            event["shrinkEndTime"],
            event.get("timestamp", event.get("shrinkStartTime", 0)),
            event["currentPhase"],
        ),
    )


def _zone_for_timestamp(ordered_zone_events: list[dict], timestamp: int) -> int:
    """
    Find the current phase for an event timestamp with a simple linear scan.

    We stop at the first zone whose `shrinkEndTime` still contains the event.
    If the timestamp lands after every known shrink, we keep the last phase
    instead of assuming a missing phase should crash parsing.
    """
    for zone_event in ordered_zone_events:
        if timestamp <= zone_event["shrinkEndTime"]:
            return zone_event["currentPhase"]

    return ordered_zone_events[-1]["currentPhase"]


def parse_match_metadata(raw: RawMatchData):
    match_info = raw.info
    
    return {
        "match_id": raw.match_id,
        "event_id": match_info["eventId"],
        "event_window_id": match_info["eventWindowId"],
        "map_path": match_info["mapPath"],
        "start_time": datetime.fromtimestamp(match_info["aircraftStartTime"] / 1e6),
        "end_time": (
            datetime.fromtimestamp(match_info["endTimestamp"] / 1e6)
            if match_info.get("endTimestamp")
            else None
        ),
        "gamemode": match_info["gameMode"],
        "duration": timedelta(milliseconds=match_info["lengthMs"]),
        "player_count": match_info["playerCount"],
    }


def _is_match_player(player: dict) -> bool:
    return not player["isSpectator"] and not player["isBot"]


def _eligible_player_ids(raw: RawMatchData) -> set[str]:
    return {
        player["epicId"]
        for player in raw.players
        if _is_match_player(player)
    }


def parse_match_players(raw: RawMatchData) -> list[dict]:
    match_players = raw.players
    
    players = []
    for p in match_players:
        if not _is_match_player(p):
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
    ordered_zone_events = _sorted_zone_events(raw.zone_update_events)
    movement_events = raw.movement_events

    match_start = match_info["aircraftStartTime"]
    eligible_player_ids = _eligible_player_ids(raw)

    elim_events.sort(key=lambda e: e["timestamp"])
    
    # Keep only non-self eliminations between players that will exist in MatchPlayer.
    non_self_elims = [
        e for e in elim_events
        if (
            not e.get("selfElimination")
            and e.get("epicId") in eligible_player_ids
            and e.get("targetId") in eligible_player_ids
        )
    ]
    pos_cache = indexed_events(non_self_elims, movement_events)

    enriched_elim_events = []
    coord_pairs = []

    for i, ee in enumerate(non_self_elims):
        actor_id = ee["epicId"]
        recipient_id = ee["targetId"]

        ts = ee["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = _zone_for_timestamp(ordered_zone_events, ts)
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
    ordered_zone_events = _sorted_zone_events(raw.zone_update_events)
    movement_events = raw.movement_events
    shot_events = raw.shot_events

    match_start = match_info["aircraftStartTime"]
    eligible_player_ids = _eligible_player_ids(raw)

    # Keep only fatal player-vs-player hits that map to MatchPlayer rows.
    elim_events = [
        e for e in shot_events 
        if (
            e.get("hitPlayer") and
            e.get("hitFatal") and
            e.get("epicId") in eligible_player_ids and
            e.get("hitEpicId") in eligible_player_ids
        )
    ]

    elim_events.sort(key=lambda e: e["timestamp"])
    pos_cache = indexed_events(elim_events, movement_events)

    enriched_damage_events = []

    coord_pairs = []
    for i, he in enumerate(elim_events):
        ts = he["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = _zone_for_timestamp(ordered_zone_events, ts)

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
    ordered_zone_events = _sorted_zone_events(raw.zone_update_events)
    movement_events = raw.movement_events
    shot_events = raw.shot_events

    match_start = match_info["aircraftStartTime"]
    eligible_player_ids = _eligible_player_ids(raw)

    # Keep only player-vs-player hits that map to MatchPlayer rows.
    hit_events = [
        e for e in shot_events
        if (
            e.get("hitPlayer")
            and e.get("epicId") in eligible_player_ids
            and e.get("hitEpicId") in eligible_player_ids
        )
    ]

    hit_events.sort(key=lambda e: e["timestamp"])
    pos_cache = indexed_events(hit_events, movement_events)

    enriched_damage_events = []

    coord_pairs = []
    for i, he in enumerate(hit_events):
        ts = he["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = _zone_for_timestamp(ordered_zone_events, ts)

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
