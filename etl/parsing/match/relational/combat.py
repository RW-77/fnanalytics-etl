import numpy as np

from etl.types import RawMatchData
from etl.parsing.common.indexing import indexed_events, PlayerPositionIndex
from etl.parsing.common.eligibility import eligible_player_ids
from etl.parsing.common.zones import sorted_zone_events, zone_for_timestamp


coord3d = tuple[float, float, float]


def calculate_distances(
    coords_pairs: list[tuple[coord3d, coord3d]]
) -> np.ndarray:
    """Calculate 3D distances from coordinate pairs."""
    a_arr = np.array([c[0] for c in coords_pairs], dtype=np.float32)
    r_arr = np.array([c[1] for c in coords_pairs], dtype=np.float32)
    diff = a_arr - r_arr
    return np.sqrt(np.sum(diff**2, axis=1))


def parse_elims(raw: RawMatchData, *, max_gap: int = 600_000):
    print(f"Parsing eliminations for match {raw.match_id}...")
    """
    Time
    Distance
    Weapon
    """
    match_info = raw.info
    elim_events = raw.elimination_events
    ordered_zone_events = sorted_zone_events(raw.zone_update_events)

    match_start = match_info["aircraftStartTime"]
    eligible = eligible_player_ids(raw)
    index = PlayerPositionIndex(raw)

    elim_events.sort(key=lambda e: e["timestamp"])

    # Keep only non-self eliminations between players that will exist in MatchPlayer.
    non_self_elims = [
        e for e in elim_events
        if (
            not e.get("selfElimination")
            and e.get("epicId") in eligible
            and e.get("targetId") in eligible
        )
    ]

    enriched_elim_events = []
    coord_pairs = []

    for ee in non_self_elims:
        actor_id = ee["epicId"]
        recipient_id = ee["targetId"]

        ts = ee["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = zone_for_timestamp(ordered_zone_events, ts)
        weapon_id = "WID_Assault_FirePetal_Fast_Athena_UC"
        gun_type = ee["gunType"]

        actor_loc = ee.get("playerLocation")
        # key "playerLocation" is not guaranteed to exist for storm eliminations
        if actor_loc is None:
            pos = index.position_at(ts, actor_id, max_gap)
            if pos is None:
                continue
            actor_loc = {"x": pos.location[0], "y": pos.location[1], "z": pos.location[2]}

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


def parse_knocks(raw: RawMatchData, *, max_gap: int = 600_000):
    print(f"Parsing knocks for match {raw.match_id}...")
    """
    Time
    Distance
    Gun type
    """
    match_info = raw.info
    knocked_events = raw.knocked_events
    ordered_zone_events = sorted_zone_events(raw.zone_update_events)

    match_start = match_info["aircraftStartTime"]
    eligible = eligible_player_ids(raw)
    index = PlayerPositionIndex(raw)

    knocked_events.sort(key=lambda e: e["timestamp"])

    # Keep only non-self knocks between players that will exist in MatchPlayer.
    non_self_knocks = [
        e for e in knocked_events
        if (
            not e.get("selfElimination")
            and e.get("epicId") in eligible
            and e.get("targetId") in eligible
        )
    ]

    enriched_knock_events = []
    coord_pairs = []

    for ke in non_self_knocks:
        actor_id = ke["epicId"]
        recipient_id = ke["targetId"]

        ts = ke["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = zone_for_timestamp(ordered_zone_events, ts)
        gun_type = ke.get("gunType")

        actor_loc = ke.get("playerLocation")
        # key "playerLocation" is not guaranteed to exist for storm knocks
        if actor_loc is None:
            pos = index.position_at(ts, actor_id, max_gap)
            if pos is None:
                continue
            actor_loc = {"x": pos.location[0], "y": pos.location[1], "z": pos.location[2]}

        # key "targetLocation" should always exist
        recipient_loc = ke["targetLocation"]

        coord_pairs.append((
            (actor_loc["x"], actor_loc["y"], actor_loc["z"]),
            (recipient_loc["x"], recipient_loc["y"], recipient_loc["z"])
        ))

        enriched_knock_events.append({
            "timestamp": ts,
            "game_time_seconds": game_time_seconds,
            "zone": zone,
            "gun_type": gun_type,
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
    for i, event in enumerate(enriched_knock_events):
        event["distance"] = float(distances[i])

    return enriched_knock_events


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
    ordered_zone_events = sorted_zone_events(raw.zone_update_events)
    movement_events = raw.movement_events
    shot_events = raw.shot_events

    match_start = match_info["aircraftStartTime"]
    eligible = eligible_player_ids(raw)

    # Keep only fatal player-vs-player hits that map to MatchPlayer rows.
    elim_events = [
        e for e in shot_events
        if (
            e.get("hitPlayer") and
            e.get("hitFatal") and
            e.get("epicId") in eligible and
            e.get("hitEpicId") in eligible
        )
    ]

    elim_events.sort(key=lambda e: e["timestamp"])
    pos_cache = indexed_events(elim_events, movement_events)

    enriched_damage_events = []

    coord_pairs = []
    for i, he in enumerate(elim_events):
        ts = he["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = zone_for_timestamp(ordered_zone_events, ts)

        damage = he["damage"]
        weapon_id = he["weaponId"]

        actor_id = he["epicId"]
        actor_move_event = pos_cache[actor_id]["closest_events"][i]
        actor_loc = actor_move_event["movementData"]["location"]

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
            "recipient_id": he["hitEpicId"],
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
    Returns a list of damage events throughout a match. Excludes damage
    events onto knocked bodies.
    """

    match_info = raw.info
    ordered_zone_events = sorted_zone_events(raw.zone_update_events)
    movement_events = raw.movement_events
    # Source fireWeaponEvents, not shot_events: the two logs carry identical
    # fields, but the /events/shots endpoint truncates under load (dropping a
    # chunk of the match), while fireWeaponEvents comes from the bulk /events
    # endpoint and is complete.
    fire_weapon_events = raw.fire_weapon_events

    match_start = match_info["aircraftStartTime"]
    eligible = eligible_player_ids(raw)

    # Keep only hits on standing opponents that map to MatchPlayer rows.
    # ``hitResult == "HIT_PLAYER"`` is the game's own per-hit classification of a
    # hit on a standing player; it excludes hits on knocked (DBNO) players
    # (HIT_KNOCKED_PLAYER) and teammates (HIT_TEAM), matching Osirion's
    # ``damageToPlayers`` stat.
    hit_events = [
        e for e in fire_weapon_events
        if (
            e.get("hitResult") == "HIT_PLAYER"
            and e.get("epicId") in eligible
            and e.get("hitEpicId") in eligible
        )
    ]

    hit_events.sort(key=lambda e: e["timestamp"])
    pos_cache = indexed_events(hit_events, movement_events)

    # Which damage field to trust is schema-dependent. Newer logs populate
    # `actualDamage` (post-mitigation, overkill-capped — matches Osirion) and use
    # `damage` as the pre-mitigation value. Older logs leave `actualDamage`
    # unpopulated (always 0) and carry the landed damage in `damage`. Detect per
    # match which field is live so both schemas total correctly; without this,
    # older matches zero out.
    use_actual_damage = any((e.get("actualDamage") or 0) > 0 for e in hit_events)

    enriched_damage_events = []

    coord_pairs = []
    for i, he in enumerate(hit_events):
        ts = he["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6

        zone = zone_for_timestamp(ordered_zone_events, ts)

        damage = he["actualDamage"] if use_actual_damage else he["damage"]
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


# NOTE: _movement_lookup / _nearest_movement_location are currently unused
# (carried over unchanged from the old basic.py during the parsing reorg).
def _movement_lookup(movement_events: list[dict]) -> dict[str, list[dict]]:
    """Build a per-player sorted list of movement events for binary search."""
    by_player: dict[str, list[dict]] = {}
    for evt in movement_events:
        pid = evt["epicId"]
        if pid not in by_player:
            by_player[pid] = []
        by_player[pid].append(evt)
    for events in by_player.values():
        events.sort(key=lambda e: e["timestamp"])
    return by_player


def _nearest_movement_location(
    player_events: list[dict], timestamp: int
) -> tuple[float, float, float] | None:
    """Binary search for the movement event closest to timestamp."""
    import bisect
    if not player_events:
        return None
    ts_list = [e["timestamp"] for e in player_events]
    idx = bisect.bisect_left(ts_list, timestamp)
    if idx == 0:
        evt = player_events[0]
    elif idx >= len(player_events):
        evt = player_events[-1]
    else:
        before = player_events[idx - 1]
        after = player_events[idx]
        evt = after if (after["timestamp"] - timestamp) < (timestamp - before["timestamp"]) else before
    loc = evt["movementData"]["location"]
    return loc["x"], loc["y"], loc["z"]


def parse_shots(raw: RawMatchData, *, max_gap = 600_000):
    match_start = raw.info["aircraftStartTime"]
    ordered_zone_events = sorted_zone_events(raw.zone_update_events)
    eligible = eligible_player_ids(raw)
    index = PlayerPositionIndex(raw)

    results = []
    for evt in raw.fire_weapon_events:
        if evt["epicId"] not in eligible:
            continue

        actor_loc = evt.get("instigatorLocation")
        if actor_loc is not None:
            actor_x, actor_y, actor_z = actor_loc["x"], actor_loc["y"], actor_loc["z"]
        else:
            pos = index.position_at(evt["timestamp"], evt["epicId"], max_gap)
            actor_x, actor_y, actor_z = pos.location if pos is not None else (None, None, None)

        results.append({
            "timestamp": evt["timestamp"],
            "game_time_seconds": (evt["timestamp"] - match_start) / 1e6,
            "zone": zone_for_timestamp(ordered_zone_events, evt["timestamp"]),
            "epic_id": evt["epicId"],
            "weapon_id": evt["weaponId"],
            "damage": evt["damage"],
            "actual_damage": evt["actualDamage"],
            "harvest": evt["harvest"],
            "hit_player": evt["hitPlayer"],
            "hit_critical": evt["hitCritical"],
            "hit_player_build": evt["hitPlayerBuild"],
            "hit_epic_id": evt["hitEpicId"],
            "hit_fatal": evt["hitFatal"],
            "hit_shield": evt["hitShield"],
            "hit_ballistic": evt["hitBallistic"],
            "destroyed_shield": evt["destroyedShield"],
            "actor_x": actor_x,
            "actor_y": actor_y,
            "actor_z": actor_z,
            "end_x": evt["location"]["x"],
            "end_y": evt["location"]["y"],
            "end_z": evt["location"]["z"],
            "hit_result": evt["hitResult"],
            "item_entry_guid": evt.get("itemEntryGuid"),
            "hit_actor_id": evt.get("hitActorId"),
        })

    return results
