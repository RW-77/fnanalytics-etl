"""
This module holds functions which call the Osirion client to fetch different types
of event and match data.
"""

import math
import os

import etl.api.osirion_client as osr
import etl.api.fnapi_osirion_client as fnapi
from etl.types import RawMatchData, RawEventWindowData, RawLeaderboardData
from etl.storage.s3_client import S3TournamentLogStore


LOGS_BUCKET = os.getenv("TOURNAMENT_LOGS_BUCKET", "fortnite-tournament-logs")


EVENT_TYPES = {
    # map one-to-one to Osirion endpoints
    "info": osr.fetch_match_info,
    "players": osr.fetch_match_players,
    "weapons": osr.fetch_match_weapons,
    "movement_events": osr.fetch_match_movement_events,
    "shot_events": osr.fetch_match_shot_events,
}

# Endpoints that require an explicit relative time window; their fetchers are
# called with `start_time`/`end_time` derived from the match length. The shots
# endpoint returns HTTP 400 ("StartTime and EndTime are required") otherwise.
TIME_WINDOWED_EVENT_TYPES = {"shot_events"}

GENERAL_EVENT_TYPES = [
    # map to the osirion endpoint GetMatchEvents
    "safeZoneUpdateEvents",
    "reviveEvents",
    "rebootEvents",
    "knockedDownEvents",
    "eliminationEvents",
    "playerInventoryUpdateEvents",
    "healthUpdateEvents",
    "shieldUpdateEvents",
    "fireWeaponEvents",
    "teams",
    "knockedHealthUpdateEvents",
    "aircraftUpdateEvents",
    "supplyDropEvents",
    "llamaUpdateEvents",
    "buildEvents",
    "buildEditEvents",
    "buildDestroyEvents",
    "gameplayCueDamageHitEvents",
    "insideStormUpdateEvents",
]

RAW_MATCH_DATA_FIELDS = {
    # external to internal naming
    "info": "info",
    "players": "players",
    "weapons": "weapons",
    "movement_events": "movement_events",
    "shot_events": "shot_events",
    "safeZoneUpdateEvents": "zone_update_events",
    "reviveEvents": "revive_events",
    "rebootEvents": "reboot_events",
    "knockedDownEvents": "knocked_events",
    "eliminationEvents": "elimination_events",
    "playerInventoryUpdateEvents": "inventory_update_events",
    "healthUpdateEvents": "health_update_events",
    "shieldUpdateEvents": "shield_update_events",
    "fireWeaponEvents": "fire_weapon_events",
    "teams": "teams",
    "knockedHealthUpdateEvents": "knocked_health_update_events",
    "aircraftUpdateEvents": "aircraft_update_events",
    "supplyDropEvents": "supply_drop_events",
    "llamaUpdateEvents": "llama_update_events",
    "buildEvents": "build_events",
    "buildEditEvents": "build_edit_events",
    "buildDestroyEvents": "build_destroy_events",
    "gameplayCueDamageHitEvents": "cue_damage_hit_events",
    "insideStormUpdateEvents": "inside_storm_update_events",
}


def _normalize_event_window_matches(value, *, event_window_id: str) -> list[dict]:
    """
    Backward-compatible normalization for cached event-window match payloads.

    New fetches store a bare list, but older synced caches may still contain the
    full API envelope: {"matches": [...]}.
    """
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        matches = value.get("matches")
        if matches is None:
            return []
        if isinstance(matches, list):
            return matches
        raise ValueError(
            f"Unexpected `matches` payload for event window {event_window_id}: "
            f"`matches` was {type(matches).__name__}, expected list."
        )

    raise ValueError(
        f"Unexpected event-window matches payload for {event_window_id}: "
        f"got {type(value).__name__}, expected list or object."
    )


def _get_missing_log_types(match_id: str) -> list[str]:
    """
    Checks for existence of all raw match logs in S3 and returns the log keys
    that are not yet present.
    """
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)
    return [
        log_type
        for log_type in RAW_MATCH_DATA_FIELDS
        if not bucket.contains_match_log(match_id, log_type)
    ]


def _get_match_length_seconds(match_id: str, bucket: S3TournamentLogStore) -> int:
    """
    Returns the match length in whole seconds, derived from the match info's
    `lengthMs`. Reads cached info from S3 when present, otherwise fetches it
    from the API and caches it (the same `info` log the fetch loop would store).
    """
    if bucket.contains_match_log(match_id, "info"):
        info = bucket.get_match_log(match_id, "info")
    else:
        info = osr.fetch_match_info(match_id)
        bucket.put_match_log(match_id, "info", info)

    length_ms = info.get("lengthMs") if isinstance(info, dict) else None
    if not isinstance(length_ms, (int, float)) or length_ms <= 0:
        raise ValueError(
            f"Match {match_id} info is missing a valid `lengthMs` "
            f"(got {length_ms!r}); cannot bound time-windowed event fetch."
        )

    return math.ceil(length_ms / 1000)


def _fetch_logs(match_id: str, log_types: list[str]) -> None:
    """
    Fetches all raw match logs in log_types and stores them in S3.
    """
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    general_types_set = set(GENERAL_EVENT_TYPES)
    general_types = []
    match_length_s: int | None = None
    for log_type in log_types:
        if log_type in general_types_set:
            general_types.append(log_type)
            continue

        fn = EVENT_TYPES[log_type]
        if log_type in TIME_WINDOWED_EVENT_TYPES:
            if match_length_s is None:
                match_length_s = _get_match_length_seconds(match_id, bucket)
            data = fn(match_id, start_time=0, end_time=match_length_s)
        else:
            data = fn(match_id)
        bucket.put_match_log(match_id, log_type, data)

    if general_types:
        general_logs = osr.fetch_match_events(match_id, include=general_types)
        for log_type, data in general_logs.items():
            bucket.put_match_log(match_id, log_type, data)


def get_raw_match_data(match_id: str) -> RawMatchData:
    """
    Gets all raw logs for a given match from S3 and returns a RawMatchData
    object.
    """
    missing = _get_missing_log_types(match_id)
    if missing:
        raise ValueError(
            f"Missing raw logs for match {match_id}: {', '.join(missing)}"
        )

    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)
    raw_match_data = {
        field_name: bucket.get_match_log(match_id, log_type)
        for log_type, field_name in RAW_MATCH_DATA_FIELDS.items()
    }

    return RawMatchData(match_id=match_id, **raw_match_data)
    

def ensure_match_raw(match_id: str) -> RawMatchData:
    """
    Returns all match logs for a given match_id by getting them from S3.
    For any missing match logs, fetches them from the Osirion API and stores
    them in S3 first.
    """

    # check existence of match logs for each log type
    missing = _get_missing_log_types(match_id)

    # if it doesn't exist, fetch into S3
    if missing:
        _fetch_logs(match_id, missing)

    # assemble RawMatchData object and return it
    return get_raw_match_data(match_id)


def _load_or_fetch_window_log(
    bucket: S3TournamentLogStore,
    event_window_id: str,
    log_type: str,
    fetch,
    *,
    refresh: bool,
):
    """
    Reads one event-window log from S3, or fetches it from Osirion and writes
    it through to S3.

    `refresh=True` bypasses the cache read and overwrites whatever is stored.
    """
    if not refresh and bucket.contains_event_window_log(event_window_id, log_type):
        return bucket.get_event_window_log(event_window_id, log_type)

    data = fetch(event_window_id)
    bucket.put_event_window_log(event_window_id, log_type, data)
    return data


def ensure_event_window_raw(
    event_window_id: str,
    *,
    refresh: bool = False,
) -> RawEventWindowData:
    """
    Returns event window data (metadata + match list) from S3, fetching each
    log from Osirion only if it is not already cached.

    Unlike per-match logs, a window's ``matches`` log is not immutable: while an
    event is still live the match list keeps growing, so a cached copy taken
    mid-event is frozen at first-fetch time and the pipeline will believe there
    is nothing left to process. Pass ``refresh=True`` to re-pull both logs from
    the API and overwrite the cached copies — the flag exists because only the
    caller knows whether a window is live. Once upcoming tournament schedules
    can be known in advance, this can become automatic.
    """

    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    info = _load_or_fetch_window_log(
        bucket,
        event_window_id,
        "info",
        osr.fetch_event_window_data,
        refresh=refresh,
    )
    matches = _load_or_fetch_window_log(
        bucket,
        event_window_id,
        "matches",
        osr.fetch_event_window_matches,
        refresh=refresh,
    )

    return RawEventWindowData(
        event_window_id=event_window_id,
        info=info,
        matches=_normalize_event_window_matches(
            matches, event_window_id=event_window_id
        ),
    )


def ensure_event_window_leaderboard_raw(
    event_window_id: str,
    region: str | None = None,
    *,
    refresh: bool = False,
) -> RawLeaderboardData:
    """
    Returns a window's leaderboard entries and scoring config from S3, fetching
    the two fnapi endpoints independently and saving whatever each returns.

    The two logs come from different endpoints with different retention, so they
    are cached separately:

    * ``leaderboard.json`` (``/tournaments/leaderboard``) — the standings. This
      endpoint is NOT rolling, so it is fetched and saved whenever available,
      even for aged-out events. It is addressed by ``(leaderboardEventId,
      leaderboardEventWindowId)``: the window id is the latter, and the former is
      the event's ``eventId``, which the (also non-rolling) osirion info log
      carries for old windows too — so the leaderboard never depends on the
      rolling ``/tournaments`` listing.
    * ``scoring.json`` (``/tournaments``, ~3-month rolling) — the window's main
      ``scoreLocation`` (its ``scoringRules``). A cached (possibly hand-authored)
      copy is always preferred; otherwise it is sliced from ``/tournaments`` when
      the window is still listed, else left unsaved. ``region`` narrows that
      fetch (``None`` covers region-less global events).

    ``refresh=True`` re-pulls only the (mutable) leaderboard; scoring rules are
    static, so delete the cached ``scoring.json`` to force its re-fetch. When no
    scoring rules are available, ``scoring_rules`` is ``None`` (not an error): the
    entries are still returned so player flags can be ingested, and authoring a
    ``scoring.json`` (just ``scoringRules``) later backfills team standings.
    """
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    # Scoring config (rolling /tournaments). Prefer a cached (possibly
    # hand-authored) copy; else slice it from the listing when still present.
    # May stay None for an aged-out event — that does not block the leaderboard.
    if bucket.contains_event_window_log(event_window_id, "scoring"):
        score_location = bucket.get_event_window_log(event_window_id, "scoring")
    else:
        score_location = None
        for tournament in fnapi.fetch_tournaments(region=region, include_historic=True):
            for event_window in tournament["eventWindows"]:
                if event_window["eventWindowId"] != event_window_id:
                    continue
                for location in event_window["scoreLocations"]:
                    if location.get("isMain"):
                        score_location = location
        if score_location is not None:
            bucket.put_event_window_log(event_window_id, "scoring", score_location)

    # Leaderboard entries — saved independently of scoring. Use the ids from
    # scoring when present, else fall back to the eventId in the (non-rolling)
    # info log, so aged-out events still get their standings cached.
    if not refresh and bucket.contains_event_window_log(event_window_id, "leaderboard"):
        entries = bucket.get_event_window_log(event_window_id, "leaderboard")
    else:
        if score_location is not None and score_location.get("leaderboardEventId"):
            leaderboard_event_id = score_location["leaderboardEventId"]
            leaderboard_event_window_id = score_location.get(
                "leaderboardEventWindowId", event_window_id
            )
        else:
            info = _load_or_fetch_window_log(
                bucket, event_window_id, "info", osr.fetch_event_window_data, refresh=False
            )
            leaderboard_event_id = next(
                (t["eventId"] for t in info.get("tournaments", [])
                 if t.get("eventWindowId") == event_window_id),
                None,
            )
            if leaderboard_event_id is None:
                raise LookupError(
                    f"Could not determine the eventId for {event_window_id} from "
                    f"its info log; cannot fetch its leaderboard."
                )
            leaderboard_event_window_id = event_window_id

        entries = fnapi.fetch_tournament_leaderboard(
            leaderboard_event_id, leaderboard_event_window_id
        )
        bucket.put_event_window_log(event_window_id, "leaderboard", entries)

    # Scoring rules may be unavailable (aged out of fnapi's ~3-month listing, no
    # scoring.json in S3). That only blocks team *standings* — the entries, and
    # thus player flags, are still returned. The caller decides what to build.
    scoring_rules = (
        score_location["scoringRules"] if score_location is not None else None
    )
    # Custom extension to the cached scoring.json (never returned by fnapi);
    # present only for the handful of events with an EWC-style match-point rule.
    match_point_rule = (
        score_location.get("matchPointRule") if score_location is not None else None
    )

    return RawLeaderboardData(
        event_window_id=event_window_id,
        scoring_rules=scoring_rules,
        entries=entries,
        match_point_rule=match_point_rule,
    )


if __name__ == "__main__":
    match_id = "832ceecc424df110d58e3e96d3dff834"
    data = ensure_match_raw(match_id)
    print(data)
