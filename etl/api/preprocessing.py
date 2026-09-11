import json
from pathlib import Path


def get_players(match_id: str, *, is_bot: bool) -> dict[str, dict]:
    """
    Fetches 
    """
    path = Path("data") / "raw" / f"match_{match_id}" / "players.json"
    players = json.loads(path.read_text())

    return {
        p["epicId"]: p
        for p in players
        if p.get("isBot", False) is is_bot
    }


def get_human_players(match_id: str) -> list[dict]:
    match_players_path = Path("data") / "raw" / f"match_{match_id}" / "players.json"
    match_players = json.loads(match_players_path.read_text())
    human_players = [
        player for player in match_players
        if not player.get("isBot", False)
    ]

    return human_players


def get_bot_players(match_id: str) -> list[dict]:
    match_players_path = Path("data") / "raw" / f"match_{match_id}" / "players.json"
    match_players = json.loads(match_players_path.read_text())
    bot_players = [
        player for player in match_players
        if player.get("isBot", False)
    ]

    return bot_players


def get_human_shots(match_id: str) -> list[dict]:
    match_shot_events_path = f"data/raw/match_{match_id}/shot_events.json"
    with open(match_shot_events_path, "r") as f:
        shot_events = json.load(f)

    human_players = get_human_players(match_id)

    human_players_set: set[str] = {
        player["epicId"] 
        for player in human_players
    }
    human_shot_events = []
    for i, se in enumerate(shot_events):
        # Only keep shots where both shooter and target are human players
        if se.get("epicId") not in human_players_set or se.get("hitEpicId") not in human_players_set:
            continue
        human_shot_events.append(se)

    out_path = f"data/raw/match_{match_id}/human_shot_events.json"
    with open(out_path, "w") as f:
        json.dump(human_shot_events, f, indent=2)
    print(f"✅ Saved to {out_path}")

    return human_shot_events


def get_human_elims(match_id: str) -> list[dict]:
    match_elim_events_path = f"data/raw/match_{match_id}/eliminationEvents.json"
    with open(match_elim_events_path, "r") as f:
        elim_events = json.load(f)

    human_players = get_human_players(match_id)

    human_players_set: set[str] = {
        player["epicId"] 
        for player in human_players
    }
    human_elim_events = []
    for i, ee in enumerate(elim_events):
        if ee["epicId"] not in human_players_set or ee["targetId"] not in human_players_set:
            continue
        human_elim_events.append(ee)

    out_path = f"data/raw/match_{match_id}/human_elim_events.json"
    with open(out_path, "w") as f:
        json.dump(human_elim_events, f, indent=2)
    print(f"✅ Saved to {out_path}")

    return human_elim_events
