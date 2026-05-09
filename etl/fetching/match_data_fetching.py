"""
This module holds functions which call the Osirion client to fetch different types
of event and match data.
"""

import os

import etl.api.osirion_client as osr
from etl.types import RawMatchData, RawEventWindowData
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

GENERAL_EVENT_TYPES = [
    # map to the osirion endpoint GetMatchEvents
    "safeZoneUpdateEvents",
    "reviveEvents",
    "rebootEvents",
    "knockedDownEvents",
    "eliminationEvents",
    "playerInventoryUpdateEvents",
    "healthUpdateEvents",
    "shieldUpdateEvents"
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
    "shieldUpdateEvents": "shield_update_events"
}


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


def _fetch_logs(match_id: str, log_types: list[str]) -> None:
    """
    Fetches all raw match logs in log_types and stores them in S3.
    """
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    general_types_set = set(GENERAL_EVENT_TYPES)
    general_types = []
    for log_type in log_types:
        if log_type not in general_types_set:
            fn = EVENT_TYPES[log_type]
            data = fn(match_id)
            bucket.put_match_log(match_id, log_type, data)
        else:
            general_types.append(log_type)
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


def ensure_event_window_raw(event_window_id) -> RawEventWindowData:
    """
    Fetches and returns all event window data, including metadata
    and a list of all matches.
    """
    
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    # check existence of required event window logs for info and matches
    # if it doesn't exist, fetch into S3
    if not bucket.contains_event_window_log(event_window_id, "info"):
        data = osr.fetch_event_window_data(event_window_id)
        bucket.put_event_window_log(event_window_id, "info", data)

    if not bucket.contains_event_window_log(event_window_id, "matches"):
        data = osr.fetch_event_window_matches(event_window_id)
        bucket.put_event_window_log(event_window_id, "matches", data)

    # assemble EventWindowData object and return it
    ret = {
        "info": bucket.get_event_window_log(event_window_id, "info"),
        "matches": bucket.get_event_window_log(event_window_id, "matches"),
    }

    return RawEventWindowData(event_window_id=event_window_id, **ret)


if __name__ == "__main__":
    match_id = "832ceecc424df110d58e3e96d3dff834"
    data = ensure_match_raw(match_id)
    print(data)
