import numpy as np

from etl.types import RawMatchData

TIMELINE_HZ = 30

# Column indices for the per-player state vector in each frame (shape: [N_players, 8])
_X, _Y, _Z, _YAW, _HP, _SHIELD, _ALIVE, _KNOCKED = range(8)


def parse_match_frames(
    raw: RawMatchData,
    hz: int = TIMELINE_HZ,
) -> tuple[dict[str, int], list[np.ndarray]]:
    player_index = _build_player_index(raw)
    frames, movement_keyframes = _simulate_frames(raw, player_index, hz)
    _interpolate_movement(frames, movement_keyframes)
    return player_index, frames


def _build_player_index(raw: RawMatchData) -> dict[str, int]:
    active = sorted(
        (
            p for p in raw.players
            if not p.get("isBot", False)
            and not p.get("isSpectator", False)
            # and not p["epicUsername"].startswith("BLAST_")
            # and not p["epicUsername"].startswith("OBS_")
        ),
        key=lambda p: p["epicId"],
    )
    return {p["epicId"]: i for i, p in enumerate(active)}


def _build_event_list(raw: RawMatchData) -> list[dict]:
    typed = [
        ("movement",      raw.movement_events),
        ("knock",         raw.knocked_events),
        ("elimination",   raw.elimination_events),
        ("health_update", raw.health_update_events),
        ("shield_update", raw.shield_update_events),
        ("revive",        raw.revive_events),
        ("reboot",        raw.reboot_events),
    ]
    events = [
        {"type": t, "timestamp": evt["timestamp"], "data": evt}
        for t, src in typed
        for evt in src
    ]
    events.sort(key=lambda e: e["timestamp"])
    return events


def _simulate_frames(
    raw: RawMatchData,
    player_index: dict[str, int],
    hz: int,
) -> tuple[list[np.ndarray], dict[int, list[tuple[int, np.ndarray]]]]:
    N = len(player_index)
    t_0 = raw.info["aircraftStartTime"]  # microseconds
    dt = 1.0 / hz

    state = np.zeros((N, 8), dtype=np.float32)
    state[:, _HP] = 100.0
    state[:, _ALIVE] = 1.0

    frames: list[np.ndarray] = []
    movement_keyframes: dict[int, list[tuple[int, np.ndarray]]] = {}
    next_t = 0.0

    for evt in _build_event_list(raw):
        timestamp = (evt["timestamp"] - t_0) * 1e-6  # microseconds -> seconds
        data = evt["data"]
        evt_type = evt["type"]

        player_id = data.get("epicId")
        idx = player_index.get(player_id)

        # flush frames up to this event's timestamp
        while next_t <= timestamp:
            frames.append(state.copy())
            next_t += dt

        target_id = data.get("targetId")
        target_idx = player_index.get(target_id) if target_id else None

        match evt_type:
            case "movement":
                if idx is None:
                    continue
                loc = data["movementData"]["location"]
                state[idx, _X] = loc["x"]
                state[idx, _Y] = loc["y"]
                state[idx, _Z] = loc["z"]
                state[idx, _YAW] = data["movementData"]["rotationYaw"]
                snapshot = state[idx, :4].copy()
                kf = movement_keyframes.setdefault(idx, [])
                if kf and kf[-1][0] == len(frames):
                    kf[-1] = (len(frames), snapshot)
                else:
                    kf.append((len(frames), snapshot))
            case "knock":
                if target_idx is not None:
                    state[target_idx, _KNOCKED] = 1.0
            case "elimination":
                if target_idx is not None:
                    state[target_idx, _ALIVE] = 0.0
                    state[target_idx, _KNOCKED] = 0.0
                    state[target_idx, _HP] = 0.0
                    state[target_idx, _SHIELD] = 0.0
            case "health_update":
                if idx is not None:
                    state[idx, _HP] = data["value"]
            case "shield_update":
                if idx is not None:
                    state[idx, _SHIELD] = data["value"]
            case "revive" | "reboot":
                if idx is not None:
                    state[idx, _ALIVE] = 1.0
                    state[idx, _KNOCKED] = 0.0

    return frames, movement_keyframes


def _interpolate_yaw(start: float, end: float, alpha: float) -> float:
    delta = ((end - start + 180.0) % 360.0) - 180.0
    return (start + delta * alpha) % 360.0


def _interpolate_movement(
    frames: list[np.ndarray],
    movement_keyframes: dict[int, list[tuple[int, np.ndarray]]],
) -> None:
    num_frames = len(frames)
    if num_frames == 0:
        return

    for player_idx, keyframes in movement_keyframes.items():
        visible = [(fi, v) for fi, v in keyframes if fi < num_frames]
        for (start_f, start_v), (end_f, end_v) in zip(visible, visible[1:]):
            gap = end_f - start_f
            if gap <= 1:
                continue
            # Do not synthesize movement across elimination gaps.
            if any(frames[fi][player_idx, _ALIVE] == 0.0 for fi in range(start_f, end_f + 1)):
                continue
            for fi in range(start_f, end_f + 1):
                alpha = (fi - start_f) / gap
                frames[fi][player_idx, :3] = start_v[:3] + (end_v[:3] - start_v[:3]) * alpha
                frames[fi][player_idx, _YAW] = _interpolate_yaw(
                    float(start_v[_YAW]), float(end_v[_YAW]), alpha
                )
