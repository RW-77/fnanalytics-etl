"""Sync the global cosmetic catalog from fnapi.osirion.gg into the DB and S3.

Covers ``/v1/cosmetics`` (outfits, back blings, pickaxes, gliders, emotes,
wraps, ...) plus ``/v1/cosmetics/banners`` (banner icons, which the main
listing omits) — together every id a match's ``cosmeticsV2`` loadout can
reference. The whole catalog is mirrored, not just cosmetics seen in matches,
so a cosmetic's image is already in place the first time someone wears it.

Run this:
  - On initial setup (mirrors ~37k images, ~0.5 GB; resumable if interrupted)
  - Whenever new cosmetics release (new season, weekly shop drops); later runs
    only mirror images for new cosmetics or ones whose image URL changed

Usage:
    python -m etl.jobs.sync_cosmetics

S3 layout written by this job (under fortnite-tournament-objects):
    cosmetics/snapshots/{timestamp}.json     — {"cosmetics": [...], "banners": [...]}
    cosmetics/images/{id}.{webp|jpg}         — large icon
    cosmetics/images/small/{id}.{webp|jpg}   — small icon
"""

import os
from datetime import datetime, timezone

from etl.api.fnapi_osirion_client import fetch_cosmetic_banners, fetch_cosmetics
from etl.db.loader import load_cosmetics, update_cosmetic_image_keys
from etl.db.session import get_session
from etl.storage.image_mirror import MirrorResult, mirror_images
from etl.storage.s3_client import S3TournamentObjectStore

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")

# Banner icons come from their own endpoint with no type; this mirrors the
# type id fnapi gives their sibling, banner colors ('homebasebannercolor').
BANNER_ICON_TYPE = "homebasebannericon"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_cosmetic(raw: dict) -> dict:
    """Map one raw API cosmetic dict to a flat dict matching the Cosmetic model.

    Input shape (abbreviated):
        {
            "id": "Character_HangNine",
            "name": "Malibu",
            "type": {"id": "character", "template": "AthenaCharacter", "name": "Outfit"},
            "rarity": {"id": "Rare", "name": "Rare"},
            "set": {"id": "...", "name": "...", "partOf": "..."} | null,
            "series": {"id": "...", "name": "..."} | null,
            "introduction": {"id": "26", "text": "Chapter 4, Season 4", ...} | null,
            "iconUrl": "https://...", "smallIconUrl": "https://...",
            "variants": [{"channel": "...", "name": "Style", "options": [...]}],
            "gameplayTags": [...], "path": "...", "shopHistory": [...]
        }
    ``path`` and ``shopHistory`` are dropped (kept in the S3 snapshot).
    """
    rarity = raw.get("rarity") or {}
    cosmetic_set = raw.get("set") or {}
    series = raw.get("series") or {}
    introduction = raw.get("introduction") or {}
    season = introduction.get("id")

    return {
        "id":            raw["id"],
        "name":          raw.get("name"),
        "description":   raw.get("description"),
        "cosmetic_type": raw["type"]["id"],
        "rarity":        rarity.get("id"),
        "set_name":      cosmetic_set.get("name"),
        "series_name":   series.get("name"),
        "introduction_season": int(season) if season and season.isdigit() else None,
        "introduction":  introduction.get("text"),
        "gameplay_tags": raw.get("gameplayTags"),
        "variants":      raw.get("variants") or None,
        "image_url":       raw.get("iconUrl"),
        "small_image_url": raw.get("smallIconUrl"),
    }


def _parse_banner(raw: dict) -> dict:
    """Map one raw API banner icon to the same flat Cosmetic shape."""
    return {
        "id":            raw["id"],
        "name":          raw.get("name"),
        "description":   raw.get("description"),
        "cosmetic_type": BANNER_ICON_TYPE,
        "rarity":        None,
        "set_name":      None,
        "series_name":   None,
        "introduction_season": None,
        "introduction":  None,
        "gameplay_tags": None,
        "variants":      None,
        "image_url":       raw.get("iconUrl"),
        "small_image_url": raw.get("smallIconUrl"),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _save_image_keys(results: list[MirrorResult]) -> None:
    with get_session() as session, session.begin():
        update_cosmetic_image_keys(results, session)


def sync_cosmetics() -> None:
    """Full cosmetic catalog sync: fetch → snapshot → upsert DB → mirror images.

    Steps
    -----
    1. Fetch the cosmetics and banner listings from the API.
    2. Save a timestamped raw snapshot of both to S3.
    3. Parse: flatten nested API fields into flat dicts matching the DB schema.
    4. Upsert every cosmetic. Metadata is overwritten; first_seen_at and
       already-mirrored image keys are preserved (unless the image URL changed).
    5. Mirror images only for cosmetics without S3 keys yet (new cosmetics,
       changed URLs, or ones where a previous mirror attempt failed).
    """
    print("Fetching cosmetic catalog from API...")
    raw_cosmetics = fetch_cosmetics()
    raw_banners = fetch_cosmetic_banners()
    print(f"Fetched {len(raw_cosmetics)} cosmetics and {len(raw_banners)} banners.")

    # Step 2 — snapshot
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_key = bucket.put_cosmetic_snapshot(
        timestamp_str, {"cosmetics": raw_cosmetics, "banners": raw_banners}
    )
    print(f"Snapshot saved → s3://{OBJECTS_BUCKET}/{snapshot_key}")

    # Step 3 — parse. Banner and cosmetic ids are disjoint today; should they
    # ever collide, the main listing wins.
    parsed_by_id = {b["id"]: _parse_banner(b) for b in raw_banners}
    parsed_by_id.update({c["id"]: _parse_cosmetic(c) for c in raw_cosmetics})
    parsed = list(parsed_by_id.values())

    # Step 4 — upsert; get back IDs that still need image mirroring
    with get_session() as session, session.begin():
        ids_needing_images = load_cosmetics(parsed, session)

    # Step 5 — mirror images for new/changed cosmetics only
    to_mirror = [c for c in parsed if c["id"] in ids_needing_images]
    mirror_images(
        to_mirror,
        bucket.put_cosmetic_image,
        bucket.put_cosmetic_small_image,
        _save_image_keys,
        label="cosmetics",
    )

    print("\n✅ Cosmetic sync complete.")


if __name__ == "__main__":
    sync_cosmetics()
