"""Each player's inventory over time, as a compact change log (inventory.json).

The raw ``playerInventoryUpdateEvents`` log (~50 MB, ~100k events per match)
records one event per inventory slot change. A slot is a position in the
player's inventory, and ``slot`` — not ``itemEntryGuid`` — identifies the item
in it: a removal event names only the slot, with an empty ``itemId`` and GUID.
About a third of the events repeat the slot's state unchanged; those are
dropped, along with every field the replay doesn't need.

Some weapons are two entries: the primary (``WID_HeatSnake_Drift_UC``, Rebecca's
Gun) names its alternate-fire entry (``WID_HeatSnake_Drift_Secondary_UC``, in
its own slot) in ``secondaryItemEntryGuid``. The secondary is part of the same
hotbar item, so it's dropped too.

Output shape::

    {
      "schema_version": 1,
      "match_id": "...",
      "items": ["WoodItemData", "WID_Paprika_ShieldPot", ...],
      "players": {
        "<epic id>": [
          [t, slot, item, count, ammo?],   # the slot now holds items[item]
          [t, slot, -1],                   # the slot emptied
          ...
        ]
      }
    }

``t`` is seconds since bus launch (``aircraftStartTime``), the replay clock's
origin; items granted before it (pickaxe, build pieces) have negative times.
``ammo`` is the weapon's loaded magazine, omitted when 0. Changes are in time
order per player, so a player's inventory at time T is the replay of their
changes with ``t <= T``. ``count`` is kept as reported, including 0 (an empty
material slot stays in the inventory).
"""

from etl.types import JsonList, RawMatchData
from etl.parsing.common.eligibility import eligible_player_ids

ASSET_SCHEMA_VERSION = 1

REMOVED = -1


def build_inventory_asset(
    match_id: str,
    t_0: int,
    eligible: set[str],
    events: JsonList,
) -> dict:
    """Build inventory.json from raw inventory events.

    ``t_0`` is ``aircraftStartTime`` (µs); ``eligible`` the match players
    (spectators and bots excluded).
    """
    items: list[str] = []
    item_index: dict[str, int] = {}
    players: dict[str, list[list]] = {}
    # (player, slot) -> (item, count, ammo) currently in the slot
    held: dict[tuple[str, int], tuple[int, int, int]] = {}

    # Entries some other entry names as its secondary (alternate fire). A
    # secondary can be logged before its primary, so they're collected first.
    secondaries = {
        (e["epicId"], e["secondaryItemEntryGuid"])
        for e in events
        if e.get("secondaryItemEntryGuid")
    }

    # sorted() is stable, so same-timestamp events keep their log order.
    for e in sorted(events, key=lambda e: e["timestamp"]):
        player_id = e["epicId"]
        if player_id not in eligible:
            continue
        slot = e["slot"]
        t = round((e["timestamp"] - t_0) * 1e-6, 2)

        if e.get("removed"):
            if held.pop((player_id, slot), None) is not None:
                players.setdefault(player_id, []).append([t, slot, REMOVED])
            continue

        item_id = e.get("itemId")
        if not item_id or (player_id, e.get("itemEntryGuid")) in secondaries:
            continue   # never held, so this slot's removal is ignored too
        if item_id not in item_index:
            item_index[item_id] = len(items)
            items.append(item_id)

        state = (item_index[item_id], e.get("count", 0), e.get("ammo", 0))
        if held.get((player_id, slot)) == state:
            continue   # nothing visible changed (a repeat, or only GUIDs/mods)
        held[(player_id, slot)] = state

        change = [t, slot, state[0], state[1]]
        if state[2]:
            change.append(state[2])
        players.setdefault(player_id, []).append(change)

    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "match_id": match_id,
        "items": items,
        "players": players,
    }


def parse_match_inventory(raw: RawMatchData) -> dict:
    """inventory.json for a match (see :func:`build_inventory_asset`)."""
    return build_inventory_asset(
        raw.match_id,
        raw.info["aircraftStartTime"],
        eligible_player_ids(raw),
        raw.inventory_update_events,
    )
