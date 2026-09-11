from etl.types import RawMatchData
from etl.parsing.indexing import PlayerPositionIndex

# Max staleness (microseconds) for a trusted shooter position — matches
# shot_attempts.MAX_GAP_US. A shot whose shooter has no movement sample within
# this window is dropped (we can't anchor an origin for the line).
MAX_GAP_US = 600_000

# Ranged weapon types that produce a visible shot line. Everything else
# (melee, builds, heals, grenades, loot, edit tool, …) is skipped — the same
# spirit as shot_attempts._is_ranged_weapon, but driven by the weapon catalog's
# explicit type rather than a name substring.
_RANGED_TYPES = {"ASSAULT", "SMG", "DMR", "PISTOL", "SHOTGUN", "SNIPER"}


def parse_match_shots(
    raw: RawMatchData,
    player_index: dict[str, int],
    hz: int | None = None,  # accepted for symmetry with parse_match_frames; unused
) -> list[dict]:
    """Extract sparse shot events for the replay client.

    Each returned shot carries a start time (seconds from match start, same
    origin as the movement frames and zones), the shooter's array index and
    team, a world-space origin (shooter position at shot time) and endpoint,
    and a shotgun flag that drives the line style on the client.

    Sourced from ``fire_weapon_events`` (the complete bulk feed) rather than
    ``shot_events`` (truncates under load), exactly as
    :func:`etl.parsing.shot_attempts.parse_shot_attempts`.
    """
    weapon_type = {
        w["weaponId"]: w.get("weaponType")
        for w in raw.weapons
        if w.get("weaponId")
    }
    team_of = {pid: t["teamId"] for t in raw.teams for pid in t["epicId"]}

    index = PlayerPositionIndex(raw)
    t_0 = raw.info["aircraftStartTime"]

    shots: list[dict] = []
    for evt in raw.fire_weapon_events:
        actor_id = evt.get("epicId")
        shooter_index = player_index.get(actor_id)
        if shooter_index is None:
            continue

        wtype = weapon_type.get(evt.get("weaponId", ""))
        if wtype not in _RANGED_TYPES:
            continue

        ts = evt["timestamp"]
        actor_pos = index.position_at(ts, actor_id, MAX_GAP_US)
        if actor_pos is None:
            continue  # no trustworthy origin -> can't draw the line

        loc = evt["location"]
        # Coords are centimeters; integer precision is far finer than the map
        # projection can show, and rounding roughly halves the JSON payload.
        shots.append({
            "t":  round((ts - t_0) * 1e-6, 3),
            "s":  shooter_index,
            "team": team_of.get(actor_id),
            "ax": round(actor_pos.location[0]),
            "ay": round(actor_pos.location[1]),
            "ex": round(loc["x"]),
            "ey": round(loc["y"]),
            "sg": wtype == "SHOTGUN",
        })

    shots.sort(key=lambda s: s["t"])
    return shots
