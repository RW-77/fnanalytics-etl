import json
import requests

from etl.types import JsonDict, JsonList


FNAPI_BASE_URL = "https://fnapi.osirion.gg/v1"


def fetch_maps() -> JsonList:
    """Fetch all map modes for the current build. Useful for snapshots."""
    response = requests.get(f"{FNAPI_BASE_URL}/maps")
    response.raise_for_status()
    data: JsonDict = response.json()
    return data["maps"]


def fetch_map_mode(mode_id: str = "br", lang: str = "en") -> JsonDict:
    """Fetch the map definition for a single mode (e.g. 'br').

    Returns the ``map`` dict which includes: id, mode, minimapUrl,
    minimapCenterLocation, sizeCm, rotationOffset, pois.
    The build version can be parsed from the minimapUrl path
    (e.g. 'https://.../Minimaps/41.00/br.webp' → '41.00').
    """
    response = requests.get(
        f"{FNAPI_BASE_URL}/maps/mode",
        params={"id": mode_id, "lang": lang},
    )
    response.raise_for_status()
    data: JsonDict = response.json()
    return data["map"]


def fetch_weapons(lang: str = "en") -> JsonList:
    response = requests.get(f"{FNAPI_BASE_URL}/weapons", params={"lang": lang})
    response.raise_for_status()
    data: JsonDict = response.json()
    return data["weapons"]


# Valid TournamentRegion values for ``fetch_tournaments``; None returns all.
TOURNAMENT_REGIONS = ("NAC", "NAE", "NAW", "EU", "ASIA", "ME", "OCE", "BR", "ONSITE")


def fetch_tournaments(
    region: str | None = None,
    include_historic: bool = False,
    lang: str = "en",
) -> JsonList:
    """Fetch tournament metadata in Epic 'fnpubapi' format.

    Each tournament carries ``eventWindows[] -> scoreLocations[] ->
    scoringRules[]`` (the point formula: per-placement and per-elim tiers) and
    ``payoutTables[]`` (prize/reward-by-rank). This is the source of the scoring
    config needed to turn a leaderboard's per-match ``trackedStats`` into points.

    ``region`` filters to a single :data:`TOURNAMENT_REGIONS` value; ``None``
    returns every region. ``include_historic=True`` reaches events older than
    three months (currently back to ~mid-2024).

    NB: ``includeHistoricData`` is case-sensitive on the API — only the
    lowercase ``"true"`` enables it; ``requests`` would serialize a Python bool
    as ``"True"`` (capital), which the API silently ignores and returns just the
    3-month rolling set. Hence the explicit ``str(...).lower()``.
    """
    params: JsonDict = {
        "includeHistoricData": str(include_historic).lower(),
        "lang": lang,
    }
    if region is not None:
        params["region"] = region
    response = requests.get(f"{FNAPI_BASE_URL}/tournaments", params=params)
    response.raise_for_status()
    data: JsonDict = response.json()
    return data["tournaments"]


def fetch_tournament_leaderboard_page(
    leaderboard_event_id: str,
    leaderboard_event_window_id: str,
    page: int = 0,
) -> JsonDict:
    """Fetch a single leaderboard page for one event window.

    Returns the raw ``leaderboard`` object: ``page``, ``totalPages`` (capped at
    100), ``updatedAt``, and ``entries[]`` (one per team, ≤100 per page). Each
    entry has cumulative ``pointsEarned``/``score``/``rank`` and a per-match
    ``sessionHistory[]``. The ``leaderboardEventId`` / ``leaderboardEventWindowId``
    come from a tournament's ``scoreLocations`` (see :func:`fetch_tournaments`).
    """
    response = requests.get(
        f"{FNAPI_BASE_URL}/tournaments/leaderboard",
        params={
            "leaderboardEventId": leaderboard_event_id,
            "leaderboardEventWindowId": leaderboard_event_window_id,
            "page": page,
        },
    )
    response.raise_for_status()
    data: JsonDict = response.json()
    return data["leaderboard"]


def fetch_tournament_leaderboard(
    leaderboard_event_id: str,
    leaderboard_event_window_id: str,
) -> JsonList:
    """Fetch every team entry across all pages of a window's leaderboard.

    Pages through until ``page + 1 >= totalPages`` and returns the concatenated
    ``entries``. The endpoint is rate-limited to 60 req/min; windows with a large
    field (up to 100 pages = 10k teams) may need throttling by the caller.
    """
    first = fetch_tournament_leaderboard_page(
        leaderboard_event_id, leaderboard_event_window_id, page=0
    )
    entries: JsonList = list(first["entries"])
    total_pages = int(first["totalPages"])
    for page in range(1, total_pages):
        nxt = fetch_tournament_leaderboard_page(
            leaderboard_event_id, leaderboard_event_window_id, page=page
        )
        entries.extend(nxt["entries"])
    return entries


if __name__ == "__main__":
    import os, pathlib
    root = pathlib.Path(__file__).parent.parent.parent

    maps_dir = root / "data" / "raw" / "maps"
    os.makedirs(maps_dir, exist_ok=True)

    # Full snapshot of all modes (bookkeeping)
    all_maps = fetch_maps()
    all_path = maps_dir / "maps.json"
    with open(all_path, "w") as f:
        json.dump(all_maps, f, indent=2)
    print(f"Saved {len(all_maps)} maps to {all_path}")

    # BR-specific definition
    br_map = fetch_map_mode("br")
    br_path = maps_dir / "br.json"
    with open(br_path, "w") as f:
        json.dump(br_map, f, indent=2)
    build_version = br_map["minimapUrl"].split("/")[-2]  # e.g. "41.00"
    print(f"Saved BR map (build {build_version}) to {br_path}")
