from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
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


class Weapon(Base):
    __tablename__ = "weapons"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weapon_id: Mapped[str] = mapped_column(String(100))
    weapon_type: Mapped[str] = mapped_column(String(50))
    event_window_id: Mapped[str] = mapped_column(String(100), ForeignKey("event_windows.event_window_id"))
    
    # Relationships
    event_window: Mapped["EventWindow"] = relationship(back_populates="weapons")
    
    __table_args__ = (
        Index('idx_weapon_type', 'weapon_type'),
        Index('idx_weapon_event_window', 'event_window_id'),
        Index('idx_weapon_weapon_id', 'weapon_id'),
        Index('idx_weapon_unique', 'weapon_id', 'event_window_id', unique=True),
    )
    
    def __repr__(self):
        return f"<Weapon(id={self.id}, weapon_id={self.weapon_id}, weapon_type={self.weapon_type}, event_window_id={self.event_window_id})>"


class Event(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, default=None, nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, default=None, nullable=True)

    def __repr__(self):
        return f"<Event(event_id={self.event_id})>"


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


class EventWindow(Base):
    __tablename__ = "event_windows"

    event_window_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(String(100))
    
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
    region_code: Mapped[str | None] = mapped_column(String(4))
    season_code: Mapped[str | None] = mapped_column(String(4))
    day_index: Mapped[int | None] = mapped_column()


    # Relationships
    matches: Mapped[list["Match"]] = relationship(back_populates="event_window")
    weapons: Mapped[list["Weapon"]] = relationship(back_populates="event_window")

    def __repr__(self):
        return f"<EventWindow(event_window_id={self.event_window_id})>"


class Match(Base):
    __tablename__ = "matches"
    
    match_id: Mapped[str] = mapped_column(String(50), primary_key=True)

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
    start_time: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    gamemode: Mapped[str | None] = mapped_column(String(100), default=None)
    duration: Mapped[timedelta | None] = mapped_column(Interval, default=None)
    player_count: Mapped[int | None] = mapped_column(Integer, default=None)
    
    # Relationships
    event_window: Mapped["EventWindow"] = relationship(back_populates="matches")

    def __repr__(self):
        return f"<Match(match_id={self.match_id})>"

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


class DamageDealtEvent(Base):
    __tablename__ = "damage_dealt_events"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("matches.match_id", ondelete="CASCADE")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[int | None] = mapped_column(Integer, default=None)

    # Foreign keys to players
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE"), nullable=True
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE"), nullable=True
    )
    
    # Weapon info
    weapon_id: Mapped[str] = mapped_column(String(100))
    weapon_type: Mapped[str | None] = mapped_column(String(50), default=None)
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
    game_time_seconds: Mapped[int | None] = mapped_column(Integer, default=None)

    # Foreign keys to players
    actor_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE")
    )
    
    # Weapon info
    weapon_id: Mapped[str] = mapped_column(String(100))
    weapon_type: Mapped[str | None] = mapped_column(String(50), default=None)
    
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
