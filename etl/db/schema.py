from etl.db.models import Base
from etl.db.session import get_engine


def init_db() -> None:
    """Create all tables."""
    Base.metadata.create_all(get_engine())
    print("✅ Database tables created")


def reinit_db() -> None:
    """Drop and recreate all tables (WARNING: deletes all data)."""
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("✅ Database reinitialized")
