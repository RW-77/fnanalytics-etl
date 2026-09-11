import numpy as np

from etl.types import RawMatchData, ParsedTimelineData
from etl.fetching.match_data_fetching import ensure_match_raw
from etl.parsing.timeline.frames import parse_match_frames, TIMELINE_HZ
from etl.parsing.timeline.shots import parse_match_shots
from etl.parsing.basic import parse_match_players

TIMELINE_INTERVAL_SECONDS = 10


def _parse_zone_phases(raw: RawMatchData) -> list[dict]:
    t_0 = raw.info["aircraftStartTime"]
    phases = [
        {
            "phase":       evt["currentPhase"],
            "shrinkStart": (evt["shrinkStartTime"] - t_0) * 1e-6,
            "shrinkEnd":   (evt["shrinkEndTime"]   - t_0) * 1e-6,
            "prevCX": evt["previousCenter"]["x"],
            "prevCY": evt["previousCenter"]["y"],
            "prevR":  evt["previousRadius"],
            "nextCX": evt["nextCenter"]["x"],
            "nextCY": evt["nextCenter"]["y"],
            "nextR":  evt["nextRadius"],
        }
        for evt in raw.zone_update_events
    ]
    return sorted(phases, key=lambda p: p["phase"])


def _build_index_to_team(
    raw: RawMatchData,
    player_to_index: dict[str, int],
) -> dict[str, int]:
    """Map each player's array index to their team number, so replay arrows can
    be team-colored without a second fetch. Built from ``raw.teams`` (each team
    lists its member ``epicId``s) intersected with the player index."""
    team_of = {pid: t["teamId"] for t in raw.teams for pid in t["epicId"]}
    return {
        str(index): team_of[epic_id]
        for epic_id, index in player_to_index.items()
        if epic_id in team_of
    }


def parse_match_timeline(raw: RawMatchData) -> ParsedTimelineData:
    player_to_index, frames = parse_match_frames(raw)
    players = parse_match_players(raw)

    frames_per_chunk = TIMELINE_HZ * TIMELINE_INTERVAL_SECONDS
    frame_chunks = [
        np.asarray(frames[i : i + frames_per_chunk], dtype=np.float32)
        for i in range(0, len(frames), frames_per_chunk)
    ]

    total_frames = len(frames)
    total_chunks = len(frame_chunks)

    metadata = {
        "schema_version": 2,
        "match_id": raw.match_id,
        "hz": TIMELINE_HZ,
        "interval_seconds": TIMELINE_INTERVAL_SECONDS,
        "total_frames": total_frames,
        "total_chunks": total_chunks,
        "duration_seconds": total_frames / TIMELINE_HZ,
        "player_count": len(player_to_index),
        "player_to_index": player_to_index,
        "index_to_team": _build_index_to_team(raw, player_to_index),
        "id_to_username": {p["epic_id"]: p["epic_username"] for p in players},
    }

    zone_phases = _parse_zone_phases(raw)
    shots = parse_match_shots(raw, player_to_index, TIMELINE_HZ)

    return ParsedTimelineData(
        match_id=raw.match_id,
        metadata=metadata,
        frame_chunks=frame_chunks,
        zone_phases=zone_phases,
        shots=shots,
    )


if __name__ == "__main__":
    match_id = "832ceecc424df110d58e3e96d3dff834"
    raw = ensure_match_raw(match_id)
    parsed = parse_match_timeline(raw)
    print(parsed.metadata)
