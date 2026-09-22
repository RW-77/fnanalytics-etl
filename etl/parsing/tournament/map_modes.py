"""Resolve a match's ``mapPath`` to a fnapi ``/v1/maps`` mode id.

The maps table is keyed by ``(build_major, build_minor, mode_id)``, but a match
only records its ``mapPath`` — an Epic-internal asset path such as
``/dcab7512-.../MatchMist``. The ``/v1/maps`` API never exposes that path, so the
mapping to a mode id has to be maintained by hand here.

We key on the full ``mapPath`` because its GUID is stable across builds and
seasons. (``gameMode`` is not: the same MatchMist map appears as
``...RE_MatchMistDuos_CR`` in the Reload Elite Series and
``...FNCS_MatchMistDuos`` at the EWC finals.)

Only the two maps used in competitive this year are mapped. Both were confirmed
against the EWC finals (event window ``Escargo_Day4``) in chronological order:
    Game 1 → Elite Stronghold, and game 1's mapPath is MatchMist
    Game 9 → Slurp Rush,       and game 9's mapPath is DashBerry

Anything not listed resolves to ``"br"`` — the historical default, since every
non-Reload map has always been treated as Battle Royale. The trade-off: a new
competitive (non-BR) map renders as BR until its mapPath is added here.
"""

DEFAULT_MODE_ID = "br"

MAP_PATH_TO_MODE_ID: dict[str, str] = {
    "/dcab7512-4874-bbe0-f383-9ab040fcd9fc/MatchMist": "reload-elitestronghold",
    "/f4032749-42c4-7fe9-7fa2-c78076f34f54/DashBerry": "reload-slurp",
}


def resolve_mode_id(map_path: str | None) -> str:
    """Return the fnapi mode id for a match's ``mapPath`` (``"br"`` if unmapped)."""
    return MAP_PATH_TO_MODE_ID.get(map_path, DEFAULT_MODE_ID)
