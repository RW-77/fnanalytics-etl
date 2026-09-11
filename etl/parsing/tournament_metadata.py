"""Tournament-level metadata: human-readable titles and manual overrides.

Two sources, merged in :func:`resolve_tournament_metadata`:

* :data:`TITLE_RULES` — regex rules over ``tournament_id`` that derive a
  display title for the recurring tournament types we know about
  (FNCS Major Finals, legacy FNCS Grand Finals, Performance Evaluation
  Finals, FNCS Divisional Cup finals).

* :data:`TOURNAMENT_REGISTRY` — a hand-maintained dict for one-off events
  (e.g. Global Championships) whose titles or seasons can't be derived
  from the ID alone.

Registry values take precedence over rule-derived values.

This module is read-only configuration — nothing here touches the
database. The :class:`Tournament` row itself is upserted by the loader.
"""
import re
from dataclasses import dataclass
from typing import Callable

from etl.parsing.tournament_classification import TournamentClassification


# ---------------------------------------------------------------------------
# Season → calendar-year mapping
# ---------------------------------------------------------------------------
# Used when rendering year-suffixed titles like "FNCS Major 1 - 2025".
# Extend as new seasons ship. Unknown seasons render with "?" in the title,
# which is intentional: better to display a visibly-broken title than to
# silently invent a year.

# may have to fix later using date of event instead
SEASON_TO_YEAR: dict[str, int] = {
    "S33": 2025,
    "S34": 2025,
    "S35": 2025,
    "S36": 2025,
    "S37": 2026,
    "S38": 2026,
    "S39": 2026,
    "S40": 2026,
    "S41": 2026,
    "S42": 2026,
}


def _year_for_season(season_num: str) -> str:
    return str(SEASON_TO_YEAR.get(f"S{season_num}", "?"))


# ---------------------------------------------------------------------------
# Title rules
# ---------------------------------------------------------------------------

TitleRule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]


def _fncs_major_final_title(m: re.Match[str]) -> str:
    return f"FNCS Major {m['major_num']} - {_year_for_season(m['season_num'])}"


def _fncs_legacy_grand_final_title(m: re.Match[str]) -> str:
    return (
        f"FNCS Major {m['major_num']} Grand Final - "
        f"{_year_for_season(m['season_num'])}"
    )


def _fncs_last_chance_major_final_title(m: re.Match[str]) -> str:
    return f"FNCS Last Chance Major - {_year_for_season(m['season_num'])}"


def _reload_elite_series_final_title(m: re.Match[str]) -> str:
    return f"Reload Elite Series {m['series_num']} - Finals"


def _performance_eval_final_title(m: re.Match[str]) -> str:
    return f"Performance Evaluation Finals {m['event_num']}"


def _divisional_cup_final_title(m: re.Match[str]) -> str:
    return f"Division {m['division_num']} Finals - Week {m['week_num']}"


TITLE_RULES: tuple[TitleRule, ...] = (
    (
        re.compile(
            r"^S(?P<season_num>\d+)_FNCSMajor(?P<major_num>\d+)_Final$",
            re.IGNORECASE,
        ),
        _fncs_major_final_title,
    ),
    (
        re.compile(
            r"^S(?P<season_num>\d+)_FNCSLastChanceMajor_Final$",
            re.IGNORECASE,
        ),
        _fncs_last_chance_major_final_title,
    ),
    (
        re.compile(
            r"^S\d+_ReloadEliteSeries(?P<series_num>\d+)Final$",
            re.IGNORECASE,
        ),
        _reload_elite_series_final_title,
    ),
    (
        re.compile(
            r"^S(?P<season_num>\d+)_FNCS_Major(?P<major_num>\d+)_GrandFinal$",
            re.IGNORECASE,
        ),
        _fncs_legacy_grand_final_title,
    ),
    (
        re.compile(
            r"^S\d+_PerformanceEvaluation_Event(?P<event_num>\d+)Round2$",
            re.IGNORECASE,
        ),
        _performance_eval_final_title,
    ),
    (
        re.compile(
            r"^S\d+_FNCSDivisionalCup_Division(?P<division_num>\d+)"
            r"_Week(?P<week_num>\d+)Final$",
            re.IGNORECASE,
        ),
        _divisional_cup_final_title,
    ),
)


def derive_tournament_title(tournament_id: str) -> str | None:
    """Return a rule-derived display title for *tournament_id*, or ``None``."""
    for pattern, formatter in TITLE_RULES:
        match = pattern.fullmatch(tournament_id)
        if match is not None:
            return formatter(match)
    return None


# ---------------------------------------------------------------------------
# Manual registry — overrides for tournaments not covered by the rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TournamentOverride:
    """Manual metadata for a tournament whose ID alone is insufficient.

    Any field left ``None`` is filled in by the rule-derived value (or by
    the classification's ``season_code`` for that field).
    """
    title: str | None = None
    season_code: str | None = None
    force_day_index_null: bool = False


TOURNAMENT_REGISTRY: dict[str, TournamentOverride] = {
    "Dinosauron": TournamentOverride(
        title="FNCS Global Championship 2025",
        season_code="S35",  # adjust if your real-world mapping differs
    ),
    "BambiRaptor": TournamentOverride(
        title="FNCS Global Championship 2024",
        season_code="S29",
    ),
    "Bratwurst_Finals": TournamentOverride(
        title="FNCS Summit Finals",
        season_code="S40",
        force_day_index_null=True,
    ),
    "Escargo": TournamentOverride(
        title="Esports World Cup Final - 2026",
        season_code="S41",
        force_day_index_null=True,
    ),
}


# ---------------------------------------------------------------------------
# Merge: rules + registry + classification → resolved metadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TournamentMetadata:
    tournament_id: str
    title: str | None
    season_code: str | None
    force_day_index_null: bool = False


@dataclass(frozen=True)
class EventMetadata:
    """Event-table fields that can be derived from an event_window_id.

    Carries everything ``load_event`` needs to write. ``image_key`` is
    derived by convention (``<event_id>.jpg``) so the ETL owns the
    column outright — no manual-edit preservation needed. If the
    convention ever changes (different extension, per-event override,
    etc.), this dataclass and ``parse_event_metadata`` are the single
    point of change.
    """
    event_id: str
    region_code: str | None
    season_code: str | None
    image_key: str


def resolve_tournament_metadata(
    classification: TournamentClassification,
) -> TournamentMetadata:
    """Combine rule-derived title with manual registry overrides.

    ``season_code`` falls back to the classification's value when the
    registry has nothing to say, so FNCS-style tournaments still get a
    season from the parsed ID.
    """
    tournament_id = classification.tournament_id

    title = derive_tournament_title(tournament_id)
    season_code = classification.season_code
    force_day_index_null = False

    override = TOURNAMENT_REGISTRY.get(tournament_id)
    if override is not None:
        if override.title is not None:
            title = override.title
        if override.season_code is not None:
            season_code = override.season_code
        force_day_index_null = override.force_day_index_null

    return TournamentMetadata(
        tournament_id=tournament_id,
        title=title,
        season_code=season_code,
        force_day_index_null=force_day_index_null
    )
