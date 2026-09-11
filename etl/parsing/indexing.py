import bisect
from collections import defaultdict
from typing import Any, NamedTuple
from etl.types import RawMatchData


import numpy as np


EventDict = dict[str, Any]


class PlayerPosition(NamedTuple):
    location: tuple[float, float, float]
    dt: int
    interpolated: bool


class PlayerPositionIndex:
    """Reusable "where was player P at time t?" service.

    Built once per match from :class:`RawMatchData`. ``position_at`` returns the
    player's best-known location at a query timestamp, or ``None`` when there is
    no reliable answer — the player was dead at that time, was never observed,
    or no movement sample is close enough in time to trust.
    """

    def __init__(self, raw: RawMatchData):
        # Per-player movement samples, sorted by timestamp. Parallel arrays:
        # ``_ts`` for the bisect, ``_loc`` indexed alongside it.
        self._ts: dict[str, list[int]] = defaultdict(list)
        self._loc: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        for me in sorted(raw.movement_events, key=lambda e: e["timestamp"]):
            loc = me["movementData"]["location"]
            self._ts[me["epicId"]].append(me["timestamp"])
            self._loc[me["epicId"]].append((loc["x"], loc["y"], loc["z"]))

        # Per-player alive toggles: an elimination flips a player dead, a reboot
        # flips them alive again. Stored as parallel sorted arrays so an
        # alive-at-t check is a single bisect. (reviveEvents are NOT toggles — a
        # revive is getting up from a knock, not a return from elimination.)
        toggles: dict[str, list[tuple[int, bool]]] = defaultdict(list)
        for e in raw.elimination_events:
            toggles[e["targetId"]].append((e["timestamp"], False))
        for r in raw.reboot_events:
            toggles[r["epicId"]].append((r["timestamp"], True))

        self._toggle_ts: dict[str, list[int]] = {}
        self._toggle_alive: dict[str, list[bool]] = {}
        for pid, evs in toggles.items():
            evs.sort(key=lambda x: x[0])
            self._toggle_ts[pid] = [t for t, _ in evs]
            self._toggle_alive[pid] = [alive for _, alive in evs]

    def _alive_at(self, epic_id: str, query_ts: int) -> bool:
        """True unless the most recent elimination/reboot toggle before
        ``query_ts`` left the player eliminated."""
        ts = self._toggle_ts.get(epic_id)
        if not ts:
            return True  # never eliminated -> alive the whole match
        i = bisect.bisect_right(ts, query_ts) - 1
        if i < 0:
            return True  # before their first elimination -> first life
        return self._toggle_alive[epic_id][i]

    def position_at(
        self, 
        query_ts: int, 
        epic_id: str, 
        max_gap: int,
    ) -> PlayerPosition | None:
        # (1) Alive gate — a dead player has no position to give.
        if not self._alive_at(epic_id, query_ts):
            return None

        ts = self._ts.get(epic_id)
        if not ts:
            return None  # never observed moving
        loc = self._loc[epic_id]

        # (2) Bracket — the samples just before and just after query_ts, each
        # bundled as (gap_to_query, location), or None when that side is missing.
        i = bisect.bisect_left(ts, query_ts)
        before = (query_ts - ts[i - 1], loc[i - 1]) if i > 0 else None
        after = (ts[i] - query_ts, loc[i]) if i < len(ts) else None

        candidates = [c for c in (before, after) if c is not None]
        nearest_gap = min(gap for gap, _ in candidates)

        # (3) Staleness guard — the nearest sample must be recent enough.
        if nearest_gap > max_gap:
            return None

        # (4) Interpolate when we have a trustworthy sample on BOTH sides.
        if (
            before is not None and after is not None
            and before[0] <= max_gap and after[0] <= max_gap
        ):
            gap_before, b = before
            gap_after, a = after
            w = gap_before / (gap_before + gap_after)  # 0 -> before, 1 -> after
            interp = (
                b[0] + (a[0] - b[0]) * w,
                b[1] + (a[1] - b[1]) * w,
                b[2] + (a[2] - b[2]) * w,
            )
            return PlayerPosition(location=interp, dt=nearest_gap, interpolated=True)

        # (5) Snap to the nearest sample (already known to be within max_gap).
        _, nearest_loc = min(candidates, key=lambda c: c[0])
        return PlayerPosition(location=nearest_loc, dt=nearest_gap, interpolated=False)


def indexed_events(
    reference_events: list[EventDict],
    player_events: list[EventDict],
) -> defaultdict[str, dict[str, Any]]:
    """
    Align per-player events to a reference event sequence by nearest timestamp.

    The returned cache is keyed by `epicId`. For each player,
    `closest_events[i]` is the event in `player_events` closest in time to the
    i-th event in `reference_events`.
    """

    target_ts = np.array([e["timestamp"] for e in reference_events])
    sorted_per_player = defaultdict(list)

    for event in player_events:
        sorted_per_player[event["epicId"]].append(event)
    for player_id in sorted_per_player:
        sorted_per_player[player_id].sort(key=lambda event: event["timestamp"])

    cache = defaultdict(dict)
    for player_id, events in sorted_per_player.items():
        event_ts = np.array([event["timestamp"] for event in events])
        indices = np.searchsorted(event_ts, target_ts)
        indices = np.clip(indices, 1, len(event_ts) - 1)

        before = event_ts[indices - 1]
        after = event_ts[indices]

        choose_after = np.abs(after - target_ts) < np.abs(before - target_ts)
        closest_idxs = np.where(choose_after, indices, indices - 1)

        cache[player_id]["timestamps"] = event_ts
        cache[player_id]["closest_indices"] = closest_idxs
        cache[player_id]["closest_events"] = [events[i] for i in closest_idxs]

    return cache
