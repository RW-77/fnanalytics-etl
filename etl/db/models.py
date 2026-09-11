from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    JSON,
    String,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from datetime import datetime, timedelta, timezone


class Base(DeclarativeBase):
    pass


class Tournament(Base):
    """A logical grouping of event windows that share a ``tournament_id``.

    Per-tournament metadata that isn't derivable from the event-window ID
    alone (notably the human-readable ``title``) lives here. The loader
    populates rows with derived values on first ingest and never overwrites
    them, so manual edits via psql or a future admin UI are durable.
    """
    __tablename__ = "tournaments"

    tournament_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    season_code: Mapped[str | None] = mapped_column(String(4), default=None)

    def __repr__(self):
        return f"<Tournament(tournament_id={self.tournament_id}, title={self.title!r})>"


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    # Nullable because global championships (e.g. Dinosauron, BambiRaptor)
    # legitimately have no region and no season derivable from the event id.
    region_code: Mapped[str | None] = mapped_column(String(4))
    season_code: Mapped[str | None] = mapped_column(String(4))
    # S3 object key (or just filename), derived as ``<event_id>.jpg`` by
    # ``parse_event_metadata`` and refreshed by ``load_event`` on every
    # ingest. Nullable so legacy rows that pre-date the column survive.
    image_key: Mapped[str | None] = mapped_column()

    def __repr__(self):
        return f"<Event(event_id={self.event_id})>"


class EventWindow(Base):
    __tablename__ = "event_windows"

    event_window_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("events.event_id")
    )

    # Processing status
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_processing_start: Mapped[datetime | None] = mapped_column()
    last_processed: Mapped[datetime | None] = mapped_column()
    last_failed: Mapped[datetime | None] = mapped_column()

    # Match metadata
    start_time: Mapped[datetime | None] = mapped_column()
    end_time: Mapped[datetime | None] = mapped_column()

    total_matches: Mapped[int] = mapped_column(default=0)
    processed_matches: Mapped[int] = mapped_column(default=0)

    # Classification metadata
    tournament_id: Mapped[str | None] = mapped_column()
    day_index: Mapped[int | None] = mapped_column()

    # Relationships
    matches: Mapped[list["Match"]] = relationship(back_populates="event_window")

    def __repr__(self):
        return f"<EventWindow(event_window_id={self.event_window_id})>"


class Match(Base):
    __tablename__ = "matches"

    match_id: Mapped[str] = mapped_column(String(50), primary_key=True)

    # ``serverId`` from the match info log, which equals the tournament
    # leaderboard's ``sessionId`` — the join key to ``event_window_team_matches``.
    # NOT NULL: event windows are fetched with ignoreUploads=true, so every
    # match is server-recorded and has a serverId. Existing rows are filled by
    # jobs/backfill_match_session_id.py before the enforcing migration runs.
    session_id: Mapped[str] = mapped_column(String(50))

    # Foreign keys
    event_window_id: Mapped[str] = mapped_column(String(100), ForeignKey("event_windows.event_window_id"))

    # Processing status
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_processing_start: Mapped[datetime | None] = mapped_column(DateTime, default=None, nullable=True)
    last_processed: Mapped[datetime | None] = mapped_column(DateTime, default=None, nullable=True)
    last_failed: Mapped[datetime | None] = mapped_column(DateTime, default=None, nullable=True)

    # Match metadata
    event_id: Mapped[str | None] = mapped_column(String(100), default=None)
    map_path: Mapped[String | None] = mapped_column(String, default=None)
    # fnapi /v1/maps mode id resolved from map_path (see parsing/map_modes.py);
    # joins to maps(build_major, build_minor, mode_id) for replay assets.
    mode_id: Mapped[str | None] = mapped_column(String(50), default=None)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    gamemode: Mapped[str | None] = mapped_column(String(100), default=None)
    duration: Mapped[timedelta | None] = mapped_column(Interval, default=None)
    player_count: Mapped[int | None] = mapped_column(Integer, default=None)

    # Game build version — used to look up the correct map assets.
    # Parsed from the raw match info at ingest time.
    build_major: Mapped[int | None] = mapped_column(Integer, default=None)
    build_minor: Mapped[int | None] = mapped_column(Integer, default=None)

    # Relationships
    event_window: Mapped["EventWindow"] = relationship(back_populates="matches")
    match_weapons: Mapped[list["MatchWeapon"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    players: Mapped[list["MatchPlayer"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    damage_dealt_events: Mapped[list["DamageDealtEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    elim_events: Mapped[list["EliminationEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    fire_weapon_events: Mapped[list["FireWeaponEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    damage_contribution_events: Mapped[list["DamageContributionEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    assist_events: Mapped[list["AssistEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    shot_attempt_events: Mapped[list["ShotAttemptEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reboot_events: Mapped[list["RebootEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    alive_intervals: Mapped[list["AliveInterval"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    revive_events: Mapped[list["ReviveEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    build_placed_events: Mapped[list["BuildPlacedEvent"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_match_session", "session_id"),
    )

    def __repr__(self):
        return f"<Match(match_id={self.match_id})>"


class MatchPlayer(Base):
    __tablename__ = "match_players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    epic_id: Mapped[str] = mapped_column(String(100))
    epic_username: Mapped[str] = mapped_column(String(100))
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="players")
    damage_dealt: Mapped[list["DamageDealtEvent"]] = relationship(
        foreign_keys="DamageDealtEvent.actor_id",
        back_populates="actor"
    )
    damage_taken: Mapped[list["DamageDealtEvent"]] = relationship(
        foreign_keys="DamageDealtEvent.recipient_id",
        back_populates="recipient"
    )

    __table_args__ = (
        Index('idx_player_epic_match', 'epic_id', 'match_id', unique=True),
        Index('idx_player_match', 'match_id'),
    )

    def __repr__(self):
        return f"<MatchPlayer(epic_id={self.epic_id}, epic_username={self.epic_username}, match_id={self.match_id})>"


class Weapon(Base):
    """Global weapon catalog synced from the fnapi.osirion.gg/v1/weapons endpoint.

    Stats are overwritten on each sync (buffs/nerfs update in place). Snapshots
    of the raw payload are stored in S3 at weapons/snapshots/{timestamp}.json
    for historical accuracy when needed.
    """
    __tablename__ = "weapons"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)  # API weapon id
    name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(1000))
    weapon_type: Mapped[str | None] = mapped_column(String(100))
    rarity: Mapped[str | None] = mapped_column(String(50))
    ammo: Mapped[str | None] = mapped_column(String(100))
    gameplay_tags: Mapped[list | None] = mapped_column(JSON)

    # S3 keys — null until the image has been mirrored
    image_key: Mapped[str | None] = mapped_column(String(300))
    small_image_key: Mapped[str | None] = mapped_column(String(300))
    # Originals retained so URL changes on re-sync trigger a re-mirror
    image_url: Mapped[str | None] = mapped_column(String(500))
    small_image_url: Mapped[str | None] = mapped_column(String(500))

    # Damage stats — all nullable (some weapons have zeroed/missing stats)
    dmg_pb: Mapped[float | None] = mapped_column(Float)
    firing_rate: Mapped[float | None] = mapped_column(Float)
    clip_size: Mapped[int | None] = mapped_column(Integer)
    reload_time: Mapped[float | None] = mapped_column(Float)
    bullets_per_cartridge: Mapped[int | None] = mapped_column(Integer)
    spread: Mapped[float | None] = mapped_column(Float)
    spread_downsights: Mapped[float | None] = mapped_column(Float)
    damage_zone_critical: Mapped[float | None] = mapped_column(Float)

    # Bookkeeping
    first_seen_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index('idx_weapon_type', 'weapon_type'),
        Index('idx_weapon_rarity', 'rarity'),
    )

    def __repr__(self):
        return f"<Weapon(id={self.id!r}, name={self.name!r}, rarity={self.rarity!r})>"


class MatchWeapon(Base):
    """Weapons observed in a specific match, sourced from GetMatchWeapons."""
    __tablename__ = "match_weapons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    weapon_id: Mapped[str] = mapped_column(String(200))
    weapon_type: Mapped[str | None] = mapped_column(String(100))

    # Relationship
    match: Mapped["Match"] = relationship(back_populates="match_weapons")

    __table_args__ = (
        Index('idx_match_weapon_match', 'match_id'),
        Index('idx_match_weapon_weapon', 'weapon_id'),
        Index('idx_match_weapon_unique', 'weapon_id', 'match_id', unique=True),
    )

    def __repr__(self):
        return f"<MatchWeapon(weapon_id={self.weapon_id!r}, match_id={self.match_id!r})>"


class DamageDealtEvent(Base):
    __tablename__ = "damage_dealt_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # Foreign keys to players
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE"), nullable=True
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE"), nullable=True
    )

    # Weapon info
    weapon_id: Mapped[str] = mapped_column(String(200))
    weapon_type: Mapped[str | None] = mapped_column(String(100), default=None)
    damage_amount: Mapped[float] = mapped_column(Float)

    # Positions
    actor_x: Mapped[float] = mapped_column(Float)
    actor_y: Mapped[float] = mapped_column(Float)
    actor_z: Mapped[float] = mapped_column(Float)
    recipient_x: Mapped[float] = mapped_column(Float)
    recipient_y: Mapped[float] = mapped_column(Float)
    recipient_z: Mapped[float] = mapped_column(Float)
    distance: Mapped[float] = mapped_column(Float)

    zone: Mapped[int] = mapped_column(Integer)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="damage_dealt_events")
    actor: Mapped["MatchPlayer"] = relationship(
        foreign_keys=[actor_id],
        back_populates="damage_dealt"
    )
    recipient: Mapped["MatchPlayer"] = relationship(
        foreign_keys=[recipient_id],
        back_populates="damage_taken"
    )

    __table_args__ = (
        Index('idx_damage_match', 'match_id'),
        Index('idx_damage_actor', 'actor_id'),
        Index('idx_damage_recipient', 'recipient_id'),
        Index('idx_damage_weapon', 'weapon_type'),
        Index('idx_damage_zone', 'zone'),
        Index('idx_damage_distance', 'distance'),
        Index('idx_damage_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return f"<DamageDealtEvent(actor_id={self.actor_id}, recipient_id={self.recipient_id}, damage_amount={self.damage_amount}, match_id={self.match_id})>"


class EliminationEvent(Base):
    __tablename__ = "elimination_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # Foreign keys to players
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )

    # Weapon info
    weapon_id: Mapped[str] = mapped_column(String(200))
    weapon_type: Mapped[str | None] = mapped_column(String(100), default=None)

    # Positions
    actor_x: Mapped[float] = mapped_column(Float)
    actor_y: Mapped[float] = mapped_column(Float)
    actor_z: Mapped[float] = mapped_column(Float)
    recipient_x: Mapped[float] = mapped_column(Float)
    recipient_y: Mapped[float] = mapped_column(Float)
    recipient_z: Mapped[float] = mapped_column(Float)
    distance: Mapped[float] = mapped_column(Float)

    zone: Mapped[int] = mapped_column(Integer)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="elim_events")

    __table_args__ = (
        Index('idx_elim_match', 'match_id'),
        Index('idx_elim_actor', 'actor_id'),
        Index('idx_elim_recipient', 'recipient_id'),
        Index('idx_elim_zone', 'zone'),
    )

    def __repr__(self):
        return f"<EliminationEvent(actor_id={self.actor_id}, recipient_id={self.recipient_id}, match_id={self.match_id})>"


class FireWeaponEvent(Base):
    __tablename__ = "fire_weapon_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # Shooter — FK to match_players
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )

    # Weapon
    weapon_id: Mapped[str] = mapped_column(String(200))

    zone: Mapped[int | None] = mapped_column(Integer, default=None)

    # Damage
    damage: Mapped[float] = mapped_column(Float)
    actual_damage: Mapped[float] = mapped_column(Float)

    # Hit flags
    harvest: Mapped[bool] = mapped_column(Boolean)
    hit_player: Mapped[bool] = mapped_column(Boolean)
    hit_critical: Mapped[bool] = mapped_column(Boolean)
    hit_player_build: Mapped[bool] = mapped_column(Boolean)
    hit_fatal: Mapped[bool] = mapped_column(Boolean)
    hit_shield: Mapped[bool] = mapped_column(Boolean)
    hit_ballistic: Mapped[bool] = mapped_column(Boolean)
    destroyed_shield: Mapped[bool] = mapped_column(Boolean)

    # Hit target — nullable FK (None when no player was hit)
    hit_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_players.id", ondelete="SET NULL"), nullable=True, default=None
    )
    hit_result: Mapped[str | None] = mapped_column(String(50), default=None)
    hit_actor_id: Mapped[int | None] = mapped_column(Integer, default=None)

    # Shooter location (from instigatorLocation — not always present)
    actor_x: Mapped[float | None] = mapped_column(Float, default=None)
    actor_y: Mapped[float | None] = mapped_column(Float, default=None)
    actor_z: Mapped[float | None] = mapped_column(Float, default=None)

    # Shot endpoint location (from location — always present)
    end_x: Mapped[float | None] = mapped_column(Float, default=None)
    end_y: Mapped[float | None] = mapped_column(Float, default=None)
    end_z: Mapped[float | None] = mapped_column(Float, default=None)

    # Euclidean distance actor → endpoint; None when actor location is missing
    distance: Mapped[float | None] = mapped_column(Float, default=None)

    # Misc
    item_entry_guid: Mapped[str | None] = mapped_column(String(100), default=None)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="fire_weapon_events")
    actor: Mapped["MatchPlayer"] = relationship(foreign_keys=[actor_id])
    hit_player_rel: Mapped["MatchPlayer | None"] = relationship(foreign_keys=[hit_player_id])

    __table_args__ = (
        Index("idx_fwe_match", "match_id"),
        Index("idx_fwe_actor", "actor_id"),
        Index("idx_fwe_hit_player", "hit_player_id"),
        Index("idx_fwe_weapon", "weapon_id"),
        Index("idx_fwe_time", "game_time_seconds"),
        Index("idx_fwe_zone", "zone"),
    )

    def __repr__(self):
        return f"<FireWeaponEvent(actor_id={self.actor_id}, weapon_id={self.weapon_id!r}, match_id={self.match_id})>"


class DamageContributionEvent(Base):
    """Damage a player dealt to an opponent who was then eliminated by that
    player's team — the un-healed, kill-causal portion of their damage.

    One row per (dealer, victim-death): the dealer's outstanding (un-healed,
    overkill-capped) damage on the victim at the moment the victim died to the
    dealer's team. The headline stat is ``SUM(damage_amount) GROUP BY actor_id``.
    """
    __tablename__ = "damage_contribution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # The dealer credited with the contribution.
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The opponent who was damaged and then eliminated.
    victim_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The teammate (or the dealer) who landed the elimination.
    killer_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )

    damage_amount: Mapped[float] = mapped_column(Float)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="damage_contribution_events")

    __table_args__ = (
        Index('idx_dce_match', 'match_id'),
        Index('idx_dce_actor', 'actor_id'),
        Index('idx_dce_victim', 'victim_id'),
        Index('idx_dce_killer', 'killer_id'),
        Index('idx_dce_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return (
            f"<DamageContributionEvent(actor_id={self.actor_id}, "
            f"victim_id={self.victim_id}, damage_amount={self.damage_amount}, "
            f"match_id={self.match_id})>"
        )


class AssistEvent(Base):
    """An assist: a teammate dealt un-healed damage to an opponent their team
    then eliminated (excluding the finisher).

    One row per (assister, victim-death) — binary, so there is no amount. The
    headline stat is ``COUNT(*) GROUP BY actor_id``.
    """
    __tablename__ = "assist_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # The teammate credited with the assist.
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The opponent who was damaged and then eliminated.
    victim_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The teammate who landed the elimination (never the assister).
    killer_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="assist_events")

    __table_args__ = (
        Index('idx_assist_match', 'match_id'),
        Index('idx_assist_actor', 'actor_id'),
        Index('idx_assist_victim', 'victim_id'),
        Index('idx_assist_killer', 'killer_id'),
        Index('idx_assist_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return (
            f"<AssistEvent(actor_id={self.actor_id}, victim_id={self.victim_id}, "
            f"killer_id={self.killer_id}, match_id={self.match_id})>"
        )


class ShotAttemptEvent(Base):
    """A shot that was an attempt to hit an exposed opponent.

    ``direct_hit`` is True when the bullet connected with the recipient; False
    when it hit a build/terrain while aimed at the recipient (a miss).
    ``passing_distance`` is the perpendicular distance from the recipient's
    hitbox centre to the bullet's path (0 for a direct hit) — a "how centered"
    quality measure. Headline stats: attempts (``COUNT(*) GROUP BY actor_id``)
    and accuracy (``AVG(direct_hit)``).
    """
    __tablename__ = "shot_attempt_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # The shooter.
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The opponent the shot was aimed at (hit, or missed onto a build/terrain).
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )

    weapon_id: Mapped[str | None] = mapped_column(String(200), default=None)
    direct_hit: Mapped[bool] = mapped_column(Boolean)
    passing_distance: Mapped[float] = mapped_column(Float)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="shot_attempt_events")

    __table_args__ = (
        Index('idx_shotatt_match', 'match_id'),
        Index('idx_shotatt_actor', 'actor_id'),
        Index('idx_shotatt_recipient', 'recipient_id'),
        Index('idx_shotatt_weapon', 'weapon_id'),
        Index('idx_shotatt_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return (
            f"<ShotAttemptEvent(actor_id={self.actor_id}, recipient_id={self.recipient_id}, "
            f"direct_hit={self.direct_hit}, match_id={self.match_id})>"
        )


class RebootEvent(Base):
    """A player being rebooted, one row per (rebooted player, rebooter).

    ``rebooted_id`` is the player brought back; ``rebooter_id`` is the teammate
    who rebooted them (nullable — a reboot with no listed rebooter still gets a
    row so the reboot itself is recorded). Multiple rebooters on one reboot yield
    multiple rows sharing a ``timestamp``. Primarily an input to the time-alive
    stat (which pairs reboots with eliminations).
    """
    __tablename__ = "reboot_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # The player who was rebooted.
    rebooted_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The teammate who rebooted them (None when the log lists no rebooter).
    rebooter_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_players.id", ondelete="SET NULL"), nullable=True, default=None
    )

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="reboot_events")

    __table_args__ = (
        Index('idx_reboot_match', 'match_id'),
        Index('idx_reboot_rebooted', 'rebooted_id'),
        Index('idx_reboot_rebooter', 'rebooter_id'),
        Index('idx_reboot_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return (
            f"<RebootEvent(rebooted_id={self.rebooted_id}, "
            f"rebooter_id={self.rebooter_id}, match_id={self.match_id})>"
        )


class ReviveEvent(Base):
    """A player being revived from the knocked (DBNO) state, one row per
    (revived player, reviver).

    ``revived_id`` is the player brought back up; ``reviver_id`` is the teammate
    who revived them (nullable — a player can be revived without a teammate, and
    a revive with no listed reviver still gets a row so the revive itself is
    recorded). Multiple revivers on one revive yield multiple rows sharing a
    ``timestamp``. Distinct from a reboot: a revive is a knockdown pickup, not a
    reboot-van respawn, so it does NOT toggle the alive/dead state.
    """
    __tablename__ = "revive_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # The player who was revived.
    revived_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    # The teammate who revived them (None when the log lists no reviver).
    reviver_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_players.id", ondelete="SET NULL"), nullable=True, default=None
    )

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="revive_events")

    __table_args__ = (
        Index('idx_revive_match', 'match_id'),
        Index('idx_revive_revived', 'revived_id'),
        Index('idx_revive_reviver', 'reviver_id'),
        Index('idx_revive_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return (
            f"<ReviveEvent(revived_id={self.revived_id}, "
            f"reviver_id={self.reviver_id}, match_id={self.match_id})>"
        )


class BuildPlacedEvent(Base):
    """A structure placed by a player, one row per build event.

    ``builder_id`` is the placing player. ``build_type`` is the piece's asset
    name (e.g. ``PBWA_S1_RoofC_C``); ``location_*`` is where it was placed.
    ``build_actor_id`` / ``edited_actor_id`` are the engine actor ids from the
    log (not player ids) — retained so builds can later be correlated with edit
    / destroy events. Headline stat: builds placed = ``COUNT(*) GROUP BY
    builder_id`` (each row is one placement).
    """
    __tablename__ = "build_placed_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # The player who placed the build.
    builder_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )

    build_type: Mapped[str | None] = mapped_column(String(200), default=None)
    location_x: Mapped[float | None] = mapped_column(Float, default=None)
    location_y: Mapped[float | None] = mapped_column(Float, default=None)
    location_z: Mapped[float | None] = mapped_column(Float, default=None)

    # Engine actor ids from the log (not player ids).
    build_actor_id: Mapped[int | None] = mapped_column(Integer, default=None)
    edited_actor_id: Mapped[int | None] = mapped_column(Integer, default=None)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="build_placed_events")

    __table_args__ = (
        Index('idx_buildplaced_match', 'match_id'),
        Index('idx_buildplaced_builder', 'builder_id'),
        Index('idx_buildplaced_type', 'build_type'),
        Index('idx_buildplaced_time', 'game_time_seconds'),
    )

    def __repr__(self):
        return (
            f"<BuildPlacedEvent(builder_id={self.builder_id}, "
            f"build_type={self.build_type!r}, match_id={self.match_id})>"
        )


class AliveInterval(Base):
    """A contiguous span during which a player was alive within a match.

    A player starts alive at match start; an elimination where they are the
    ``targetId`` (including self-eliminations — storm/fall still kills) ends the
    current span, and a reboot starts a new one. Anyone still alive at match end
    gets a final span closed at the match's end time. Eliminations and reboots
    are the only two events that toggle the alive/dead state, so together they
    fully reconstruct the timeline.

    ``start_seconds`` / ``end_seconds`` are game-time seconds since match start.
    Time alive = ``SUM(end_seconds - start_seconds)`` per player; a time-range
    filter clips each span to the window before summing.
    """
    __tablename__ = "alive_intervals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="alive_intervals")

    __table_args__ = (
        Index('idx_alive_match', 'match_id'),
        Index('idx_alive_player', 'player_id'),
        Index('idx_alive_start', 'start_seconds'),
        Index('idx_alive_end', 'end_seconds'),
    )

    def __repr__(self):
        return (
            f"<AliveInterval(player_id={self.player_id}, "
            f"start={self.start_seconds}, end={self.end_seconds}, "
            f"match_id={self.match_id})>"
        )


class MatchStatStatus(Base):
    """Per-(match, stat) materialization record — the "actual" state the
    reconciler compares against each asset's declared version.

    A row means ``stat_name`` was last materialized for ``match_id`` at
    ``parser_version``. The reconciler treats a stat as stale (and reprocesses
    only it) when no row exists, ``parser_version`` is NULL, or it is below the
    registry's current version. ``status`` and ``last_failed`` are observability
    only — staleness is driven by ``parser_version``. The timeline asset
    participates here as ``stat_name = 'timeline'`` alongside the relational
    stats.
    """
    __tablename__ = "match_stat_status"

    match_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("matches.match_id", ondelete="CASCADE"),
        primary_key=True,
    )
    stat_name: Mapped[str] = mapped_column(String(50), primary_key=True)

    # Last SUCCESSFULLY materialized version; NULL until the stat first
    # succeeds. Never advanced on failure, so a failed stat stays stale.
    parser_version: Mapped[int | None] = mapped_column(Integer, default=None)

    status: Mapped[str] = mapped_column(String(32), default="processed")
    last_processed: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_failed: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        Index("idx_mss_stat", "stat_name"),
    )

    def __repr__(self):
        return (
            f"<MatchStatStatus(match_id={self.match_id}, stat_name={self.stat_name!r}, "
            f"parser_version={self.parser_version}, status={self.status!r})>"
        )


class Map(Base):
    """Map catalog — one row per (build_major, build_minor, mode_id) we have
    mirrored to S3.

    Primary purpose is a fast "do we have assets for this match?" lookup so the
    replay viewer can show a meaningful error instead of a broken canvas.

    S3 layout (relative to fortnite-tournament-objects):
        maps/versions/{build_major}.{build_minor:02d}/{mode_id}.webp
        maps/versions/{build_major}.{build_minor:02d}/{mode_id}.json
    Full-snapshot bookkeeping lives at maps/snapshots/{timestamp}.json and is
    NOT tracked here (snapshot records are write-once, append-only).
    """
    __tablename__ = "maps"

    build_major: Mapped[int] = mapped_column(Integer, primary_key=True)
    build_minor: Mapped[int] = mapped_column(Integer, primary_key=True)
    # API mode identifier, e.g. 'br', 'figment', 'reload'
    mode_id: Mapped[str] = mapped_column(String(50), primary_key=True)

    # S3 object keys — null until successfully mirrored
    image_key: Mapped[str | None] = mapped_column(String(300))
    definition_key: Mapped[str | None] = mapped_column(String(300))

    # When this row was last written by sync_maps
    synced_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        Index('idx_map_build', 'build_major', 'build_minor'),
    )

    def __repr__(self):
        return (
            f"<Map(build={self.build_major}.{self.build_minor:02d}, "
            f"mode={self.mode_id!r})>"
        )


class EventWindowTeam(Base):
    """A team's final standing in one event window — a leaderboard entry.

    One row per (event_window, team) from the tournament leaderboard endpoint,
    holding the API's authoritative final numbers. ``team_id`` is the members'
    account ids joined by ':'; ``team_key`` is the same ids sorted — a stable
    identity for the exact set of players, so results can later be grouped
    across tournaments. Per-game detail lives in ``event_window_team_matches``;
    the full cumulative standing (including multi-window tournaments) is a
    downstream aggregation of those rows.
    """
    __tablename__ = "event_window_teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_window_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("event_windows.event_window_id", ondelete="CASCADE")
    )
    team_id: Mapped[str] = mapped_column(String(200))
    team_key: Mapped[str] = mapped_column(String(200))

    final_rank: Mapped[int] = mapped_column(Integer)
    final_points: Mapped[int] = mapped_column(Integer)
    # Packed points+tiebreaker value the API ranks by; ~1e15, so BigInteger.
    final_score: Mapped[int] = mapped_column(BigInteger)
    percentile: Mapped[float] = mapped_column(Float)
    matches: Mapped[int] = mapped_column(Integer)
    wins: Mapped[int] = mapped_column(Integer)
    kills: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_ewt_window", "event_window_id"),
        Index("idx_ewt_team_key", "team_key"),
        Index("idx_ewt_window_team", "event_window_id", "team_id", unique=True),
    )

    def __repr__(self):
        return (
            f"<EventWindowTeam(event_window_id={self.event_window_id!r}, "
            f"team_id={self.team_id!r}, final_rank={self.final_rank})>"
        )


class EventWindowTeamMatch(Base):
    """One team's result in one match of an event window — the per-game grain.

    One row per (event_window, team, session). Carries the raw per-game tracked
    stats and the points computed from the window's scoring rules (see
    :mod:`etl.parsing.scoring`). ``game_number`` is the team's 1-based match index
    within the window, ordered by ``end_time`` — what the leaderboard's match
    buttons scrub through. ``session_id`` is the match id: a soft reference (no
    FK), since leaderboards exist for matches that are never otherwise ingested.
    """
    __tablename__ = "event_window_team_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_window_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("event_windows.event_window_id", ondelete="CASCADE")
    )
    team_id: Mapped[str] = mapped_column(String(200))
    session_id: Mapped[str] = mapped_column(String(50))

    game_number: Mapped[int] = mapped_column(Integer)
    end_time: Mapped[datetime] = mapped_column(DateTime)

    placement: Mapped[int | None] = mapped_column(Integer, default=None)
    team_elims: Mapped[int] = mapped_column(Integer)
    victory_royale: Mapped[bool] = mapped_column(Boolean)
    time_alive: Mapped[int | None] = mapped_column(Integer, default=None)
    placement_tiebreaker: Mapped[int | None] = mapped_column(Integer, default=None)

    placement_points: Mapped[int] = mapped_column(Integer)
    elim_points: Mapped[int] = mapped_column(Integer)
    # EWC-only "match point" clinch bonus; 0 for every game without one. Folded
    # into total_points; kept separate because it is not a per-game *_INDEX rule.
    match_point_bonus: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    total_points: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_ewtm_window", "event_window_id"),
        Index("idx_ewtm_team", "team_id"),
        Index("idx_ewtm_session", "session_id"),
        Index(
            "idx_ewtm_window_team_session",
            "event_window_id", "team_id", "session_id",
            unique=True,
        ),
    )

    def __repr__(self):
        return (
            f"<EventWindowTeamMatch(event_window_id={self.event_window_id!r}, "
            f"team_id={self.team_id!r}, game_number={self.game_number}, "
            f"total_points={self.total_points})>"
        )


class EventWindowPlayer(Base):
    """A player's identity in one event window, from the leaderboard endpoint.

    One row per (event_window, player) from the tournament leaderboard's
    ``players`` array: the display name and chosen country flag as the API
    reported them for that window. ``flag_token`` is the raw
    ``GroupIdentity_GeoIdentity_<key>`` string (or ``None`` when the player has
    no flag set); mapping it to an icon is a presentation concern left to
    consumers. Privacy-hidden members are absent from the API's ``players``
    array, so a team may have fewer rows here than account ids in its ``teamId``.
    """
    __tablename__ = "event_window_players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_window_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("event_windows.event_window_id", ondelete="CASCADE")
    )
    epic_id: Mapped[str] = mapped_column(String(100))
    # Nullable: the leaderboard endpoint reports ``username: null`` for some
    # accounts (privacy-hidden members are dropped from ``players`` entirely,
    # but a listed member can still have no name). Store what the API gave for
    # this window; resolve the name from ``match_players`` (by ``epic_id``) when
    # a display name is needed.
    epic_username: Mapped[str | None] = mapped_column(String(100), default=None)
    flag_token: Mapped[str | None] = mapped_column(String(100), default=None)

    __table_args__ = (
        Index("idx_ewp_window", "event_window_id"),
        Index("idx_ewp_epic", "epic_id"),
        Index("idx_ewp_window_epic", "event_window_id", "epic_id", unique=True),
    )

    def __repr__(self):
        return (
            f"<EventWindowPlayer(event_window_id={self.event_window_id!r}, "
            f"epic_id={self.epic_id!r}, flag_token={self.flag_token!r})>"
        )
