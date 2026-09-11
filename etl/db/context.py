from dataclasses import dataclass, field

from sqlalchemy.orm import Session


@dataclass
class LoadContext:
    """Match-scoped state shared by the relational stat loaders.

    Built once per match by ``process_match_relational`` (the relational half of
    the reconciler) and passed to every ``load_*`` event function. Bundling
    these into one object gives each loader the uniform
    signature ``(rows, ctx)``; a loader that doesn't need player resolution
    simply ignores ``ctx.player_id_map``.

    ``player_id_map`` (epic_id -> MatchPlayer.id) is produced by the players
    prerequisite and consumed by every event loader that resolves an
    actor/recipient epic_id to its MatchPlayer primary key.
    """

    session: Session
    match_id: str
    event_window_id: str
    player_id_map: dict[str, int] = field(default_factory=dict)
