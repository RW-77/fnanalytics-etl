from dataclasses import asdict, dataclass
import re

from etl.types import RawEventWindowData


REGION_CODES = (
    "ASIA",
    "BR",
    "EU",
    "ME",
    "NAC",
    "NAE",
    "NAW",
    "OCE",
)

REGION_ALTERNATION = "|".join(sorted(REGION_CODES, key=len, reverse=True))

SEASON_PATTERN = re.compile(r"^(?P<season_code>S\d+)(?:_|$)", re.IGNORECASE)
TRAILING_REGION_PATTERN = re.compile(
    rf"_(?P<region_code>{REGION_ALTERNATION})$",
    re.IGNORECASE,
)
DAY_PATTERN = re.compile(r"(?:^|_)Day(?P<day_index>\d+)(?:_|$)", re.IGNORECASE)
LEGACY_DAY_PATTERN = re.compile(r"GrandFinalDay(?P<day_index>\d+)(?:_|$)", re.IGNORECASE)


# One-off events whose IDs don't follow the season-prefixed naming
# convention. ``season_code`` cannot be inferred from the ID alone for
# these — set it manually downstream if needed.
GLOBAL_CHAMPIONSHIP_CODENAMES = (
    "Dinosauron",   # 2025 Global Championship
    "BambiRaptor",  # 2024 Global Championship
)

GLOBAL_CHAMPIONSHIP_ALTERNATION = "|".join(GLOBAL_CHAMPIONSHIP_CODENAMES)


@dataclass(frozen=True)
class TournamentClassification:
    event_window_id: str
    tournament_id: str
    season_code: str | None
    region_code: str | None
    day_index: int | None
    classification_rule: str


@dataclass(frozen=True)
class _TournamentRule:
    name: str
    pattern: re.Pattern[str]


SUPPORTED_TOURNAMENT_RULES = (
    _TournamentRule(
        name="fncs_legacy_grand_finals",
        pattern=re.compile(
            rf"^(?P<tournament_id>(?P<season_code>S\d+)_FNCS_Major\d+_GrandFinal)"
            rf"Day(?P<day_index>\d+)_(?P<region_code>{REGION_ALTERNATION})$",
            re.IGNORECASE,
        ),
    ),
    _TournamentRule(
        name="fncs_major_finals",
        pattern=re.compile(
            rf"^(?P<tournament_id>(?P<season_code>S\d+)_FNCSMajor\d+_Final)"
            rf"_Day(?P<day_index>\d+)_(?P<region_code>{REGION_ALTERNATION})$",
            re.IGNORECASE,
        ),
    ),
    _TournamentRule(
        name="fncs_divisional_cup_finals",
        pattern=re.compile(
            rf"^(?P<tournament_id>(?P<season_code>S\d+)_FNCSDivisionalCup_"
            rf"Division\d+_Week\d+Final)_(?P<region_code>{REGION_ALTERNATION})$",
            re.IGNORECASE,
        ),
    ),
    _TournamentRule(
        name="performance_evaluation_finals",
        pattern=re.compile(
            rf"^(?P<tournament_id>(?P<season_code>S\d+)_PerformanceEvaluation_"
            rf"Event\d+Round2)_(?P<region_code>{REGION_ALTERNATION})$",
            re.IGNORECASE,
        ),
    ),
    _TournamentRule(
        name="global_championship",
        pattern=re.compile(
            rf"^(?P<tournament_id>{GLOBAL_CHAMPIONSHIP_ALTERNATION})"
            rf"_Day(?P<day_index>\d+)$",
            re.IGNORECASE,
        ),
    ),
)


def get_region_code(event_window_id: str) -> str | None:
    match = TRAILING_REGION_PATTERN.search(event_window_id)
    if match is None:
        return None
    return match.group("region_code").upper()


def get_season_code(event_window_id: str) -> str | None:
    match = SEASON_PATTERN.search(event_window_id)
    if match is None:
        return None
    return match.group("season_code").upper()


def get_day_index(event_window_id: str) -> int | None:
    day_match = DAY_PATTERN.search(event_window_id)
    if day_match is not None:
        return int(day_match.group("day_index"))

    legacy_day_match = LEGACY_DAY_PATTERN.search(event_window_id)
    if legacy_day_match is not None:
        return int(legacy_day_match.group("day_index"))

    return None


def classify_event_window_id(event_window_id: str) -> TournamentClassification | None:
    for rule in SUPPORTED_TOURNAMENT_RULES:
        match = rule.pattern.fullmatch(event_window_id)
        if match is None:
            continue

        groups = match.groupdict()
        season_code = groups.get("season_code")
        region_code = groups.get("region_code")
        day_index = groups.get("day_index")

        return TournamentClassification(
            event_window_id=event_window_id,
            tournament_id=match.group("tournament_id"),
            season_code=season_code.upper() if season_code else None,
            region_code=region_code.upper() if region_code else None,
            day_index=int(day_index) if day_index is not None else None,
            classification_rule=rule.name,
        )

    return None


def parse_event_window_attributes(
    raw: RawEventWindowData,
) -> dict[str, str | int | None]:
    classification = classify_event_window_id(raw.event_window_id)
    if classification is None:
        return {}

    return asdict(classification)
