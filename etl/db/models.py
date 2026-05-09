import os

from sqlalchemy import (
    String, 
    Float, 
    DateTime, 
    Boolean, 
    ForeignKey, 
    Index, 
    Integer,
    create_engine, 
)
from sqlalchemy.orm import (
    DeclarativeBase, 
    Mapped, 
    Session, 
    sessionmaker,
    mapped_column, 
    relationship, 
)
from datetime import datetime, timezone
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()


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
    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, nullable=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, nullable=True)
    
    def __repr__(self):
        return f"<Event(event_id={self.event_id})>"


class EventWindow(Base):
    __tablename__ = "event_windows"

    event_window_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    
    # Processing status flags
    processing: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    failed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Timestamps
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_processing_start: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    last_processed: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    last_failed: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)

    start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, nullable=True)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None, nullable=True)

    total_matches: Mapped[int] = mapped_column(Integer, default=0)
    processed_matches: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    matches: Mapped[List["Match"]] = relationship(back_populates="event_window")
    weapons: Mapped[List["Weapon"]] = relationship(back_populates="event_window")

    def __repr__(self):
        return f"<EventWindow(event_window_id={self.event_window_id})>"


class Match(Base):
    __tablename__ = "matches"
    
    match_id: Mapped[str] = mapped_column(String(50), primary_key=True)

    # Foreign keys
    event_window_id: Mapped[str] = mapped_column(String(50), ForeignKey("event_windows.event_window_id"))

    # Processing status
    processing: Mapped[bool] = mapped_column(Boolean, default=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    failed: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Match metadata
    event_id: Mapped[Optional[str]] = mapped_column(String(100), default=None)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    gamemode: Mapped[Optional[str]] = mapped_column(String(100), default=None)
    duration: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None)
    player_count: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    event_window: Mapped["EventWindow"] = relationship(back_populates="matches")
    damage_dealt_events: Mapped[List["DamageDealtEvent"]] = relationship(back_populates="match")
    elim_events: Mapped[List["EliminationEvent"]] = relationship(back_populates="match")
    players: Mapped[List["MatchPlayer"]] = relationship(back_populates="match")

    def __repr__(self):
        return f"<Match(match_id={self.match_id})>"


class MatchPlayer(Base):
    __tablename__ = "match_players"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    epic_id: Mapped[str] = mapped_column(String(100))
    epic_username: Mapped[str] = mapped_column(String(100))
    match_id: Mapped[str] = mapped_column(String(50), ForeignKey("matches.match_id"))

    # Relationships
    match: Mapped["Match"] = relationship(back_populates="players")
    damage_dealt: Mapped[List["DamageDealtEvent"]] = relationship(
        foreign_keys="DamageDealtEvent.actor_id", 
        back_populates="actor"
    )
    damage_taken: Mapped[List["DamageDealtEvent"]] = relationship(
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
    match_id: Mapped[str] = mapped_column(String(50), ForeignKey("matches.match_id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    
    # Foreign keys to players
    actor_id: Mapped[int] = mapped_column(Integer, ForeignKey("match_players.id"), nullable=True)
    recipient_id: Mapped[int] = mapped_column(Integer, ForeignKey("match_players.id"), nullable=True)
    
    # Weapon info
    weapon_id: Mapped[str] = mapped_column(String(100))
    weapon_type: Mapped[Optional[str]] = mapped_column(String(50), default=None)
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
    match_id: Mapped[str] = mapped_column(String(50), ForeignKey("matches.match_id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    game_time_seconds: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    
    # Foreign keys to players
    actor_id: Mapped[int] = mapped_column(Integer, ForeignKey("match_players.id"))
    recipient_id: Mapped[int] = mapped_column(Integer, ForeignKey("match_players.id"))
    
    # Weapon info
    weapon_id: Mapped[str] = mapped_column(String(100))
    weapon_type: Mapped[Optional[str]] = mapped_column(String(50), default=None)
    
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


# Database connection functions
def get_engine():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set in .env")
    return create_engine(database_url, echo=False)


def get_session() -> Session:
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def init_db():
    """Create all tables"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("✅ Database tables created")


def reinit_db():
    """Drop and recreate all tables (WARNING: deletes all data)"""
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("✅ Database reinitialized")


if __name__ == "__main__":
    init_db()
