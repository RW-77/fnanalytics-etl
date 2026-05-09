from datetime import datetime

from sqlalchemy.orm import Session

from etl.db.models import (
    EventWindow, 
    Match, 
    MatchPlayer, 
    DamageDealtEvent, 
    EliminationEvent, 
)
from etl.types import ParsedMatchData


def load_event_window_metadata(event_window_metadata: dict, session: Session) -> EventWindow: 
    event_window_id = event_window_metadata["event_window_id"]
    existing_event_window = session.query(EventWindow).filter_by(event_window_id=event_window_id).first()
    if existing_event_window:
        print(f"Event window {event_window_id} already exists in database")
        return existing_event_window
    
    event_window = EventWindow(
        event_window_id=event_window_metadata["event_window_id"],
        start_time=event_window_metadata["start_time"],
        end_time=event_window_metadata["end_time"],
        total_matches=event_window_metadata["total_matches"]
    )

    session.add(event_window)
    session.flush()

    print(f"✅ Created event window record: {event_window_id}")

    return event_window


def load_match_metadata(match_metadata: dict, session: Session) -> Match:
    match_id = match_metadata["match_id"]
    existing_match = session.query(Match).filter_by(match_id=match_id).first()
    if existing_match:
        print(f"Match {match_id} already exists in database")
        return existing_match
    
    match = Match(
        match_id=match_metadata["match_id"],
        event_window_id=match_metadata["event_window_id"],
        event_id=match_metadata["event_id"],
        start_time=match_metadata["start_time"],
        end_time=match_metadata["end_time"],
        gamemode=match_metadata["gamemode"],
        duration=match_metadata["duration"],
        player_count=match_metadata["player_count"],
    )

    session.add(match)
    session.flush()

    print(f"✅ Created match record: {match_id}")
    return match


def load_match_players(
    players_data: list[dict], 
    match_id: str, 
    session: Session
) -> dict[str, int]:
    """
    Create MatchPlayer records for all players in a match.
    
    Args:
        players_data: List of player dictionaries with keys:
            - epic_id (str): Player's Epic ID
            - epic_username (str): Player's Epic username
        match_id: The match these players belong to
        session: SQLAlchemy session
        
    Returns:
        dict[str, int]: Mapping of Epic ID to MatchPlayer primary key
    """
    if not players_data:
        print("⚠️  No players to load")
        return {}
    
    print(f"Loading {len(players_data)} players for match {match_id}...")
    
    existing_players = session.query(MatchPlayer).filter_by(match_id=match_id).all()
    existing_player_ids = {
        player.epic_id
        for player in existing_players
    }
    
    players_created = 0
    for player in players_data:
        player_id = player["epic_id"]
        
        # Check if player already exists for this match
        if player_id in existing_player_ids:
            continue
        
        # Create new player record
        new_player = MatchPlayer(
            epic_id=player_id,
            epic_username=player["epic_username"],
            match_id=match_id
        )
        
        session.add(new_player)
        players_created += 1
    
    session.flush()
    
    if players_created > 0:
        print(f"✅ Created {players_created} new player records")
    else:
        print(f"ℹ️  All {len(players_data)} players already exist for this match")

    player_rows = session.query(MatchPlayer).filter_by(match_id=match_id).all()
    return {
        player.epic_id: player.id
        for player in player_rows
    }
    

def load_damage_events(
    damage_events: list[dict], 
    match_id: str, 
    player_id_map: dict[str, int], 
    session: Session
) -> int:
    """
    Bulk insert damage dealt events into the database.
    
    Args:
        `damage_events`: List of damage event dictionaries from parse_damage_dealt() with keys:
            - `timestamp` (int): Unix timestamp in milliseconds
            - `actor_id` (str): Shooter's Epic ID
            - `recipient_id` (str): Victim's Epic ID
            - `weapon_id` (str): Weapon identifier
            - `damage` (float): Damage amount dealt
            - `ax`, `ay`, `az` (float): Actor's 3D coordinates
            - `rx`, `ry`, `rz` (float): Recipient's 3D coordinates
            - `distance` (float): Distance between actors
            - `zone` (int): Storm zone number
        `match_id`: The match these events belong to
        `session`: SQLAlchemy session
        
    Returns:
        int: Number of events loaded
    """
    if not damage_events:
        print("⚠️  No damage events to load")
        return 0
    
    print(f"Loading {len(damage_events)} damage events...")
    
    # Prepare records for bulk insert
    damage_records = []
    for event in damage_events:
        # Convert timestamp from milliseconds to datetime
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1000)
        
        actor_db_id = player_id_map.get(event["actor_id"])
        recipient_db_id = player_id_map.get(event["recipient_id"])

        if actor_db_id is None or recipient_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for damage event in match {match_id}: "
                f"actor={event['actor_id']}, recipient={event['recipient_id']}"
            )

        damage_records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "actor_id": actor_db_id,
            "recipient_id": recipient_db_id,
            "weapon_id": event["weapon_id"],
            "weapon_type": None,  # TODO: Add weapon type mapping if available
            "damage_amount": event["damage"],  # Note: parse_damage_dealt returns "damage", not "damage_amount"
            "actor_x": event["ax"],
            "actor_y": event["ay"],
            "actor_z": event["az"],
            "recipient_x": event["rx"],
            "recipient_y": event["ry"],
            "recipient_z": event["rz"],
            "distance": event["distance"],
            "zone": event["zone"]
        })
    
    # Bulk insert using SQLAlchemy
    session.bulk_insert_mappings(DamageDealtEvent, damage_records) # type: ignore
    
    print(f"✅ Loaded {len(damage_records)} damage events")
    return len(damage_records)


def load_elimination_events(
    elim_events: list[dict], 
    match_id: str,
    player_id_map: dict[str, int],
    session: Session
) -> int:
    """
    Bulk insert elimination events into the database.
    
    Args:
        elim_events: List of elimination event dictionaries from parse_elims() with keys:
            - timestamp (int): Unix timestamp in milliseconds
            - actor_id (str): Eliminator's Epic ID
            - recipient_id (str): Victim's Epic ID
            - weapon_id (str): Weapon identifier
            - ax, ay, az (float): Actor's 3D coordinates
            - rx, ry, rz (float): Recipient's 3D coordinates
            - distance (float): Distance between actors
            - zone (int): Storm zone number
        match_id: The match these events belong to
        session: SQLAlchemy session
        
    Returns:
        int: Number of events loaded
    """
    if not elim_events:
        print("⚠️  No elimination events to load")
        return 0
    
    print(f"Loading {len(elim_events)} elimination events...")
    
    # Prepare records for bulk insert
    elim_records = []
    for event in elim_events:
        # Convert timestamp from milliseconds to datetime
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1000)

        actor_db_id = player_id_map.get(event["actor_id"])
        recipient_db_id = player_id_map.get(event["recipient_id"])

        if actor_db_id is None or recipient_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for elimination event in match {match_id}: "
                f"actor={event['actor_id']}, recipient={event['recipient_id']}"
            )
        
        elim_records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "actor_id": actor_db_id,
            "recipient_id": recipient_db_id,
            "weapon_id": event["weapon_id"],
            "weapon_type": None,  # TODO: Add weapon type mapping if available
            "actor_x": event["ax"],
            "actor_y": event["ay"],
            "actor_z": event["az"],
            "recipient_x": event["rx"],
            "recipient_y": event["ry"],
            "recipient_z": event["rz"],
            "distance": event["distance"],
            "zone": event["zone"]
        })
    
    # Bulk insert using SQLAlchemy
    session.bulk_insert_mappings(EliminationEvent, elim_records) # type: ignore
    
    print(f"✅ Loaded {len(elim_records)} elimination events")
    return len(elim_records)


def load_match(parsed: ParsedMatchData, event_window_id: str, session: Session) -> None:

    metadata = dict(parsed.metadata)
    metadata["event_window_id"] = event_window_id

    load_match_metadata(metadata, session)
    player_id_map = load_match_players(parsed.players, parsed.match_id, session)
    load_damage_events(parsed.damage, parsed.match_id, player_id_map, session)
    load_elimination_events(parsed.elims, parsed.match_id, player_id_map, session)
    session.commit()


if __name__ == "__main__":
    # Initialize database tables (run once)
    # init_db()
    
    # Load match
    match_id = "832ceecc424df110d58e3e96d3dff834"
    
    # Need to get the match start timestamp from match_info.json
    import json
    with open(f"data/raw/match_{match_id}/match_info.json") as f:
        match_info = json.load(f)
        match_start = match_info["startTimestamp"]
