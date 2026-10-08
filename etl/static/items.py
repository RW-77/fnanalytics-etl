"""Hand-maintained inventory items that no fnapi catalog covers.

An inventory ``itemId`` resolves to an image through, in order:
    1. ``weapons.id``          — guns, most melee, traps (sync_weapons)
    2. ``WMID_<x>`` → ``weapons.id = 'WID_<x>'`` — a weapon with mods applied
    3. ``augments.id``         — ``PAID_*`` augments/boons/medallions (sync_augments)
    4. ``static_items.id``     — this map (sync_static_items)
    5. pickaxes (``WID_Harvest_Pickaxe_*``, ``Weapon_Pickaxe_*``) — the same
       player's ``LoadoutSlot_Pickaxe`` row in ``match_player_cosmetics``, whose
       ``cosmetic_id`` joins ``cosmetics``. Pickaxe weapon ids don't name their
       cosmetic (``WID_Harvest_Pickaxe_SharpDresser`` is
       ``Pickaxe_ID_074_SharpDresser``), so the loadout is the only exact link.

fnapi has nothing for consumables, ammo, materials, build pieces or objective
items (keycards, crowns), so they live here. Each entry gives what is known:
``name`` (None where the in-game name hasn't been confirmed), a UI
``category``, and optionally ``alias_of`` — a ``weapons.id`` whose image (and
name, when ``name`` is None) the item reuses. Icons for the rest are files in
``etl/static/item_icons/`` named ``<itemId>.<webp|png|jpg>``; until one is
added the item has a name/category but no image.

To find ids that still resolve to nothing, run
``python scripts/audit_item_icons.py`` against recent matches — the set of
consumables changes every season. After editing, run
``python -m etl.jobs.sync_static_items``.
"""

from dataclasses import dataclass

# UI groupings. 'internal' marks engine items a UI should hide.
MATERIAL = "material"
AMMO = "ammo"
BUILDING = "building"
TOOL = "tool"
CONSUMABLE = "consumable"
UTILITY = "utility"
MELEE = "melee"
OBJECTIVE = "objective"
CURRENCY = "currency"
INTERNAL = "internal"


@dataclass(frozen=True)
class StaticItem:
    name: str | None
    category: str | None
    alias_of: str | None = None


# Categories marked "(weaponType X)" come from the item's weaponType in the
# match weapons log, where the name is unconfirmed.
STATIC_ITEMS: dict[str, StaticItem] = {
    # Materials
    "WoodItemData":  StaticItem("Wood", MATERIAL),
    "StoneItemData": StaticItem("Brick", MATERIAL),
    "MetalItemData": StaticItem("Metal", MATERIAL),

    # Ammo
    "AthenaAmmoDataBulletsLight":  StaticItem("Light Bullets", AMMO),
    "AthenaAmmoDataBulletsMedium": StaticItem("Medium Bullets", AMMO),
    "AthenaAmmoDataBulletsHeavy":  StaticItem("Heavy Bullets", AMMO),
    "AthenaAmmoDataShells":        StaticItem("Shells", AMMO),
    "AmmoDataRockets":             StaticItem("Rockets", AMMO),
    # Weapon-specific ammo
    "AthenaAmmoData_Melee_Katana": StaticItem(None, AMMO),
    "Ammo_MotoWrestle":            StaticItem(None, AMMO),

    # Build pieces and tools
    "BuildingItemData_Wall":    StaticItem("Wall", BUILDING),
    "BuildingItemData_Floor":   StaticItem("Floor", BUILDING),
    "BuildingItemData_Stair_W": StaticItem("Stairs", BUILDING),
    "BuildingItemData_RoofS":   StaticItem("Cone", BUILDING),
    "EditTool":                 StaticItem("Edit Tool", TOOL),
    "WingSuit_Visuals_Gadget_TemporaryWorkAround": StaticItem(None, INTERNAL),

    # Heals and shields
    "WID_Paprika_ShieldSmall": StaticItem("Small Shield Potion", CONSUMABLE),
    "WID_Paprika_ShieldPot":   StaticItem("Shield Potion", CONSUMABLE),
    "WID_Paprika_MedBox":      StaticItem("Med Kit", CONSUMABLE),
    "WID_Paprika_Bandage":     StaticItem("Bandages", CONSUMABLE),
    "Athena_PurpleStuff_VR":   StaticItem("Slurp Juice", CONSUMABLE),
    "Athena_ChillBronco":      StaticItem("Chug Splash", CONSUMABLE),
    "WID_Athena_Flopper":      StaticItem("Flopper", CONSUMABLE),
    "WID_Athena_FlopperSmall": StaticItem("Small Fry", CONSUMABLE),
    "WID_Athena_Apple":        StaticItem("Apple", CONSUMABLE),
    "WID_Athena_Banana":       StaticItem("Banana", CONSUMABLE),
    "WID_Athena_Cabbage":      StaticItem("Cabbage", CONSUMABLE),
    "WID_Athena_Corn":         StaticItem("Corn", CONSUMABLE),
    "WID_Athena_Coconut":      StaticItem("Coconut", CONSUMABLE),
    "WID_Athena_ShieldMushroom": StaticItem("Shield Mushroom", CONSUMABLE),
    "WID_Athena_PizzaParty":   StaticItem("Pizza Party", CONSUMABLE),
    "WID_Athena_PizzaSlice":   StaticItem(None, CONSUMABLE),
    "WID_Athena_Flopper_Shield": StaticItem("Shield Fish", CONSUMABLE),
    "WID_Athena_ShieldGenerator": StaticItem(None, CONSUMABLE),  # (weaponType SHIELD_HEAL)
    # The gadget ("Pocket") variant in the weapons catalog shares the item's icon.
    "WID_MedCloud":            StaticItem(None, CONSUMABLE, alias_of="GID_MedCloud"),

    # Movement / protection / information gadgets
    "WID_Athena_AppleSunSmall":    StaticItem(None, UTILITY, alias_of="GID_Athena_AppleSunSmall"),
    "Athena_SilverBlazer_Mini_UC": StaticItem(None, UTILITY, alias_of="GID_SilverBlazer_Mini_UC"),
    "Athena_Stimpulse":            StaticItem("Overdrive Grenade", UTILITY),
    "WID_NarrowFleaObsidian":      StaticItem("Sonic Ball", UTILITY),
    "Athena_ShockGrenade":         StaticItem("Shockwave Grenade", UTILITY),
    "WID_Athena_LaunchPadThrown":       StaticItem(None, UTILITY),  # (weaponType MOVEMENT)
    "WID_Athena_LaunchPadThrown_Super": StaticItem(None, UTILITY),
    "WID_Athena_SpicySoda":        StaticItem(None, UTILITY),  # (weaponType MOVEMENT)
    "WID_Athena_VividBronco":      StaticItem(None, UTILITY),  # (weaponType MOVEMENT)
    "WID_StealthSplash":           StaticItem(None, UTILITY),  # (weaponType PROTECTION)
    "WID_Athena_ThrownMarking":    StaticItem(None, UTILITY),  # (weaponType INFORMATION)

    # Melee weapons missing from /v1/weapons
    "WID_Athena_ElectroBat":  StaticItem(None, MELEE),  # (weaponType MELEE)
    "WID_FirePetal_Masamune": StaticItem(None, MELEE),  # (weaponType MELEE)

    # Objective items: crowns, keycards, vault access
    "AGID_VictoryCrown":                    StaticItem("Victory Crown", OBJECTIVE),
    "AGID_Athena_Keycard_Outlaw_1":         StaticItem(None, OBJECTIVE),
    "AGID_Athena_Keycard_Outlaw_2":         StaticItem(None, OBJECTIVE),
    "AGID_Athena_Keycard_Outlaw_3":         StaticItem(None, OBJECTIVE),
    "AGID_Athena_Keycard_Outlaw_4":         StaticItem(None, OBJECTIVE),
    "AGID_Athena_Keycard_Outlaw_5":         StaticItem(None, OBJECTIVE),
    "AGID_ParticleAccelerator_VaultAccess": StaticItem(None, OBJECTIVE),
    "AGID_CosmicThunder_TempleVaultKey":    StaticItem(None, OBJECTIVE),
    "AGID_Heist_RelicVault":                StaticItem(None, OBJECTIVE),
    "AGID_Heist_RelicVault_MotherLode":     StaticItem(None, OBJECTIVE),
    "AGID_DuckVault_Access":                StaticItem(None, OBJECTIVE),
    "AGID_HauntedManor_SkeletonKey":        StaticItem(None, OBJECTIVE),

    # Currency
    "Athena_WadsItemData": StaticItem("Gold Bars", CURRENCY),
}
