"""
Handles light data cleaning and fetching common information.
"""

import json
from pathlib import Path


def get_id_to_name_map(match_id: str, path: str = "data/raw") -> dict:
    """
    Returns a mapping of player IDs to Epic usernames for all players for 
    given match.
    """
    players_file = Path(path) / f"match_{match_id}" / "players.json"

    if not players_file.exists():
        print(f"No players.json found at: {players_file}")
        return {}

    data = json.loads(players_file.read_text())

    players = data
    if players:
        return {
            p["epicId"]: p["epicUsername"] 
            for p in players
            if not (
                p["isSpectator"]
                or p["isBot"]
                or p["epicUsername"].startswith("BLAST_")
                or p["epicUsername"].startswith("OBS_")
            )
        }
    print(f"No players found in {players_file}")
    print(json.dumps(data, indent=2))
    return {}
