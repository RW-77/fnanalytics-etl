"""Safe-zone (storm phase) lookup helpers.

Match events carry a raw timestamp; downstream we want the storm phase that was
active at that moment. These two helpers build a stable chronological ordering
of the zone-update events and resolve a timestamp onto its phase. Shared by the
combat parsers and the engagement clustering.
"""


def sorted_zone_events(zone_events: list[dict]) -> list[dict]:
    """
    Return safe zone updates ordered by the time each phase ends.

    Some payloads include a replay-event `timestamp`, while older cached samples
    only expose shrink timings. We only need stable chronological ordering to
    map a gameplay event onto the phase that was active at that time.
    """
    if not zone_events:
        raise ValueError("Match did not include any safe zone update events.")

    return sorted(
        zone_events,
        key=lambda event: (
            event["shrinkEndTime"],
            event.get("timestamp", event.get("shrinkStartTime", 0)),
            event["currentPhase"],
        ),
    )


def zone_for_timestamp(ordered_zone_events: list[dict], timestamp: int) -> int:
    """
    Find the current phase for an event timestamp with a simple linear scan.

    We stop at the first zone whose `shrinkEndTime` still contains the event.
    If the timestamp lands after every known shrink, we keep the last phase
    instead of assuming a missing phase should crash parsing.
    """
    for zone_event in ordered_zone_events:
        if timestamp <= zone_event["shrinkEndTime"]:
            return zone_event["currentPhase"]

    return ordered_zone_events[-1]["currentPhase"]
