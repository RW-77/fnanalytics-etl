from datetime import datetime, timedelta

from etl.types import RawMatchData
from etl.parsing.common.eligibility import is_match_player
from etl.parsing.tournament.map_modes import resolve_mode_id


def parse_match_metadata(raw: RawMatchData):
    match_info = raw.info

    return {
        "match_id": raw.match_id,
        "session_id": match_info.get("serverId"),
        "event_id": match_info["eventId"],
        "event_window_id": match_info["eventWindowId"],
        "map_path": match_info["mapPath"],
        "mode_id": resolve_mode_id(match_info["mapPath"]),
        "start_time": datetime.fromtimestamp(match_info["aircraftStartTime"] / 1e6),
        "end_time": (
            datetime.fromtimestamp(match_info["endTimestamp"] / 1e6)
            if match_info.get("endTimestamp")
            else None
        ),
        "gamemode": match_info["gameMode"],
        "duration": timedelta(milliseconds=match_info["lengthMs"]),
        "player_count": match_info["playerCount"],
        "build_major": match_info["buildMajor"],
        "build_minor": match_info["buildMinor"],
    }


def parse_match_players(raw: RawMatchData) -> list[dict]:
    match_players = raw.players

    players = []
    for p in match_players:
        if not is_match_player(p):
            continue

        players.append({
            "epic_id": p["epicId"],
            "epic_username": p["epicUsername"],
        })

    print(f"Parsed {len(players)} players from {len(match_players)} total")
    return players
