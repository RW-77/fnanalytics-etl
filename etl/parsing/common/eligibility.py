"""Shared player-eligibility helpers.

A match's ``players`` list includes spectators and bots. Almost every parser
needs the same filter — the real, human participants that map to
``MatchPlayer`` rows — so it lives here rather than being re-defined per module.
"""

from etl.types import RawMatchData


def is_match_player(player: dict) -> bool:
    return not player["isSpectator"] and not player["isBot"]


def eligible_player_ids(raw: RawMatchData) -> set[str]:
    return {
        player["epicId"]
        for player in raw.players
        if is_match_player(player)
    }
