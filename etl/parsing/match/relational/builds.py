from etl.types import RawMatchData
from etl.parsing.common.eligibility import eligible_player_ids


def parse_builds_placed(raw: RawMatchData) -> list[dict]:
    """One row per structure placed (``buildEvents``).

    ``epicId`` is the placing player; ``type`` is the piece's asset name and
    ``location`` its placement position. ``actorId`` / ``editedActorId`` are the
    engine actor ids from the log (kept for later correlation with edit/destroy
    events). Builders that aren't match players are skipped — headline stat is
    ``COUNT(*) GROUP BY builder``.
    """
    match_start = raw.info["aircraftStartTime"]
    eligible = eligible_player_ids(raw)

    rows: list[dict] = []
    for e in raw.build_events:
        builder_id = e["epicId"]
        if builder_id not in eligible:
            continue

        ts = e["timestamp"]
        loc = e.get("location") or {}
        rows.append({
            "match_id": raw.match_id,
            "timestamp": ts,
            "game_time_seconds": (ts - match_start) / 1e6,
            "builder_id": builder_id,
            "build_type": e.get("type"),
            "location_x": loc.get("x"),
            "location_y": loc.get("y"),
            "location_z": loc.get("z"),
            "build_actor_id": e.get("actorId"),
            "edited_actor_id": e.get("editedActorId"),
        })

    return rows
