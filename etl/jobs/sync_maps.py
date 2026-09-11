"""Sync map definitions from fnapi.osirion.gg into S3 and the DB.

Run this:
  - Once at the start of each new season (map geometry changes)
  - Any time a mid-season map update is detected
  - On initial setup to populate S3 and the maps table

Usage:
    python -m etl.jobs.sync_maps

S3 layout written by this job (under fortnite-tournament-objects):
    maps/snapshots/{timestamp}.json            — full API payload, all modes
    maps/versions/{major}.{minor}/{mode}.webp  — mirrored minimap image
    maps/versions/{major}.{minor}/{mode}.json  — projection definition
"""

import os
from datetime import datetime, timezone

import requests

from etl.api.fnapi_osirion_client import fetch_maps
from etl.db.loader import upsert_map
from etl.db.session import get_session
from etl.storage.s3_client import S3TournamentObjectStore

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_build_version(minimap_url: str) -> tuple[int, int]:
    """Extract (build_major, build_minor) from a minimapUrl.

    The URL pattern is:
        https://.../Minimaps/{major}.{minor}/{mode}.webp
    so the version segment is always the second-to-last path component.

    Examples:
        '.../Minimaps/41.00/br.webp' → (41, 0)
        '.../Minimaps/40.10/br.webp' → (40, 10)
    """
    version_segment = minimap_url.rstrip("/").split("/")[-2]
    major_str, minor_str = version_segment.split(".")
    return int(major_str), int(minor_str)


def _mirror_map(
    map_def: dict,
    build_major: int,
    build_minor: int,
    bucket: S3TournamentObjectStore,
) -> tuple[str | None, str | None]:
    """Download the map image and upload both the image and definition JSON to S3.

    This intentionally does no DB work — same thread-safety discipline as
    _mirror_one in sync_weapons: S3 I/O here, DB writes in the caller.

    Returns (image_key, definition_key).
    """
    mode_id = map_def["id"]
    image_key = None

    minimap_url = map_def.get("minimapUrl")
    if minimap_url:
        data = requests.get(minimap_url, timeout=30).content
        image_key = bucket.put_map_image(build_major, build_minor, mode_id, data)

    definition_key = bucket.put_map_definition(build_major, build_minor, mode_id, map_def)

    return image_key, definition_key


# ---------------------------------------------------------------------------
# Mirroring
# ---------------------------------------------------------------------------

def mirror_maps(map_defs: list[dict], bucket: S3TournamentObjectStore) -> None:
    """Mirror every map mode in ``map_defs`` to S3 and upsert the maps table.

    Each map def carries its own build version inside ``minimapUrl``, so this
    works uniformly for a live API payload and for a stored snapshot — the
    target ``maps/versions/{major}.{minor:02d}/`` folder is derived per mode,
    not assumed to be a single build.

    Modes without a ``minimapUrl`` are skipped (nothing to mirror).
    """
    for map_def in map_defs:
        mode_id = map_def["id"]

        minimap_url = map_def.get("minimapUrl", "")
        if not minimap_url:
            print(f"⚠️  Mode '{mode_id}' has no minimapUrl, skipping")
            continue

        build_major, build_minor = _parse_build_version(minimap_url)
        label = f"{build_major}.{build_minor:02d}/{mode_id}"
        print(f"Syncing {label}...")

        image_key, definition_key = _mirror_map(map_def, build_major, build_minor, bucket)

        with get_session() as session, session.begin():
            upsert_map(build_major, build_minor, mode_id, image_key, definition_key, session)

        print(f"  ✅ {label}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def sync_maps() -> None:
    """Full map sync: fetch all → snapshot → mirror every mode → upsert DB.

    Steps
    -----
    1. Fetch all map modes from the API (single request, current build only).
    2. Save a timestamped raw snapshot to S3 — the durable record used later by
       :mod:`scripts.backfill_maps_from_snapshots` to rebuild historical builds.
    3. Mirror every mode's image + definition JSON to S3 and upsert the maps
       table with the resulting S3 keys and a fresh synced_at.
    """
    print("Fetching map definitions from API...")
    all_maps = fetch_maps()
    print(f"Fetched {len(all_maps)} map modes.")

    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_key = bucket.put_map_snapshot(timestamp_str, all_maps)
    print(f"Snapshot saved → s3://{OBJECTS_BUCKET}/{snapshot_key}")

    mirror_maps(all_maps, bucket)

    print("\n✅ Map sync complete.")


if __name__ == "__main__":
    sync_maps()
