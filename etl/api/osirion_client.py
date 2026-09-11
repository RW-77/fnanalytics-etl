"""
Pure HTTP client for raw data from the Osirion API and OsirionFNAPI
"""

import os
import json
import time
import requests
from typing import Any
from dotenv import load_dotenv

from etl.types import JsonDict, JsonList


load_dotenv()

BASE_URL = "https://api.osirion.gg/fortnite/v1"
API_KEY = os.getenv("API_KEY") 

if not API_KEY:
    raise ValueError("API_KEY not found in environment variables. Please check your .env file.")

HEADERS = {"Authorization": f"Bearer {API_KEY}"}

REQUIRED_MATCH_EVENT_LOGS = [
    "safeZoneUpdateEvents",
    "reviveEvents",
    "rebootEvents",
    "knockedDownEvents",
    "eliminationEvents",
    "playerInventoryUpdateEvents",
    "landingEvents",
    "healthUpdateEvents",
    "shieldUpdateEvents",
    "fireWeaponEvents",
]


def _make_request(
    url: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> JsonDict:
    """
    Make a request to the Osirion API with retry logic for transient errors.
    
    Args:
        url: The API endpoint URL
        params: Optional query parameters
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries (exponential backoff)
    
    Returns:
        The JSON response data
    
    Raises:
        RuntimeError: If the request fails after all retries
        ValueError: If API_KEY is not set
    """
    # Transient error status codes that should be retried
    retryable_status_codes = {502, 503, 504, 429}  # Bad Gateway, Service Unavailable, Gateway Timeout
    
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=HEADERS, params=params or {}, timeout=30)
            
            # Success
            if res.status_code == 200:
                data = res.json()
                if not isinstance(data, dict):
                    raise ValueError(
                        f"Unexpected API response from {url}: expected object, "
                        f"got {type(data).__name__}"
                    )
                return data
            
            # Check if it's a retryable error
            if res.status_code in retryable_status_codes and attempt < max_retries - 1:
                retry_after = res.headers.get("Retry-After")
                wait_time = float(retry_after) if retry_after else retry_delay * (2 ** attempt)  # Exponential backoff
                error_msg = res.text[:200] if res.text else "No error message"
                print(f"⚠️  Transient error {res.status_code} (attempt {attempt + 1}/{max_retries}): {error_msg}")
                print(f"   Retrying in {wait_time:.1f} seconds...")
                time.sleep(wait_time)
                continue
            
            # Non-retryable error or final attempt
            error_msg = res.text[:200] if res.text else "No error message"
            
            # Special handling for 401 errors
            if res.status_code == 401:
                raise RuntimeError(
                    f"Authentication failed (401). "
                    f"This usually means your API key is invalid or expired. "
                    f"Error details: {error_msg}\n"
                    f"Please check your API_KEY in the .env file."
                )
            
            raise RuntimeError(f"Error {res.status_code}: {error_msg}")
            
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                print(f"⚠️  Request timeout (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time:.1f} seconds...")
                time.sleep(wait_time)
                continue
            raise RuntimeError(f"Request timeout after {max_retries} attempts")
        
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                print(f"⚠️  Request exception (attempt {attempt + 1}/{max_retries}): {e}")
                print(f"   Retrying in {wait_time:.1f} seconds...")
                time.sleep(wait_time)
                continue
            raise RuntimeError(f"Request failed after {max_retries} attempts: {e}")
    
    # Should never reach here, but just in case
    raise RuntimeError(f"Request failed after {max_retries} attempts")


def _extract_list(data: JsonDict, key: str) -> JsonList:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(
            f"Unexpected API response: `{key}` was {type(value).__name__}, "
            "expected list."
        )
    return value


def session_to_match_id(session_id: str) -> str | None:
    """
    TODO: need to ask how a session id differs from an event window id
    Returns the match ID for a given session ID.
    """
    url = f"{BASE_URL}/matches/session-id-to-match-id?serverRecordedOnly=true&sessionIds={session_id}"
    params = {}
    data = _make_request(url, params)

    match_ids = data.get("matchIds", {})
    if not isinstance(match_ids, dict):
        raise ValueError("Unexpected API response: `matchIds` was not an object.")

    match_id = match_ids.get(session_id)
    return match_id if isinstance(match_id, str) else None


def fetch_tournaments(
    interval_seconds: int,
    include_progress: bool = True,
    season: int | None = None,
    event_window_id: str | None = None,
    limit: int = 100,
    from_index: int = 0,
) -> list[dict[str, Any]]:
    """
    Fetch tournament/event-window metadata from Osirion with pagination.

    The API returns tournament metadata in pages, so this helper keeps fetching
    until it receives a short page.
    """
    url = f"{BASE_URL}/tournaments"
    tournaments: list[dict[str, Any]] = []
    current_from_index = from_index

    while True:
        params: dict[str, Any] = {
            "intervalS": interval_seconds,
            "includeProgress": include_progress,
            "fromIndex": current_from_index,
            "limit": limit,
        }
        if season is not None:
            params["season"] = season

        if event_window_id is not None:
            params["eventWindowId"] = event_window_id

        data = _make_request(url, params)
        batch = data.get("tournaments", [])

        if not isinstance(batch, list):
            raise ValueError("Unexpected API response: `tournaments` was not a list.")

        tournaments.extend(batch)

        if not batch or len(batch) < limit:
            break

        current_from_index += len(batch)

    return tournaments


def fetch_tournaments_by_event_window_id(
    event_window_id: str,
    include_progress: bool = True,
    season: int | None = None,
    limit: int = 100,
    from_index: int = 0,
) -> list[dict[str, Any]]:
    """
    Fetch tournament/event-window metadata for a specific event window with pagination.

    This mirrors ``fetch_tournaments`` but scopes requests by ``eventWindowId``
    instead of requiring an interval.
    """
    url = f"{BASE_URL}/tournaments"
    tournaments: list[dict[str, Any]] = []
    current_from_index = from_index

    while True:
        params: dict[str, Any] = {
            "eventWindowId": event_window_id,
            "includeProgress": include_progress,
            "fromIndex": current_from_index,
            "limit": limit,
        }
        if season is not None:
            params["season"] = season

        data = _make_request(url, params)
        batch = data.get("tournaments", [])

        if not isinstance(batch, list):
            raise ValueError("Unexpected API response: `tournaments` was not a list.")

        tournaments.extend(batch)

        if not batch or len(batch) < limit:
            break

        current_from_index += len(batch)

    return tournaments


def get_team_players(epic_id: str, match_id: str) -> list[str]:
    """
    TODO: need a cheaper way to do this
    """
    url = f"{BASE_URL}/matches/{match_id}/team?epicId={epic_id}"
    params = {}

    data = _make_request(url, params)

    player_data = _extract_list(data, "players")
    return [
        p["epicId"]
        for p in player_data
        if isinstance(p.get("epicId"), str)
    ]


def fetch_match_players(match_id: str) -> JsonList:
    """
    Fetch all match players, including spectators and bots, for a single match.
    """
    url = f"{BASE_URL}/matches/{match_id}/players"
    data = _make_request(url)
    return _extract_list(data, "players")


def fetch_match_info(match_id: str) -> JsonDict:
    url = f"{BASE_URL}/matches/{match_id}"
    return _make_request(url)


def fetch_match_events(
    match_id: str,
    *,
    include: list[str] | None = None,
) -> dict[str, JsonList]:
    url = f"{BASE_URL}/matches/{match_id}/events"
    logs = include or REQUIRED_MATCH_EVENT_LOGS
    params = { "include": ",".join(logs) }
    data = _make_request(url, params)

    return {
        event_type: _extract_list(data, event_type)
        for event_type in logs
    }


def _time_window_params(start_time: int | None, end_time: int | None) -> dict[str, int]:
    """Build relative time-window params, omitting bounds that are unset.

    The movement endpoint returns the full match when no bounds are given, but
    the shots endpoint requires both bounds and returns HTTP 400
    ("StartTime and EndTime are required") if either is missing. Callers that
    hit the shots endpoint must pass an explicit window (see
    ``etl.fetching.match_data_fetching``).
    """
    params: dict[str, int] = {}
    if start_time is not None:
        params["startTimeRelative"] = start_time
    if end_time is not None:
        params["endTimeRelative"] = end_time
    return params


def fetch_match_movement_events(
    match_id: str,
    *,
    start_time: int | None = None,
    end_time: int | None = None,
) -> JsonList:
    url = f"{BASE_URL}/matches/{match_id}/events/movement"
    data = _make_request(url, _time_window_params(start_time, end_time))
    return _extract_list(data, "events")


# The /events/shots endpoint truncates its response under load: it can drop a
# large, non-deterministic chunk of the match yet still return HTTP 200 with
# valid JSON. A single full-match request is therefore unreliable. We instead
# page over the window in small sub-windows — each response stays well under the
# truncation threshold — then merge. Windows overlap so no event is lost at a
# boundary regardless of the endpoint's inclusive/exclusive semantics;
# de-duplication removes the double-covered events. Observed truncation only
# occurs above ~19k events per response; a 200s window tops out around ~8k.
SHOT_WINDOW_SECONDS = 200
SHOT_WINDOW_STEP_SECONDS = 190  # 10s overlap between consecutive windows


def _fetch_shots_window(
    match_id: str,
    start_time: int | None,
    end_time: int | None,
) -> JsonList:
    url = f"{BASE_URL}/matches/{match_id}/events/shots"
    data = _make_request(url, _time_window_params(start_time, end_time))
    return _extract_list(data, "hitscanEvents")


def fetch_match_shot_events(
    match_id: str,
    *,
    start_time: int | None = None,
    end_time: int | None = None,
) -> JsonList:
    """Fetch all hitscan (shot) events for a match, in timestamp order.

    ``start_time`` / ``end_time`` are relative seconds (``startTimeRelative`` /
    ``endTimeRelative``). When both are given we page over the range in
    overlapping sub-windows and merge, to defeat the endpoint's silent
    truncation of large responses. With no window we fall back to a single
    request (the endpoint requires both bounds, so this only covers unusual
    callers).
    """
    if start_time is None or end_time is None:
        return _fetch_shots_window(match_id, start_time, end_time)

    merged: dict[str, JsonDict] = {}
    window_start = start_time
    while window_start < end_time:
        window_end = min(window_start + SHOT_WINDOW_SECONDS, end_time)
        for event in _fetch_shots_window(match_id, window_start, window_end):
            # Key on full content so genuinely distinct events (e.g. multiple
            # pellets sharing a timestamp) survive while boundary overlaps
            # collapse.
            merged[json.dumps(event, sort_keys=True)] = event
        if window_end >= end_time:
            break
        window_start += SHOT_WINDOW_STEP_SECONDS

    return sorted(merged.values(), key=lambda e: e["timestamp"])


def fetch_match_weapons(match_id: str) -> JsonList:
    url = f"{BASE_URL}/matches/{match_id}/weapons"
    data = _make_request(url)
    return _extract_list(data, "weapons")


def fetch_event_window_data(event_window_id: str) -> JsonDict:
    url = f"{BASE_URL}/tournaments"
    params = { "eventWindowId": event_window_id }
    return _make_request(url, params)


def fetch_event_window_matches(event_window_id: str) -> JsonList:
    url = f"{BASE_URL}/matches"
    params = { "eventWindowId": event_window_id, "ignoreUploads": True }
    data = _make_request(url, params)
    return _extract_list(data, "matches")


def fetch_event_matches(event_id: str) -> JsonList:
    url = f"{BASE_URL}/matches"
    params = { "eventId": event_id, "ignoreUploads": True }
    data = _make_request(url, params)
    return _extract_list(data, "matches")


if __name__ == "__main__":
    # event_window_id = "S36_PerformanceEvaluation_Event6Round2_EU"
    # print(json.dumps(fetch_tournaments_by_event_window_id(event_window_id), indent=2))
    match_id = "2f9cd76dd24473df2f04b09c06db0c76"
    print(json.dumps(session_to_match_id(match_id), indent=2))
