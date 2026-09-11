from etl.types import RawMatchData


def parse_reboots(raw: RawMatchData) -> list[dict]:
    """One row per (rebooted player, rebooter).

    In ``rebootEvents`` the ``epicId`` is the player who was rebooted, and
    ``rebooterIds`` is the list of teammate(s) who rebooted them — usually one,
    occasionally more. We fan that out into one row per rebooter, each carrying
    the same ``rebooted_id`` / ``timestamp``.

    A reboot with an empty ``rebooterIds`` still yields a single row with
    ``rebooter_id=None``, so the reboot itself is always recorded (it's a needed
    input for the time-alive stat). ``otherTeamRebooterIds`` is ignored for now
    (always empty in observed data).
    """
    match_start = raw.info["aircraftStartTime"]

    rows: list[dict] = []
    for e in raw.reboot_events:
        rebooted_id = e["epicId"]
        ts = e["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6
        # `... or [None]` covers both an empty list and a missing key.
        for rebooter_id in (e.get("rebooterIds") or [None]):
            rows.append({
                "match_id": raw.match_id,
                "timestamp": ts,
                "game_time_seconds": game_time_seconds,
                "rebooted_id": rebooted_id,
                "rebooter_id": rebooter_id,
            })

    return rows
