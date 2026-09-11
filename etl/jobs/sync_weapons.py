"""Sync the global weapon catalog from fnapi.osirion.gg into the DB and S3.

Run this:
  - Once at the start of a new season (new weapons introduced)
  - Whenever weapon stats change (buffs/nerfs)
  - On initial setup to populate the weapons table

Usage:
    python -m etl.jobs.sync_weapons
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from etl.api.fnapi_osirion_client import fetch_weapons
from etl.db.loader import load_weapons, update_weapon_image_keys
from etl.db.session import get_session
from etl.storage.s3_client import S3TournamentObjectStore

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")
IMAGE_MIRROR_WORKERS = int(os.getenv("IMAGE_MIRROR_WORKERS", "8"))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_weapon(raw: dict) -> dict:
    """Map one raw API weapon dict to a flat dict matching the Weapon model.

    The API returns nested structures (rarity.id, stats.DmgPB) and camelCase
    keys. This flattens and renames them to match DB column names.

    Input shape (abbreviated):
        {
            "id": "WID_Assault_...",
            "type": "FortWeaponRangedItemDefinition",
            "rarity": {"id": "Rare", "name": "Rare"},
            "imageUrl": "https://...",
            "stats": {"DmgPB": 36, "FiringRate": 5.5, ...},
            "gameplayTags": ["Weapon.Ranged.Assault", ...]
        }
    """
    stats = raw.get("stats") or {}
    rarity = raw.get("rarity") or {}

    return {
        "id":           raw["id"],
        "name":         raw.get("name"),
        "description":  raw.get("description"),
        "weapon_type":  raw.get("type"),           # API field is "type"
        "rarity":       rarity.get("id"),           # flatten rarity.id → rarity
        "ammo":         raw.get("ammo"),
        "gameplay_tags": raw.get("gameplayTags"),   # stored as JSON array in DB
        # Image URLs — S3 keys start as None; filled in after mirroring
        "image_url":       raw.get("imageUrl"),
        "small_image_url": raw.get("smallImageUrl"),
        "image_key":       None,
        "small_image_key": None,
        # Damage stats — flatten stats.{PascalCase} → snake_case columns
        "dmg_pb":                stats.get("DmgPB"),
        "firing_rate":           stats.get("FiringRate"),
        "clip_size":             stats.get("ClipSize"),
        "reload_time":           stats.get("ReloadTime"),
        "bullets_per_cartridge": stats.get("BulletsPerCartridge"),
        "spread":                stats.get("Spread"),
        "spread_downsights":     stats.get("SpreadDownsights"),
        "damage_zone_critical":  stats.get("DamageZone_Critical"),
    }


# ---------------------------------------------------------------------------
# Image mirroring
# ---------------------------------------------------------------------------

def _mirror_one(
    weapon: dict,
    bucket: S3TournamentObjectStore,
) -> tuple[str, str | None, str | None]:
    """Download one weapon's images and upload them to S3.

    This runs on a worker thread. It must not touch the SQLAlchemy session —
    sessions are not thread-safe. It only does HTTP downloads and S3 uploads,
    then returns the resulting S3 keys so the main thread can write them to DB.
    """
    weapon_id = weapon["id"]
    image_key = small_image_key = None

    if weapon.get("image_url"):
        data = requests.get(weapon["image_url"], timeout=30).content
        image_key = bucket.put_weapon_image(weapon_id, data)

    if weapon.get("small_image_url"):
        data = requests.get(weapon["small_image_url"], timeout=30).content
        small_image_key = bucket.put_weapon_small_image(weapon_id, data)

    return weapon_id, image_key, small_image_key


def _mirror_images(
    to_mirror: list[dict],
    bucket: S3TournamentObjectStore,
) -> None:
    """Download and upload images for a batch of weapons, in parallel.

    How the thread pool works
    -------------------------
    pool.submit(fn, arg) schedules fn(arg) on a worker thread and immediately
    returns a Future — a receipt for a result that isn't ready yet. We collect
    all futures in a dict keyed by weapon_id so we can log which weapon failed
    if something goes wrong.

    as_completed(futures) is a generator that yields each future as soon as its
    thread finishes — in completion order, not submission order. Processing
    results here on the main thread keeps the DB session thread-safe: worker
    threads only do HTTP + S3; the main thread does all DB writes.

    Each successful mirror is committed in its own small transaction so a
    later failure can't roll back keys we've already saved.
    """
    if not to_mirror:
        return

    print(f"Mirroring images for {len(to_mirror)} weapons "
          f"({IMAGE_MIRROR_WORKERS} concurrent workers)...")

    succeeded = failed = 0

    with ThreadPoolExecutor(max_workers=IMAGE_MIRROR_WORKERS) as pool:
        # Submit all weapons at once. Up to IMAGE_MIRROR_WORKERS run in
        # parallel; the rest wait in the pool's internal queue.
        futures = {
            pool.submit(_mirror_one, w, bucket): w["id"]
            for w in to_mirror
        }

        for future in as_completed(futures):
            weapon_id = futures[future]   # look up which weapon this was
            try:
                _, image_key, small_image_key = future.result()

                # DB write back on main thread
                with get_session() as session, session.begin():
                    update_weapon_image_keys(weapon_id, image_key, small_image_key, session)

                succeeded += 1
                print(f"  ✅ {weapon_id}")

            except Exception as e:
                # Log and continue — one bad URL won't abort the whole batch
                failed += 1
                print(f"  ❌ {weapon_id}: {e}")

    print(f"Image mirroring complete — {succeeded} succeeded, {failed} failed.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def sync_weapons() -> None:
    """Full weapon catalog sync: fetch → snapshot → upsert DB → mirror images.

    Steps
    -----
    1. Fetch the current weapon list from the API.
    2. Save a timestamped raw snapshot to S3. This is the historical source of
       truth — if a weapon gets buffed/nerfed the DB is updated in place, but
       the snapshot lets us recover what stats were in effect at a given date.
    3. Parse: flatten nested API fields into flat dicts matching the DB schema.
    4. Upsert all weapons. Stats are overwritten; first_seen_at and any
       already-mirrored image keys are preserved.
    5. Mirror images only for weapons that don't have S3 keys yet (new weapons,
       or ones where a previous mirror attempt failed).
    """
    print("Fetching weapon catalog from API...")
    raw_weapons = fetch_weapons()
    print(f"Fetched {len(raw_weapons)} weapons.")

    # Step 2 — snapshot
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_key = bucket.put_weapon_snapshot(timestamp_str, raw_weapons)
    print(f"Snapshot saved → s3://{OBJECTS_BUCKET}/{snapshot_key}")

    # Step 3 — parse
    parsed_weapons = [_parse_weapon(w) for w in raw_weapons]

    # Step 4 — upsert; get back IDs that still need image mirroring
    with get_session() as session, session.begin():
        ids_needing_images = load_weapons(parsed_weapons, session)

    # Step 5 — mirror images for new weapons only
    to_mirror = [w for w in parsed_weapons if w["id"] in ids_needing_images]
    _mirror_images(to_mirror, bucket)

    print("\n✅ Weapon sync complete.")


if __name__ == "__main__":
    sync_weapons()
