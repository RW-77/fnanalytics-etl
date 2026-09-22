"""Tournament scoring math — one game's stats -> the points it earned.

Uses an event window's ``scoringRules`` (Epic 'fnpubapi' format, found under each
``scoreLocation`` in :func:`etl.api.fnapi_osirion_client.fetch_tournaments`). A
rule is ``{trackedStat, matchRule ("lte"/"gte"), rewardTiers[]}`` and each tier is
``{keyValue, pointsEarned, multiplicative}``.

Tiers are CUMULATIVE: within one rule you earn EVERY tier whose ``matchRule``
the stat satisfies, summed. For placement (``lte``, non-multiplicative) a 1st
place therefore collects all placement tiers — the non-linear step curve; for
eliminations (``gte``, one ``multiplicative`` tier at keyValue 1) you get a flat
``pointsEarned`` per elim. This reproduces the API's cumulative ``pointsEarned``
exactly; the intuitive "best matching tier" reading undercounts ~2x.
"""

from etl.types import JsonDict, JsonList


# trackedStat keys we surface as dedicated point columns. Any other stat a rule
# tracks still counts toward the game total, just not as its own column.
PLACEMENT_STAT = "PLACEMENT_STAT_INDEX"
TEAM_ELIMS_STAT = "TEAM_ELIMS_STAT_INDEX"


def _rule_points(value: float, rule: JsonDict) -> int:
    """Points a single scoring rule awards for ``value`` (cumulative tiers)."""
    match_rule = rule["matchRule"]
    if match_rule not in ("lte", "gte"):
        raise ValueError(f"Unsupported scoring matchRule: {match_rule!r}")

    points = 0.0
    for tier in rule["rewardTiers"]:
        key = tier["keyValue"]
        satisfied = value <= key if match_rule == "lte" else value >= key
        if not satisfied:
            continue
        # multiplicative tiers scale with the stat (e.g. points-per-elim);
        # non-multiplicative tiers are a flat award for clearing the threshold.
        points += tier["pointsEarned"] * value if tier["multiplicative"] else tier["pointsEarned"]
    return int(points)


def game_points_breakdown(tracked_stats: JsonDict, scoring_rules: JsonList) -> dict[str, int]:
    """Points earned in one game, keyed by each rule's ``trackedStat``.

    A rule whose ``trackedStat`` is absent from ``tracked_stats`` contributes
    nothing — skipped rather than defaulted to 0, because a 0 default would make
    an ``lte`` placement rule spuriously satisfy every tier.
    """
    breakdown: dict[str, int] = {}
    for rule in scoring_rules:
        stat = rule["trackedStat"]
        if stat not in tracked_stats:
            continue
        breakdown[stat] = breakdown.get(stat, 0) + _rule_points(tracked_stats[stat], rule)
    return breakdown


def game_points(tracked_stats: JsonDict, scoring_rules: JsonList) -> int:
    """Total points earned in one game (sum across all scoring rules)."""
    return sum(game_points_breakdown(tracked_stats, scoring_rules).values())
