from collections.abc import Callable
from dataclasses import dataclass

from etl.types import RawMatchData, JsonList
from etl.db.context import LoadContext
from etl.db.loader import (
    load_damage_events,
    load_elimination_events,
    load_shot_events,
    load_damage_contribution_events,
    load_assist_events,
    load_shot_attempt_events,
    load_reboot_events,
    load_revive_events,
    load_builds_placed,
    load_alive_intervals,
)
from etl.parsing.match.relational.combat import (
    parse_elims,
    parse_damage_dealt,
    parse_shots,
)
from etl.parsing.match.relational.damage_contribution import (
    parse_damage_contribution_on_elims,
    parse_assists,
)
from etl.parsing.match.relational.shot_attempts import parse_shot_attempts
from etl.parsing.match.relational.support import parse_reboots
from etl.parsing.match.relational.support import parse_revives
from etl.parsing.match.relational.builds import parse_builds_placed
from etl.parsing.match.relational.time_alive import parse_time_alive


@dataclass(frozen=True)
class RelationalStat:
    """One independently (re)loadable relational stat.

    ``parse`` turns a RawMatchData into that stat's rows; ``load`` writes
    exactly those rows for the match — it owns its table (see the per-table
    delete in each ``load_*`` function), so a stat can be reloaded without
    touching the others.

    ``version`` is a hand-maintained integer, bumped when ``parse``'s logic
    changes in a way that should invalidate already-materialized rows. The
    backfill driver (a later step) compares it against the version recorded
    per (match, stat) to find matches that need re-running.
    """

    name: str
    version: int
    parse: Callable[[RawMatchData], JsonList]
    load: Callable[[JsonList, LoadContext], int]


# The loadable relational stats. Prerequisites (match metadata, players) are
# not here — the runner always runs those first to build the LoadContext.
# Note: ``hitscan_elims`` is intentionally absent; it is parsed by the legacy
# monolith but has no table/loader, so it is not a loadable stat.
STATS: dict[str, RelationalStat] = {
    stat.name: stat
    for stat in (
        # v2: damage/contribution/assists/shot_attempts now source the complete
        # fireWeaponEvents log instead of the truncation-prone shot_events log.
        # `damage` additionally sums actualDamage on HIT_PLAYER hits only
        # (excludes knocked/team), matching Osirion's damageToPlayers.
        # v3: fall back to nominal `damage` on older logs that leave
        # `actualDamage` unpopulated (0), so those matches aren't zeroed out.
        RelationalStat("damage", 3, parse_damage_dealt, load_damage_events),
        RelationalStat("elims", 1, parse_elims, load_elimination_events),
        RelationalStat("shots", 1, parse_shots, load_shot_events),
        RelationalStat(
            "damage_contribution",
            2,
            parse_damage_contribution_on_elims,
            load_damage_contribution_events,
        ),
        RelationalStat("assists", 2, parse_assists, load_assist_events),
        RelationalStat("shot_attempts", 2, parse_shot_attempts, load_shot_attempt_events),
        RelationalStat("reboots", 1, parse_reboots, load_reboot_events),
        RelationalStat("revives", 1, parse_revives, load_revive_events),
        RelationalStat("builds_placed", 1, parse_builds_placed, load_builds_placed),
        RelationalStat("time_alive", 1, parse_time_alive, load_alive_intervals),
    )
}


# The timeline S3 asset is not a RelationalStat (it uploads to S3 outside any DB
# transaction), but it is a versioned, reconciled asset — bump TIMELINE_VERSION
# to re-materialize (re-upload) every match's timeline.
TIMELINE_NAME = "timeline"
TIMELINE_VERSION = 1


def desired_versions() -> dict[str, int]:
    """Current version of every materializable asset — the relational stats in
    :data:`STATS` plus the timeline S3 asset. This is the "desired" state the
    reconciler compares against ``match_stat_status``.
    """
    versions = {name: stat.version for name, stat in STATS.items()}
    versions[TIMELINE_NAME] = TIMELINE_VERSION
    return versions
