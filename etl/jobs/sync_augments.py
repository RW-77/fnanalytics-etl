"""Sync the global augment catalog from fnapi.osirion.gg into the DB and S3.

Augments, boons and medallions — the ``PAID_*`` items in player inventories,
whose ids match the API's exactly.

Run this:
  - Once at the start of a new season (new augments/boons introduced)
  - On initial setup to populate the augments table

Usage:
    python -m etl.jobs.sync_augments

S3 layout written by this job (under fortnite-tournament-objects):
    augments/snapshots/{timestamp}.json   — full API payload
    augments/images/{id}.webp             — large icon
    augments/images/small/{id}.webp       — small icon
"""

import os
from datetime import datetime, timezone

from etl.api.fnapi_osirion_client import fetch_augments
from etl.db.loader import load_augments, update_augment_image_keys
from etl.db.session import get_session
from etl.storage.image_mirror import MirrorResult, mirror_images
from etl.storage.s3_client import S3TournamentObjectStore

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_augment(raw: dict) -> dict:
    """Map one raw API augment dict to a flat dict matching the Augment model.

    Input shape (abbreviated):
        {
            "id": "PAID_VividRazor_Greedy",
            "name": "Greed Boon",
            "rarity": {"id": "Epic", "name": "Epic"},
            "iconUrl": "https://...", "smallIconUrl": "https://...",
            "gameplayTags": [...], "path": "..."
        }
    About a third of augments have no icon (both URLs null).
    """
    rarity = raw.get("rarity") or {}

    return {
        "id":            raw["id"],
        "name":          raw.get("name"),
        "description":   raw.get("description"),
        "rarity":        rarity.get("id"),
        "gameplay_tags": raw.get("gameplayTags"),
        "image_url":       raw.get("iconUrl"),
        "small_image_url": raw.get("smallIconUrl"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _save_image_keys(results: list[MirrorResult]) -> None:
    with get_session() as session, session.begin():
        update_augment_image_keys(results, session)


def sync_augments() -> None:
    """Full augment catalog sync: fetch → snapshot → upsert DB → mirror images.

    Same steps as :func:`etl.jobs.sync_weapons.sync_weapons`; images are only
    mirrored for augments without S3 keys yet (new augments, changed image
    URLs, or previously failed mirrors).
    """
    print("Fetching augment catalog from API...")
    raw_augments = fetch_augments()
    print(f"Fetched {len(raw_augments)} augments.")

    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_key = bucket.put_augment_snapshot(timestamp_str, raw_augments)
    print(f"Snapshot saved → s3://{OBJECTS_BUCKET}/{snapshot_key}")

    parsed_augments = [_parse_augment(a) for a in raw_augments]

    with get_session() as session, session.begin():
        ids_needing_images = load_augments(parsed_augments, session)

    to_mirror = [a for a in parsed_augments if a["id"] in ids_needing_images]
    mirror_images(
        to_mirror,
        bucket.put_augment_image,
        bucket.put_augment_small_image,
        _save_image_keys,
        label="augments",
    )

    print("\n✅ Augment sync complete.")


if __name__ == "__main__":
    sync_augments()
