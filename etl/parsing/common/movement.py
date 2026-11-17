from typing import Any


def movement_data(event: dict[str, Any]) -> dict[str, Any]:
    """Return a movement event's payload (``location``, ``rotationYaw``, ...).

    Osirion parser 4.24 (Fortnite build 43.00+) renamed ``movementData`` to
    ``movementDataV2``, replacing the per-state booleans (``isSkydiving`` etc.)
    with a ``flags`` bitmask. ``location`` and the rotations are unchanged, so
    callers can read either schema through this accessor.
    """
    data = event.get("movementDataV2")
    if data is None:
        data = event["movementData"]
    return data
