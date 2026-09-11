from etl.types import RawMatchData


def parse_revives(raw: RawMatchData) -> list[dict]:
    """One row per (revived player, reviver).

    In ``reviveEvents`` the ``epicId`` is the player who was revived from the
    knocked (DBNO) state, and ``reviverIds`` is the list of teammate(s) who
    revived them — usually one, occasionally more. We fan that out into one row
    per reviver, each carrying the same ``revived_id`` / ``timestamp``.

    A revive with an empty ``reviverIds`` still yields a single row with
    ``reviver_id=None`` (a player can be revived without a teammate), so the
    revive itself is always recorded. ``hadNonPlayerHelp`` is ignored.
    """
    match_start = raw.info["aircraftStartTime"]

    rows: list[dict] = []
    for e in raw.revive_events:
        revived_id = e["epicId"]
        ts = e["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6
        # `... or [None]` covers both an empty list and a missing key.
        for reviver_id in (e.get("reviverIds") or [None]):
            rows.append({
                "match_id": raw.match_id,
                "timestamp": ts,
                "game_time_seconds": game_time_seconds,
                "revived_id": revived_id,
                "reviver_id": reviver_id,
            })

    return rows
