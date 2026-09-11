import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


load_dotenv()

database_url = os.getenv("DATABASE_URL")
if database_url is None:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# pool_pre_ping: test each pooled connection with a lightweight liveness
# check before handing it out, transparently reconnecting if the server
# (or pgbouncer/SSL layer) dropped it while idle — e.g. during the minutes
# spent parsing a match timeline before the next transaction opens.
# pool_recycle: proactively discard connections older than this many
# seconds so we don't lean on pre_ping for connections we know are stale.
engine = create_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300")),
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Session:
    return SessionLocal()


def get_engine():
    return engine
