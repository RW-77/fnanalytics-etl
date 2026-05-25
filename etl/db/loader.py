from datetime import datetime

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from etl.db.models import (
    EventWindow,
    Match,
    MatchPlayer,
    DamageDealtEvent,
    EliminationEvent,
    Tournament,
)
from etl.parsing.tournament_metadata import TournamentMetadata
from etl.types import ParsedMatchData


def ensure_tournament(metadata: TournamentMetadata, session: Session) -> None:
    """Insert a Tournament row if one doesn't already exist.

    Uses ``INSERT … ON CONFLICT DO NOTHING`` so that any field manually
    edited downstream (a curated title, a season fix) is never clobbered
    by a re-ingest. To force a re-derivation of an existing row, delete
    the row first or update it explicitly.
    """
    stmt = (
        pg_insert(Tournament)
        .values(
            tournament_id=metadata.tournament_id,
            title=metadata.title,
            season_code=metadata.season_code,
        )
        .on_conflict_do_nothing(index_elements=[Tournament.tournament_id])
    )
    session.execute(stmt)


def load_event_window_metadata(event_window_metadata: dict, session: Session) -> EventWindow:
    """Upsert an EventWindow row.

    Columns not present in ``event_window_metadata`` (e.g. ``status``,
    ``last_processed``, ``created_at``) are preserved on existing rows
    because ``Session.merge`` only copies attributes that were explicitly
    set on the source object.
    """
    event_window = EventWindow(**event_window_metadata)
    return session.merge(event_window)


def load_match_metadata(match_metadata: dict, session: Session) -> Match:
    """Upsert a Match row, preserving processing-status columns.

    See :func:`load_event_window_metadata` for the merge semantics that
    make this safe across re-ingests.
    """
    match = Match(**match_metadata)
    return session.merge(match)


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

    # Pull only the two columns we need for the lookup map; avoids loading
    # full ORM objects we don't intend to use. ``.tuples()`` gives Pyright
    # a concrete ``Sequence[tuple[str, int]]`` so ``dict()`` resolves cleanly.
    id_map: dict[str, int] = dict(
        session.execute(
            select(MatchPlayer.epic_id, MatchPlayer.id)
            .where(MatchPlayer.match_id == match_id)
        ).tuples().all()
    )

    new_rows = [
        {
            "epic_id": p["epic_id"],
            "epic_username": p["epic_username"],
            "match_id": match_id,
        }
        for p in players_data
        if p["epic_id"] not in id_map
    ]

    if not new_rows:
        print(f"ℹ️  All {len(players_data)} players already exist for this match")
        return id_map

    # Insert + RETURNING in one round trip. SQLAlchemy 2.x's
    # "insertmanyvalues" feature preserves the input order on PostgreSQL,

    result = session.execute(
        insert(MatchPlayer).returning(MatchPlayer.epic_id, MatchPlayer.id),
        new_rows,
    )
    for epic_id, pk in result:
        id_map[epic_id] = pk

    print(f"✅ Created {len(new_rows)} new player records")
    return id_map
    

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
            - `timestamp` (int): Unix timestamp in microseconds
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
        # Raw Osirion event timestamps are in microseconds.
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)
        
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
    session.execute(insert(DamageDealtEvent), damage_records)
    
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
            - timestamp (int): Unix timestamp in microseconds
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
        # Raw Osirion event timestamps are in microseconds.
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

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
    session.execute(insert(EliminationEvent), elim_records)
    
    print(f"✅ Loaded {len(elim_records)} elimination events")
    return len(elim_records)


def load_match_relational(parsed: ParsedMatchData, event_window_id: str, session: Session) -> Match:
    """Idempotently (re-)load a single match and all of its child rows.

    The Match row itself is upserted so that processing-status columns
    (``status``, ``last_processed``, …) survive a re-ingest. The child
    rows are wiped and re-inserted: deleting MatchPlayer rows cascades
    to DamageDealtEvent and EliminationEvent via the ``ON DELETE
    CASCADE`` foreign keys on ``actor_id`` / ``recipient_id``.
    """
    metadata = dict(parsed.metadata)

    raw_event_window_id = metadata["event_window_id"]
    if raw_event_window_id != event_window_id:
        raise ValueError(
            f"Match {parsed.match_id} belongs to event window {raw_event_window_id}, "
            f"but was queued under {event_window_id}."
        )

    match = load_match_metadata(metadata, session)

    # Wipe child rows; CASCADE on the event-table FKs handles the rest.
    session.execute(
        delete(MatchPlayer).where(MatchPlayer.match_id == parsed.match_id)
    )
    session.flush()  # ensure DELETE lands before we re-INSERT players

    player_id_map = load_match_players(parsed.players, parsed.match_id, session)
    load_damage_events(parsed.damage, parsed.match_id, player_id_map, session)
    load_elimination_events(parsed.elims, parsed.match_id, player_id_map, session)
    return match


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
