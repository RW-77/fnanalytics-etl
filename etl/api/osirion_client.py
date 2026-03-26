import os
import json
import time
import requests
from pathlib import Path
from typing import Any
from dotenv import load_dotenv


load_dotenv()

BASE_URL = "https://api.osirion.gg/fortnite/v1"
API_KEY = os.getenv("API_KEY") 

if not API_KEY:
    raise ValueError("API_KEY not found in environment variables. Please check your .env file.")

HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def _make_request(url: str, params: dict | None = None, max_retries: int = 3, retry_delay: float = 1.0) -> dict:
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
    retryable_status_codes = {502, 503, 504}  # Bad Gateway, Service Unavailable, Gateway Timeout
    
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=HEADERS, params=params or {}, timeout=30)
            
            # Success
            if res.status_code == 200:
                return res.json()
            
            # Check if it's a retryable error
            if res.status_code in retryable_status_codes and attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
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


def _save_json(data: dict, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Saved to {path}")


def session_to_match_id(session_id: str) -> str | None:
    """
    TODO: need to ask how a session id differs from an event window id
    Returns the match ID for a given session ID.
    """
    url = f"{BASE_URL}/matches/session-id-to-match-id?serverRecordedOnly=true&sessionIds={session_id}"
    params = {}
    data = _make_request(url, params)

    match_id = data.get("matchIds", {}).get(session_id)
    if match_id:
        print(f"Session ID: {session_id}")
        print(f"Match ID: {match_id}")
        return match_id
    else:
        print("No match ID found in response.")
        print(json.dumps(data, indent=2))
        return None


def fetch_tournaments(
    interval_seconds: int,
    include_progress: bool = True,
    season: int | None = None,
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

        data = _make_request(url, params)
        batch = data.get("tournaments", [])

        if not isinstance(batch, list):
            raise ValueError("Unexpected API response: `tournaments` was not a list.")

        tournaments.extend(batch)

        if not batch or len(batch) < limit:
            break

        current_from_index += len(batch)

    return tournaments


def get_team_players(epic_id: str, match_id: str):
    """
    TODO: need a cheaper way to do this
    """
    url = f"{BASE_URL}/matches/{match_id}/team?epicId={epic_id}"
    params = {}

    data = _make_request(url, params)

    player_data: list[dict] = data.get("players", {})
    team_players = [p["epicId"] for p in player_data]
    if team_players:
        print(f"Found {len(team_players)} players")
        return team_players
    else:
        print("Could not find team players.")
        print(json.dumps(data, indent=2))
        return None


def fetch_match_players(match_id: str, out_dir="date/raw") -> str:
    """
    Fetch all match players, including spectators and bots, for a single match.
    """
    url = f"{BASE_URL}/matches/{match_id}/players"
    data = _make_request(url)
    out_path =f"{out_dir}/match_{match_id}/players.json"
    _save_json(data, out_path)
    return out_path


def fetch_match_info(match_id: str, out_dir="data/raw") -> str:
    url = f"{BASE_URL}/matches/{match_id}"
    data = _make_request(url)
    out_path =f"{out_dir}/match_{match_id}/info.json"
    _save_json(data, out_path)
    return out_path


def fetch_match_events(match_id: str, out_dir="data/raw") -> str:
    url = f"{BASE_URL}/matches/{match_id}/events"

    required_logs = [
        "safeZoneUpdateEvents",
        "reviveEvents",
        "rebootEvents", 
        "knockedDownEvents",
        "eliminationEvents",
        "playerInventoryUpdateEvents",
        "landingEvents",
        "healthUpdateEvents",
        "shieldUpdateEvents"
    ]

    params = { "include": ",".join(required_logs) }
    data = _make_request(url, params)
    saved_paths = {}

    match_dir = f"{out_dir}/match_{match_id}"
    for event_type in required_logs:
        if event_type in data and data[event_type]:
            out_path = f"{match_dir}/{event_type}.json"
            _save_json(data[event_type], out_path)
            saved_paths[event_type] = out_path
        else:
            print(f"Warning: no data for {event_type} found in general events")

    return out_path


def fetch_match_movement_events(
    match_id: str, 
    out_dir="data/raw", 
    start_time=0,
    end_time=1650
) -> str:
    url = f"{BASE_URL}/matches/{match_id}/events/movement"
    params = { "startTimeRelative": {start_time}, "endTimeRelative": {end_time} }
    data = _make_request(url, params)["events"]
    out_path = f"{out_dir}/match_{match_id}/movement_events.json"
    _save_json(data, out_path)
    return out_path


def fetch_match_shot_events(
    match_id: str,
    out_dir="data/raw",
    start_time=0,
    end_time=1650
) -> str:

    url = f"{BASE_URL}/matches/{match_id}/events/shots"
    params = {"startTimeRelative": start_time, "endTimeRelative": end_time}
    data = _make_request(url, params)["hitscanEvents"]
    out_path = f"{out_dir}/match_{match_id}/shot_events.json"
    _save_json(data, out_path)
    return out_path


def fetch_match_weapons(match_id: str, out_dir = "data/raw"):
    url = f"{BASE_URL}/matches/{match_id}/weapons"
    params = {}
    data = _make_request(url, params)
    out_path = f"{out_dir}/match_{match_id}/weapons.json"
    _save_json(data, out_path)
    return out_path


def fetch_event_window_data(event_window_id: str, out_dir="data/raw"):
    url = f"{BASE_URL}/tournaments"
    params = { "eventWindowId": event_window_id }
    data = _make_request(url, params)
    out_path = f"{out_dir}/event_window_{event_window_id}/info.json"
    _save_json(data, out_path)
    return out_path


def fetch_by_event_window(event_window_id: str, out_dir="data/raw"):
    url = f"{BASE_URL}/matches"
    params = { "eventWindowId": event_window_id, "ignoreUploads": True }
    data = _make_request(url, params)
    out_path = f"{out_dir}/event_window_{event_window_id}/matches.json"
    _save_json(data, out_path)
    return out_path


def fetch_by_event(event_id: str, out_dir="data/raw"):
    url = f"{BASE_URL}/matches"
    params = { "eventId": event_id, "ignoreUploads": True }
    data = _make_request(url, params)
    out_path = f"{out_dir}/event_window_{event_id}/matches.json"
    _save_json(data, out_path)
    return out_path


if __name__ == "__main__":
    season = 37
    # match_id = "832ceecc424df110d58e3e96d3dff834"
    # fetch_match_info(match_id)
    event_window = "S29_FNCS_Major2_GrandFinalDay2_EU"
    # ew_data_path = fetch_event_window_data(event_window)

    interval_seconds = 604800
    print(json.dumps(fetch_tournaments(interval_seconds=interval_seconds), indent=2))
