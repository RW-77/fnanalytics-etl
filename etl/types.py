from typing import Any
from dataclasses import dataclass


JsonDict = dict[str, Any]
JsonList = list[JsonDict]


@dataclass(frozen=True)
class RawEventWindowData:
    event_window_id: str
    info: JsonDict
    matches: JsonList


@dataclass(frozen=True)
class RawLeaderboardData:
    event_window_id: str
    # None when the window's scoring rules aren't available (aged out of fnapi's
    # listing, no scoring.json in S3). The entries are still present, so player
    # flags can be ingested; only team standings need scoring rules.
    scoring_rules: JsonList | None
    entries: JsonList
    # EWC-only "match point" bonus, if the window has one: a hand-authored
    # ``{"threshold", "bonus"}`` extension to the cached scoring.json (not from
    # fnapi). None for the vast majority of windows, which have no such rule.
    match_point_rule: JsonDict | None = None


@dataclass(frozen=True)
class RawMatchData:
    match_id: str
    info: JsonDict
    players: JsonList
    weapons: JsonList
    movement_events: JsonList
    shot_events: JsonList
    zone_update_events: JsonList
    revive_events: JsonList
    reboot_events: JsonList
    knocked_events: JsonList
    elimination_events: JsonList
    inventory_update_events: JsonList
    health_update_events: JsonList
    shield_update_events: JsonList
    fire_weapon_events: JsonList
    teams: JsonList
    knocked_health_update_events: JsonList
    aircraft_update_events: JsonList
    supply_drop_events: JsonList
    llama_update_events: JsonList
    build_events: JsonList
    build_edit_events: JsonList
    build_destroy_events: JsonList
    cue_damage_hit_events: JsonList
    inside_storm_update_events: JsonList


@dataclass(frozen=True)
class ParsedTimelineData:
    match_id: str
    metadata: JsonDict
    frame_chunks: list[Any]
    zone_phases: JsonList
    shots: JsonList
