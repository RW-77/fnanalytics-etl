from etl.types import RawMatchData
from etl.parsing.basic import _eligible_player_ids


def parse_time_alive(raw: RawMatchData) -> list[dict]:
    """One row per contiguous alive span, per player.

    A player is alive from match start; an elimination where they are the
    ``targetId`` (including self-eliminations — storm/fall still kills) ends the
    current span, and a reboot (``epicId`` in a reboot event) starts a new one.
    Anyone still alive at match end gets a final span closed at the match's end
    time. Eliminations and reboots are the only two events that toggle the
    alive/dead state, so together they fully reconstruct the timeline.

    Times are game-time seconds since ``aircraftStartTime``. Spans are clamped to
    ``[0, match_end]`` and zero-length spans are dropped.
    """
    info = raw.info
    match_start = info["aircraftStartTime"]

    # Match end (µs): prefer the explicit end timestamp, fall back to length.
    end_ts = info.get("endTimestamp")
    if end_ts is None:
        end_ts = match_start + info["lengthMs"] * 1000
    match_end_seconds = (end_ts - match_start) / 1e6

    eligible = _eligible_player_ids(raw)

    # Per player: state-change times. deaths -> dead, reboots -> alive.
    deaths: dict[str, list[float]] = {p: [] for p in eligible}
    reboots: dict[str, list[float]] = {p: [] for p in eligible}

    for e in raw.elimination_events:
        target = e.get("targetId")
        if target in eligible:
            deaths[target].append((e["timestamp"] - match_start) / 1e6)

    for e in raw.reboot_events:
        rebooted = e.get("epicId")
        if rebooted in eligible:
            reboots[rebooted].append((e["timestamp"] - match_start) / 1e6)

    rows: list[dict] = []
    for pid in eligible:
        # Merge deaths (kind 0) and reboots (kind 1) into one ordered timeline.
        # On a tie, process the death first — a reboot only follows a death.
        timeline = sorted(
            [(t, 0) for t in deaths[pid]] + [(t, 1) for t in reboots[pid]]
        )

        alive = True
        seg_start = 0.0
        for t, kind in timeline:
            if kind == 0:  # death
                if alive:
                    end = min(t, match_end_seconds)
                    if end > seg_start:
                        rows.append({
                            "match_id": raw.match_id,
                            "player_id": pid,
                            "start_seconds": seg_start,
                            "end_seconds": end,
                        })
                    alive = False
            else:  # reboot
                if not alive:
                    seg_start = min(t, match_end_seconds)
                    alive = True

        if alive and match_end_seconds > seg_start:
            rows.append({
                "match_id": raw.match_id,
                "player_id": pid,
                "start_seconds": seg_start,
                "end_seconds": match_end_seconds,
            })

    return rows
