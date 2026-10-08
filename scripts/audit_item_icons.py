"""
Report how well inventory item ids resolve to images for a set of matches.

Reads each match's ``playerInventoryUpdateEvents`` log from S3 and resolves
every ``itemId`` in the order documented in ``etl/static/items.py``: weapons,
``WMID_`` → ``WID_`` weapons, augments, static items, then pickaxes via the
same player's ``LoadoutSlot_Pickaxe`` cosmetic. Prints the share of item ids
and inventory events covered by each source, then the ids that resolve to no
image at all — the candidates to add to ``STATIC_ITEMS`` / ``item_icons``.

Read-only: S3 reads and DB selects only. Requires the catalog syncs and, for
pickaxes, the match's ``cosmetics`` stat to have run.

Usage:
    python scripts/audit_item_icons.py [match_id ...] [--latest N]
"""

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select

from etl.db.models import (
    Augment,
    Cosmetic,
    Match,
    MatchPlayer,
    MatchPlayerCosmetic,
    StaticItem,
    Weapon,
)
from etl.db.session import get_session
from etl.fetching.match_data_fetching import LOGS_BUCKET
from etl.storage.s3_client import S3TournamentLogStore

INVENTORY_LOG = "playerInventoryUpdateEvents"


def _latest_match_ids(n: int) -> list[str]:
    with get_session() as session:
        return list(
            session.scalars(select(Match.match_id).order_by(Match.start_time.desc()).limit(n))
        )


def _catalog(model) -> dict[str, str | None]:
    with get_session() as session:
        return dict(session.execute(select(model.id, model.image_key)).tuples().all())


def _pickaxe_images(match_id: str, cosmetic_images: dict[str, str | None]) -> dict[str, str | None]:
    """epic_id -> image key of the player's equipped pickaxe cosmetic."""
    with get_session() as session:
        rows = session.execute(
            select(MatchPlayer.epic_id, MatchPlayerCosmetic.cosmetic_id)
            .join(MatchPlayer, MatchPlayer.id == MatchPlayerCosmetic.player_id)
            .where(MatchPlayerCosmetic.match_id == match_id)
            .where(MatchPlayerCosmetic.loadout_slot == "LoadoutSlot_Pickaxe")
        ).tuples()
        return {epic_id: cosmetic_images.get(cosmetic_id) for epic_id, cosmetic_id in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit inventory item image coverage.")
    parser.add_argument("match_ids", nargs="*", help="Match ids to audit.")
    parser.add_argument(
        "--latest", type=int, default=3,
        help="When no match ids are given, audit the N most recent matches (default 3).",
    )
    args = parser.parse_args()
    match_ids = args.match_ids or _latest_match_ids(args.latest)

    weapons = _catalog(Weapon)
    augments = _catalog(Augment)
    static_items = _catalog(StaticItem)
    cosmetics = _catalog(Cosmetic)
    bucket = S3TournamentLogStore(bucket=LOGS_BUCKET)

    def resolve(item_id: str, epic_id: str, pickaxes: dict) -> tuple[str, str | None]:
        if item_id in weapons:
            return "weapon", weapons[item_id]
        if item_id.startswith("WMID_") and "WID_" + item_id[5:] in weapons:
            return "weapon (WMID)", weapons["WID_" + item_id[5:]]
        if item_id in augments:
            return "augment", augments[item_id]
        if item_id in static_items:
            return "static", static_items[item_id]
        if "pickaxe" in item_id.lower() and epic_id in pickaxes:
            return "pickaxe (loadout)", pickaxes[epic_id]
        return "unresolved", None

    events: collections.Counter = collections.Counter()      # (source, has_image) -> events
    ids_by_source: dict[tuple[str, bool], set[str]] = collections.defaultdict(set)
    no_image: collections.Counter = collections.Counter()    # item_id -> events

    for match_id in match_ids:
        print(f"Reading {INVENTORY_LOG} for {match_id}...")
        pickaxes = _pickaxe_images(match_id, cosmetics)
        for e in bucket.get_match_log(match_id, INVENTORY_LOG):
            item_id = e["itemId"]
            if not item_id:
                continue
            source, image_key = resolve(item_id, e["epicId"], pickaxes)
            key = (source, image_key is not None)
            events[key] += 1
            ids_by_source[key].add(item_id)
            if image_key is None:
                no_image[item_id] += 1

    total_events = sum(events.values())
    print(f"\nAudited {len(match_ids)} matches, {total_events} inventory events\n")
    print(f"{'source':<20} {'image':<6} {'ids':>5} {'events':>9} {'share':>7}")
    for (source, has_image), n in sorted(events.items(), key=lambda kv: -kv[1]):
        ids = len(ids_by_source[(source, has_image)])
        print(f"{source:<20} {'yes' if has_image else 'no':<6} {ids:>5} {n:>9} {n / total_events:>7.1%}")

    unresolved = ids_by_source[("unresolved", False)]
    print(f"\nItem ids with no image ({len(no_image)}; * = in no catalog at all):")
    for item_id, n in no_image.most_common():
        print(f"  {'*' if item_id in unresolved else ' '} {item_id:<50} {n:>7} events")


if __name__ == "__main__":
    main()
