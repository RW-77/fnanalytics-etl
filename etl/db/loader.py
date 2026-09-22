import math
from datetime import datetime, timezone

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from etl.db.models import (
    EventWindow,
    FireWeaponEvent,
    Map,
    Match,
    MatchPlayer,
    DamageDealtEvent,
    DamageContributionEvent,
    AssistEvent,
    ShotAttemptEvent,
    RebootEvent,
    ReviveEvent,
    BuildPlacedEvent,
    AliveInterval,
    EventWindowPlayer,
    EventWindowTeam,
    EventWindowTeamMatch,
    EliminationEvent,
    Tournament,
    Event,
    Weapon,
)
from etl.parsing.tournament.metadata import TournamentMetadata, EventMetadata
from etl.db.context import LoadContext


def load_tournament(metadata: TournamentMetadata, session: Session) -> None:
    """Upsert a Tournament row.

    On insert: writes all three fields (id, title, season_code).
    On conflict: refreshes ``season_code`` from the latest derivation but
    leaves ``title`` untouched, so any manually-curated title — or a
    title set from an older rule that you've since changed — survives
    re-ingest.

    To force a re-derivation of an existing tournament's title, delete
    the row (``DELETE FROM tournaments WHERE tournament_id = '…'``) and
    re-run, or UPDATE the column directly.
    """
    stmt = pg_insert(Tournament).values(
        tournament_id=metadata.tournament_id,
        title=metadata.title,
        season_code=metadata.season_code,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Tournament.tournament_id],
        set_={"season_code": stmt.excluded.season_code},
    )
    session.execute(stmt)


def load_event(metadata: EventMetadata, session: Session) -> None:
    """Upsert an Event row.

    All four columns are ETL-derived — ``image_key`` follows the
    ``<event_id>.jpg`` convention set in :func:`parse_event_metadata`,
    so the ETL is the sole writer and refreshing it on conflict is
    correct. If a per-event manual override ever becomes desirable
    (different extension, custom branding), drop the corresponding key
    from ``set_`` and the existing column value will be preserved on
    re-ingest.
    """
    stmt = pg_insert(Event).values(
        event_id=metadata.event_id,
        region_code=metadata.region_code,
        season_code=metadata.season_code,
        image_key=metadata.image_key,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Event.event_id],
        set_={
            "region_code": stmt.excluded.region_code,
            "season_code": stmt.excluded.season_code,
            "image_key": stmt.excluded.image_key,
        },
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


def load_event_window_players(
    player_rows: list[dict], event_window_id: str, session: Session
) -> int:
    """Bulk insert a window's leaderboard players.

    ``player_rows`` come from :func:`etl.parsing.tournament.leaderboard.
    build_leaderboard_player_rows`; each dict already carries the exact
    ``event_window_players`` columns (``event_window_id``, ``epic_id``,
    ``epic_username``, ``flag_token``). Owns its rows: clears this window's
    existing rows before (re)inserting so a re-ingest is idempotent.
    """
    session.execute(
        delete(EventWindowPlayer).where(
            EventWindowPlayer.event_window_id == event_window_id
        )
    )

    if not player_rows:
        print(f"⚠️  No leaderboard players to load for {event_window_id}")
        return 0

    print(f"Loading {len(player_rows)} leaderboard players for {event_window_id}...")
    session.execute(insert(EventWindowPlayer), player_rows)
    print(f"✅ Loaded {len(player_rows)} leaderboard players")
    return len(player_rows)


def load_event_window_teams(
    team_rows: list[dict], event_window_id: str, session: Session
) -> int:
    """Bulk insert a window's team standings.

    ``team_rows`` come from :func:`etl.parsing.tournament.leaderboard.build_leaderboard_rows`;
    each dict already carries the exact ``event_window_teams`` columns. Owns its
    rows: clears this window's existing rows before (re)inserting.
    """
    session.execute(
        delete(EventWindowTeam).where(
            EventWindowTeam.event_window_id == event_window_id
        )
    )

    if not team_rows:
        print(f"⚠️  No leaderboard teams to load for {event_window_id}")
        return 0

    print(f"Loading {len(team_rows)} leaderboard teams for {event_window_id}...")
    session.execute(insert(EventWindowTeam), team_rows)
    print(f"✅ Loaded {len(team_rows)} leaderboard teams")
    return len(team_rows)


def load_event_window_team_matches(
    match_rows: list[dict], event_window_id: str, session: Session
) -> int:
    """Bulk insert a window's per-game team results.

    ``match_rows`` come from :func:`etl.parsing.tournament.leaderboard.build_leaderboard_rows`;
    each dict already carries the exact ``event_window_team_matches`` columns
    (including ``match_point_bonus``). Owns its rows: clears this window's
    existing rows before (re)inserting.
    """
    session.execute(
        delete(EventWindowTeamMatch).where(
            EventWindowTeamMatch.event_window_id == event_window_id
        )
    )

    if not match_rows:
        print(f"⚠️  No leaderboard team-matches to load for {event_window_id}")
        return 0

    print(
        f"Loading {len(match_rows)} leaderboard team-matches for {event_window_id}..."
    )
    session.execute(insert(EventWindowTeamMatch), match_rows)
    print(f"✅ Loaded {len(match_rows)} leaderboard team-matches")
    return len(match_rows)


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
    

def load_damage_events(damage_events: list[dict], ctx: LoadContext) -> int:
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
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(DamageDealtEvent).where(DamageDealtEvent.match_id == ctx.match_id)
    )

    if not damage_events:
        print("⚠️  No damage events to load")
        return 0
    
    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

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


def load_elimination_events(elim_events: list[dict], ctx: LoadContext) -> int:
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
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(EliminationEvent).where(EliminationEvent.match_id == ctx.match_id)
    )

    if not elim_events:
        print("⚠️  No elimination events to load")
        return 0
    
    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

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


def load_shot_events(shot_events: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert fire weapon events into the database.

    Args:
        shot_events: List of shot event dicts from parse_shots() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - zone (int): Storm zone number
            - epic_id (str): Shooter's Epic ID
            - weapon_id (str): Weapon identifier
            - damage, actual_damage (float): Damage values
            - harvest, hit_player, hit_critical, hit_player_build,
              hit_fatal, hit_shield, hit_ballistic, destroyed_shield (bool)
            - hit_epic_id (str): Hit player's Epic ID, or "" if no player hit
            - end_x, end_y, end_z (float): Shot endpoint location
            - hit_result (str): e.g. "HIT_NOTHING"
            - item_entry_guid (str | None): Specific weapon instance GUID
            - hit_actor_id (int | None): Server-side numeric actor ID
        match_id: The match these events belong to
        player_id_map: epic_id -> MatchPlayer.id, from load_match_players()
        session: SQLAlchemy session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(FireWeaponEvent).where(FireWeaponEvent.match_id == ctx.match_id)
    )

    if not shot_events:
        print("⚠️  No shot events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(shot_events)} shot events...")

    records = []
    for event in shot_events:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        actor_db_id = player_id_map.get(event["epic_id"])
        if actor_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for shot event in match {match_id}: "
                f"actor={event['epic_id']}"
            )

        # hit_epic_id is "" when no player was hit — resolve to None
        hit_epic_id = event["hit_epic_id"] or None
        hit_player_db_id = player_id_map.get(hit_epic_id) if hit_epic_id else None

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event["game_time_seconds"],
            "zone": event["zone"],
            "actor_id": actor_db_id,
            "weapon_id": event["weapon_id"],
            "damage": event["damage"],
            "actual_damage": event["actual_damage"],
            "harvest": event["harvest"],
            "hit_player": event["hit_player"],
            "hit_critical": event["hit_critical"],
            "hit_player_build": event["hit_player_build"],
            "hit_fatal": event["hit_fatal"],
            "hit_shield": event["hit_shield"],
            "hit_ballistic": event["hit_ballistic"],
            "destroyed_shield": event["destroyed_shield"],
            "hit_player_id": hit_player_db_id,
            "hit_result": event["hit_result"],
            "actor_x": event.get("actor_x"),
            "actor_y": event.get("actor_y"),
            "actor_z": event.get("actor_z"),
            "end_x": event["end_x"],
            "end_y": event["end_y"],
            "end_z": event["end_z"],
            "distance": (
                math.sqrt(
                    (event["end_x"] - event["actor_x"]) ** 2 +
                    (event["end_y"] - event["actor_y"]) ** 2 +
                    (event["end_z"] - event["actor_z"]) ** 2
                )
                if event.get("actor_x") is not None else None
            ),
            "item_entry_guid": event.get("item_entry_guid"),
            "hit_actor_id": event.get("hit_actor_id"),
        })

    session.execute(insert(FireWeaponEvent), records)

    print(f"✅ Loaded {len(records)} shot events")
    return len(records)


def load_damage_contribution_events(dce_events: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert damage-contribution-on-elimination events.

    Args:
        dce_events: List of dicts from parse_damage_contribution_on_elims() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - actor_id (str): Credited dealer's Epic ID
            - victim_id (str): Eliminated opponent's Epic ID
            - killer_id (str): Eliminator's Epic ID (on the dealer's team)
            - amount (float): Un-healed, overkill-capped damage credited
        match_id: The match these events belong to
        player_id_map: epic_id -> MatchPlayer.id, from load_match_players()
        session: SQLAlchemy session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(DamageContributionEvent).where(DamageContributionEvent.match_id == ctx.match_id)
    )

    if not dce_events:
        print("⚠️  No damage contribution events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(dce_events)} damage contribution events...")

    records = []
    for event in dce_events:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        actor_db_id = player_id_map.get(event["actor_id"])
        victim_db_id = player_id_map.get(event["victim_id"])
        killer_db_id = player_id_map.get(event["killer_id"])

        if actor_db_id is None or victim_db_id is None or killer_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for damage contribution event in match {match_id}: "
                f"actor={event['actor_id']}, victim={event['victim_id']}, killer={event['killer_id']}"
            )

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "actor_id": actor_db_id,
            "victim_id": victim_db_id,
            "killer_id": killer_db_id,
            "damage_amount": event["amount"],
        })

    session.execute(insert(DamageContributionEvent), records)

    print(f"✅ Loaded {len(records)} damage contribution events")
    return len(records)


def load_assist_events(assist_events: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert assist events.

    Args:
        assist_events: List of dicts from parse_assists() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - actor_id (str): Assisting teammate's Epic ID
            - victim_id (str): Eliminated opponent's Epic ID
            - killer_id (str): Eliminator's Epic ID (never the assister)
        match_id: The match these events belong to
        player_id_map: epic_id -> MatchPlayer.id, from load_match_players()
        session: SQLAlchemy session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(AssistEvent).where(AssistEvent.match_id == ctx.match_id)
    )

    if not assist_events:
        print("⚠️  No assist events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(assist_events)} assist events...")

    records = []
    for event in assist_events:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        actor_db_id = player_id_map.get(event["actor_id"])
        victim_db_id = player_id_map.get(event["victim_id"])
        killer_db_id = player_id_map.get(event["killer_id"])

        if actor_db_id is None or victim_db_id is None or killer_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for assist event in match {match_id}: "
                f"actor={event['actor_id']}, victim={event['victim_id']}, killer={event['killer_id']}"
            )

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "actor_id": actor_db_id,
            "victim_id": victim_db_id,
            "killer_id": killer_db_id,
        })

    session.execute(insert(AssistEvent), records)

    print(f"✅ Loaded {len(records)} assist events")
    return len(records)


def load_shot_attempt_events(shot_attempts: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert shot-attempt events.

    Args:
        shot_attempts: List of dicts from parse_shot_attempts() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - actor_id (str): Shooter's Epic ID
            - recipient_id (str): Targeted opponent's Epic ID
            - weapon_id (str): Weapon identifier
            - direct_hit (bool): True if the bullet hit the recipient, False if
              it hit a build/terrain while aimed at them
            - passing_distance (float): Perpendicular distance from the
              recipient's hitbox centre to the bullet path (0 for a direct hit)
        match_id: The match these events belong to
        player_id_map: epic_id -> MatchPlayer.id, from load_match_players()
        session: SQLAlchemy session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(ShotAttemptEvent).where(ShotAttemptEvent.match_id == ctx.match_id)
    )

    if not shot_attempts:
        print("⚠️  No shot attempt events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(shot_attempts)} shot attempt events...")

    records = []
    for event in shot_attempts:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        actor_db_id = player_id_map.get(event["actor_id"])
        recipient_db_id = player_id_map.get(event["recipient_id"])

        if actor_db_id is None or recipient_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for shot attempt event in match {match_id}: "
                f"actor={event['actor_id']}, recipient={event['recipient_id']}"
            )

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "actor_id": actor_db_id,
            "recipient_id": recipient_db_id,
            "weapon_id": event.get("weapon_id"),
            "direct_hit": event["direct_hit"],
            "passing_distance": event["passing_distance"],
        })

    session.execute(insert(ShotAttemptEvent), records)

    print(f"✅ Loaded {len(records)} shot attempt events")
    return len(records)


def load_reboot_events(reboot_events: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert reboot events.

    Args:
        reboot_events: List of dicts from parse_reboots() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - rebooted_id (str): Epic ID of the player who was rebooted
            - rebooter_id (str | None): Epic ID of the teammate who rebooted
              them, or None when the log lists no rebooter
        ctx: LoadContext carrying match_id, player_id_map, and session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(RebootEvent).where(RebootEvent.match_id == ctx.match_id)
    )

    if not reboot_events:
        print("⚠️  No reboot events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(reboot_events)} reboot events...")

    records = []
    for event in reboot_events:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        rebooted_db_id = player_id_map.get(event["rebooted_id"])
        if rebooted_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for reboot event in match {match_id}: "
                f"rebooted={event['rebooted_id']}"
            )

        # rebooter_id is nullable — only map it when present.
        rebooter_db_id = None
        if event["rebooter_id"] is not None:
            rebooter_db_id = player_id_map.get(event["rebooter_id"])
            if rebooter_db_id is None:
                raise ValueError(
                    f"Missing MatchPlayer row for reboot event in match {match_id}: "
                    f"rebooter={event['rebooter_id']}"
                )

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "rebooted_id": rebooted_db_id,
            "rebooter_id": rebooter_db_id,
        })

    session.execute(insert(RebootEvent), records)

    print(f"✅ Loaded {len(records)} reboot events")
    return len(records)


def load_revive_events(revive_events: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert revive events.

    Args:
        revive_events: List of dicts from parse_revives() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - revived_id (str): Epic ID of the player who was revived
            - reviver_id (str | None): Epic ID of the teammate who revived
              them, or None when the log lists no reviver
        ctx: LoadContext carrying match_id, player_id_map, and session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(ReviveEvent).where(ReviveEvent.match_id == ctx.match_id)
    )

    if not revive_events:
        print("⚠️  No revive events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(revive_events)} revive events...")

    records = []
    for event in revive_events:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        revived_db_id = player_id_map.get(event["revived_id"])
        if revived_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for revive event in match {match_id}: "
                f"revived={event['revived_id']}"
            )

        # reviver_id is nullable — only map it when present.
        reviver_db_id = None
        if event["reviver_id"] is not None:
            reviver_db_id = player_id_map.get(event["reviver_id"])
            if reviver_db_id is None:
                raise ValueError(
                    f"Missing MatchPlayer row for revive event in match {match_id}: "
                    f"reviver={event['reviver_id']}"
                )

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "revived_id": revived_db_id,
            "reviver_id": reviver_db_id,
        })

    session.execute(insert(ReviveEvent), records)

    print(f"✅ Loaded {len(records)} revive events")
    return len(records)


def load_builds_placed(build_events: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert build-placed events.

    Args:
        build_events: List of dicts from parse_builds_placed() with keys:
            - timestamp (int): Unix timestamp in microseconds
            - game_time_seconds (float): Seconds since match start
            - builder_id (str): Placing player's Epic ID
            - build_type (str | None): The piece's asset name
            - location_x/y/z (float | None): Placement position
            - build_actor_id / edited_actor_id (int | None): Engine actor ids
        ctx: LoadContext carrying match_id, player_id_map, and session

    Returns:
        int: Number of events loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(BuildPlacedEvent).where(BuildPlacedEvent.match_id == ctx.match_id)
    )

    if not build_events:
        print("⚠️  No build placed events to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(build_events)} build placed events...")

    records = []
    for event in build_events:
        timestamp_dt = datetime.fromtimestamp(event["timestamp"] / 1e6)

        builder_db_id = player_id_map.get(event["builder_id"])
        if builder_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for build placed event in match {match_id}: "
                f"builder={event['builder_id']}"
            )

        records.append({
            "match_id": match_id,
            "timestamp": timestamp_dt,
            "game_time_seconds": event.get("game_time_seconds"),
            "builder_id": builder_db_id,
            "build_type": event.get("build_type"),
            "location_x": event.get("location_x"),
            "location_y": event.get("location_y"),
            "location_z": event.get("location_z"),
            "build_actor_id": event.get("build_actor_id"),
            "edited_actor_id": event.get("edited_actor_id"),
        })

    session.execute(insert(BuildPlacedEvent), records)

    print(f"✅ Loaded {len(records)} build placed events")
    return len(records)


def load_alive_intervals(alive_intervals: list[dict], ctx: LoadContext) -> int:
    """
    Bulk insert alive intervals.

    Args:
        alive_intervals: List of dicts from parse_time_alive() with keys:
            - player_id (str): The player's Epic ID
            - start_seconds (float): Span start, game-time seconds since start
            - end_seconds (float): Span end, game-time seconds since start
        ctx: LoadContext carrying match_id, player_id_map, and session

    Returns:
        int: Number of intervals loaded
    """
    # Own our rows: clear this match's existing rows before (re)inserting.
    ctx.session.execute(
        delete(AliveInterval).where(AliveInterval.match_id == ctx.match_id)
    )

    if not alive_intervals:
        print("⚠️  No alive intervals to load")
        return 0

    match_id = ctx.match_id
    player_id_map = ctx.player_id_map
    session = ctx.session

    print(f"Loading {len(alive_intervals)} alive intervals...")

    records = []
    for interval in alive_intervals:
        player_db_id = player_id_map.get(interval["player_id"])
        if player_db_id is None:
            raise ValueError(
                f"Missing MatchPlayer row for alive interval in match {match_id}: "
                f"player={interval['player_id']}"
            )

        records.append({
            "match_id": match_id,
            "player_id": player_db_id,
            "start_seconds": interval["start_seconds"],
            "end_seconds": interval["end_seconds"],
        })

    session.execute(insert(AliveInterval), records)

    print(f"✅ Loaded {len(records)} alive intervals")
    return len(records)


def load_weapons(parsed: list[dict], session: Session) -> set[str]:
    """Upsert all weapons from the catalog API into the weapons table.

    On first insert:  all fields written, first_seen_at = last_seen_at = now.
    On conflict:      stats, name, rarity, etc. overwritten; first_seen_at and
                      image_key / small_image_key are deliberately left alone so
                      a re-sync doesn't clear already-mirrored S3 keys or reset
                      the "when did we first see this weapon" timestamp.

    Returns:
        Set of weapon IDs where image_key IS NULL after the upsert — i.e. the
        weapons whose images still need to be downloaded and uploaded to S3.
    """
    if not parsed:
        return set()

    now = datetime.now(timezone.utc)

    # Attach the audit timestamps to every row. first_seen_at is only used on
    # INSERT (it's excluded from the ON CONFLICT SET list below).
    rows = [{**w, "first_seen_at": now, "last_seen_at": now} for w in parsed]

    stmt = pg_insert(Weapon).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Weapon.id],
        set_={
            # Human-readable fields
            "name":           stmt.excluded.name,
            "description":    stmt.excluded.description,
            "weapon_type":    stmt.excluded.weapon_type,
            "rarity":         stmt.excluded.rarity,
            "ammo":           stmt.excluded.ammo,
            "gameplay_tags":  stmt.excluded.gameplay_tags,
            # Keep source URLs up to date so we can detect future changes
            "image_url":       stmt.excluded.image_url,
            "small_image_url": stmt.excluded.small_image_url,
            # Damage stats — overwritten so buffs/nerfs take effect
            "dmg_pb":                stmt.excluded.dmg_pb,
            "firing_rate":           stmt.excluded.firing_rate,
            "clip_size":             stmt.excluded.clip_size,
            "reload_time":           stmt.excluded.reload_time,
            "bullets_per_cartridge": stmt.excluded.bullets_per_cartridge,
            "spread":                stmt.excluded.spread,
            "spread_downsights":     stmt.excluded.spread_downsights,
            "damage_zone_critical":  stmt.excluded.damage_zone_critical,
            # Bookkeeping
            "last_seen_at": stmt.excluded.last_seen_at,
            # NOT updated on conflict:
            #   first_seen_at  — preserves the original discovery date
            #   image_key      — preserves already-mirrored S3 keys
            #   small_image_key
        },
    )
    session.execute(stmt)
    session.flush()

    # After the upsert, find which weapons still need their images mirrored.
    # Any weapon with an image_url but no image_key hasn't been mirrored yet —
    # either it's brand-new, or a previous mirror attempt failed.
    needs_images: set[str] = set(
        session.scalars(
            select(Weapon.id)
            .where(Weapon.image_url.isnot(None))
            .where(Weapon.image_key.is_(None))
        )
    )

    print(
        f"✅ Upserted {len(parsed)} weapons "
        f"({len(needs_images)} need image mirroring)"
    )
    return needs_images


def update_weapon_image_keys(
    weapon_id: str,
    image_key: str | None,
    small_image_key: str | None,
    session: Session,
) -> None:
    """Write S3 keys back to the weapon row after successful image mirroring."""
    session.execute(
        update(Weapon)
        .where(Weapon.id == weapon_id)
        .values(image_key=image_key, small_image_key=small_image_key)
    )


def upsert_map(
    build_major: int,
    build_minor: int,
    mode_id: str,
    image_key: str | None,
    definition_key: str | None,
    session: Session,
) -> None:
    """Upsert a map row after a successful S3 mirror.

    image_key and definition_key are always refreshed so a re-run
    that corrects a failed or partial mirror takes effect immediately.
    synced_at is set to now on every upsert.
    """
    now = datetime.now(timezone.utc)
    stmt = pg_insert(Map).values(
        build_major=build_major,
        build_minor=build_minor,
        mode_id=mode_id,
        image_key=image_key,
        definition_key=definition_key,
        synced_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Map.build_major, Map.build_minor, Map.mode_id],
        set_={
            "image_key":      stmt.excluded.image_key,
            "definition_key": stmt.excluded.definition_key,
            "synced_at":      stmt.excluded.synced_at,
        },
    )
    session.execute(stmt)


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
