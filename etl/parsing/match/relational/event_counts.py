from etl.types import RawMatchData


# The raw event logs a match's event count covers, by RawMatchData field.
# ``shot_events`` is left out: it carries the same shots as
# ``fire_weapon_events`` (from a second, truncation-prone endpoint), so counting
# both would count every shot twice. The non-event logs (info, players, teams,
# weapons, cosmetics) describe the match rather than things that happened in it.
COUNTED_LOGS: tuple[str, ...] = (
    "movement_events",
    "inventory_update_events",
    "fire_weapon_events",
    "build_events",
    "build_edit_events",
    "build_destroy_events",
    "health_update_events",
    "shield_update_events",
    "knocked_health_update_events",
    "inside_storm_update_events",
    "cue_damage_hit_events",
    "elimination_events",
    "knocked_events",
    "revive_events",
    "reboot_events",
    "zone_update_events",
    "aircraft_update_events",
    "supply_drop_events",
    "llama_update_events",
)


def parse_event_counts(raw: RawMatchData) -> list[dict]:
    """One row per counted raw event log: how many events it holds.

    Feeds the homepage's all-time event total. Counts are kept per log so the
    website can choose which logs to sum (say, leaving out health and shield
    updates) without re-reading every match's raw logs.
    """
    return [
        {"log_type": log_type, "count": len(getattr(raw, log_type))}
        for log_type in COUNTED_LOGS
    ]
