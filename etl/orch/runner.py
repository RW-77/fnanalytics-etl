from collections.abc import Collection

from sqlalchemy.orm import Session

from etl.types import RawMatchData
from etl.db.context import LoadContext
from etl.db.models import Match
from etl.db.loader import load_match_metadata, load_match_players
from etl.parsing.basic import parse_match_metadata, parse_match_players
from etl.orch.registry import STATS
from etl.parsing.match_parsing import parse_match_timeline
from etl.storage.loader import load_match_timeline


def process_match_relational(
    raw: RawMatchData,
    event_window_id: str,
    session: Session,
    stats: Collection[str] | None = None,
) -> Match:
    """Registry-driven relational load for a single match.

    Runs the two prerequisites — match metadata (upserts the Match row) and
    players (builds the epic_id -> MatchPlayer.id map) — then loads the
    selected stats from :data:`etl.orch.registry.STATS`. ``stats=None`` runs
    every registered stat; pass a subset (e.g. ``["shots"]``) to (re)load
    only those, leaving the other stats' rows untouched.

    The caller owns the transaction, so this composes inside the reconciler's
    ``session.begin()`` block.
    """
    match_metadata = parse_match_metadata(raw)

    raw_event_window_id = match_metadata["event_window_id"]
    if raw_event_window_id != event_window_id:
        raise ValueError(
            f"Match {raw.match_id} belongs to event window {raw_event_window_id}, "
            f"but was queued under {event_window_id}."
        )

    if stats is not None:
        unknown = set(stats) - set(STATS)
        if unknown:
            raise ValueError(
                f"Unknown relational stats {sorted(unknown)}; "
                f"known: {sorted(STATS)}"
            )

    match = load_match_metadata(match_metadata, session)

    ctx = LoadContext(
        session=session,
        match_id=raw.match_id,
        event_window_id=event_window_id,
        player_id_map=load_match_players(parse_match_players(raw), raw.match_id, session),
    )

    for name in (STATS if stats is None else stats):
        stat = STATS[name]
        stat.load(stat.parse(raw), ctx)

    return match


def process_match_timeline(raw: RawMatchData, event_window_id: str) -> None:
    """Materialize the timeline asset: parse frames + zones and upload the
    chunks/metadata to S3.

    Runs with NO database transaction open — a 100+ MiB upload can take minutes
    and would otherwise trip Postgres's idle-in-transaction timeout. Idempotent:
    the uploader clears this match's stale chunks before writing.
    """
    load_match_timeline(parse_match_timeline(raw), event_window_id)
