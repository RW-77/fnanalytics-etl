from etl.types import JsonList, RawMatchData
from etl.parsing.common.eligibility import eligible_player_ids


def parse_cosmetic_loadouts(cosmetics: JsonList, eligible: set[str]) -> list[dict]:
    """One row per (player, loadout slot) from a ``cosmeticsV2`` log.

    Each log row is one equipped cosmetic: ``loadoutSlot`` (e.g.
    ``LoadoutSlot_Character``), the ``cosmetic`` id, and the selected
    ``cosmeticStyles``. Players outside ``eligible`` (spectators, bots) are
    skipped. Should a (player, slot) ever repeat, the last row wins — the table
    holds one cosmetic per slot.
    """
    by_slot: dict[tuple[str, str], dict] = {}
    for e in cosmetics:
        player_id = e["epicId"]
        if player_id not in eligible or not e.get("cosmetic"):
            continue

        slot = e["loadoutSlot"]
        by_slot[(player_id, slot)] = {
            "player_id": player_id,
            "loadout_slot": slot,
            "cosmetic_id": e["cosmetic"],
            "styles": e.get("cosmeticStyles") or [],
        }

    return list(by_slot.values())


def parse_match_cosmetics(raw: RawMatchData) -> list[dict]:
    """Every eligible player's equipped cosmetics for the match."""
    return parse_cosmetic_loadouts(raw.cosmetics, eligible_player_ids(raw))
