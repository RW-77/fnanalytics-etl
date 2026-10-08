"""Sync the hand-maintained static item map into the DB and S3.

Static items are the inventory items no fnapi catalog covers (consumables,
ammo, materials, build pieces, objective items); they are defined in
:data:`etl.static.items.STATIC_ITEMS`, with optional icons in
``etl/static/item_icons/<itemId>.<webp|png|jpg>``.

Run this after editing the map or adding icons, and after ``sync_weapons``
when an entry aliases a weapon (its image key is copied from the weapon row).
The map is the source of truth: rows for removed entries are deleted.

Usage:
    python -m etl.jobs.sync_static_items

S3 layout written by this job (under fortnite-tournament-objects):
    static_items/images/{id}.{webp|png|jpg}
"""

import os
from pathlib import Path

from sqlalchemy import select

from etl.db.loader import sync_static_item_rows
from etl.db.models import Weapon
from etl.db.session import get_session
from etl.static.items import STATIC_ITEMS
from etl.storage.s3_client import IMAGE_EXTENSIONS, S3TournamentObjectStore

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")
ICONS_DIR = Path(__file__).resolve().parent.parent / "static" / "item_icons"
CONTENT_TYPES = {extension: content_type for content_type, extension in IMAGE_EXTENSIONS.items()}


def _find_icon(item_id: str) -> Path | None:
    """The local icon file for an item, if one has been added."""
    for extension in IMAGE_EXTENSIONS.values():
        path = ICONS_DIR / f"{item_id}.{extension}"
        if path.is_file():
            return path
    return None


def sync_static_items() -> None:
    """Upload local icons, resolve aliases, and replace the static_items rows.

    Every local icon is re-uploaded on each run — there are only a few dozen,
    so this keeps S3 in step with edited files without change tracking.
    """
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)

    unknown_icons = sorted(
        p.name for p in ICONS_DIR.iterdir()
        if not p.name.startswith(".") and p.stem not in STATIC_ITEMS
    )
    if unknown_icons:
        print(f"⚠️  Icons with no STATIC_ITEMS entry (ignored): {', '.join(unknown_icons)}")

    alias_ids = {item.alias_of for item in STATIC_ITEMS.values() if item.alias_of}
    with get_session() as session:
        aliased = {
            weapon_id: (name, image_key)
            for weapon_id, name, image_key in session.execute(
                select(Weapon.id, Weapon.name, Weapon.image_key)
                .where(Weapon.id.in_(alias_ids))
            )
        }

    rows = []
    uploaded = 0
    for item_id, item in STATIC_ITEMS.items():
        alias_name = alias_image_key = None
        if item.alias_of:
            if item.alias_of in aliased:
                alias_name, alias_image_key = aliased[item.alias_of]
            else:
                print(f"⚠️  {item_id}: alias {item.alias_of} not in weapons table")

        # A local icon takes precedence over the alias's image.
        image_key = alias_image_key
        icon = _find_icon(item_id)
        if icon is not None:
            content_type = CONTENT_TYPES[icon.suffix.lstrip(".")]
            image_key = bucket.put_static_item_image(item_id, icon.read_bytes(), content_type)
            uploaded += 1

        rows.append({
            "id": item_id,
            "name": item.name or alias_name,
            "category": item.category,
            "alias_of": item.alias_of,
            "image_key": image_key,
        })

    with get_session() as session, session.begin():
        sync_static_item_rows(rows, session)

    with_image = sum(1 for r in rows if r["image_key"])
    print(
        f"✅ Synced {len(rows)} static items — {with_image} with an image "
        f"({uploaded} uploaded icons, {with_image - uploaded} via alias)"
    )


if __name__ == "__main__":
    sync_static_items()
