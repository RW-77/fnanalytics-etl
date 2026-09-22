import math

from etl.types import RawMatchData
from etl.parsing.common.indexing import PlayerPositionIndex
from etl.parsing.common.eligibility import eligible_player_ids


MAX_GAP_US = 600_000      # 600 ms — max staleness for a trusted position
MAX_RANGE_CM = 25_000.0   # 250 m — max bullet travel we consider


def _is_ranged_weapon(weapon_id: str) -> bool:
    """Exclude melee from ranged hit-attempt analysis. The pickaxe is the only
    melee weapon; a pickaxe swing that 'hits' terrain near an opponent is not a
    ranged attempt on them."""
    return "pickaxe" not in (weapon_id or "").lower()


def _ray_sphere_entry(origin, direction, center, radius, max_t):
    """Distance along a UNIT-direction ray to where it first enters a sphere,
    or None if it never enters within ``max_t``.

    ``direction`` must be unit length, so the quadratic's ``a`` term is 1 and
    the returned value is a distance in the ray's units (centimeters here).
    """
    ocx = origin[0] - center[0]
    ocy = origin[1] - center[1]
    ocz = origin[2] - center[2]
    b = ocx * direction[0] + ocy * direction[1] + ocz * direction[2]  # half-b
    c = ocx * ocx + ocy * ocy + ocz * ocz - radius * radius
    disc = b * b - c
    if disc < 0.0:
        return None
    sq = math.sqrt(disc)
    t = -b - sq # near intersection
    if t <= 0.0:
        t = -b + sq # origin inside the sphere -> use far root
        if t <= 0.0:
            return None
    return t if t <= max_t else None


# todo: standardize return types
def parse_shot_attempts(
    raw: RawMatchData,
    *,
    max_gap: int = MAX_GAP_US,
    max_range: float = MAX_RANGE_CM,
) -> list[dict]:
    """
    Identify shots that were attempts to hit an exposed opponent.

    - A shot that hit a player is an attempt on that player (a direct hit).
    - A shot that hit a build / terrain / nothing is an attempt on the closest
      opponent whose distance-scaled hitbox sphere the bullet's path passes
      through *in front of* whatever stopped the bullet. Opponents behind the
      impact point were occluded (no line of sight) and don't count.
    - Shots with no such opponent are strays and are dropped.

    Positions come from :class:`PlayerPositionIndex`, so eliminated players and
    candidates without a trustworthy position at the shot time are skipped.
    Knocked (DBNO) players are still valid targets — they remain "alive" in the
    index until their actual elimination.
    """
    index = PlayerPositionIndex(raw)
    team_of = {pid: t["teamId"] for t in raw.teams for pid in t["epicId"]}
    eligible = eligible_player_ids(raw)
    match_start = raw.info["aircraftStartTime"]

    attempts: list[dict] = []

    # Source fireWeaponEvents, not shot_events: identical fields, but the
    # /events/shots endpoint truncates under load whereas fireWeaponEvents
    # (bulk /events) is complete.
    for se in raw.fire_weapon_events:
        actor_id = se["epicId"]
        if actor_id not in eligible:
            continue
        weapon_id = se.get("weaponId", "")
        if not _is_ranged_weapon(weapon_id):
            continue  # melee (pickaxe) is not a ranged hit attempt
        ts = se["timestamp"]
        game_time_seconds = (ts - match_start) / 1e6
        actor_team = team_of.get(actor_id)

        # --- Direct hit: recipient is known, no geometry needed. ---
        if se["hitPlayer"]:
            recipient_id = se["hitEpicId"]
            if recipient_id in eligible and team_of.get(recipient_id) != actor_team:
                actor_pos = index.position_at(ts, actor_id, max_gap)
                ap = actor_pos.location if actor_pos else None
                attempts.append({
                    "match_id": raw.match_id,
                    "timestamp": ts,
                    "game_time_seconds": game_time_seconds,
                    "actor_id": actor_id,
                    "recipient_id": recipient_id,
                    "weapon_id": weapon_id,
                    "direct_hit": True,
                    "passing_distance": 0.0,
                    "ax": ap[0] if ap else None,
                    "ay": ap[1] if ap else None,
                    "az": ap[2] if ap else None,
                    "rx": se["location"]["x"],
                    "ry": se["location"]["y"],
                    "rz": se["location"]["z"],
                })
            continue

        # --- Missed shot: ray-test exposed opponents in front of the impact. ---
        actor_pos = index.position_at(ts, actor_id, max_gap)
        if actor_pos is None:
            continue  # can't locate the shooter -> can't reason about the ray
        p_actor = actor_pos.location

        loc = se["location"]
        p_hit = (loc["x"], loc["y"], loc["z"])
        vx = p_hit[0] - p_actor[0]
        vy = p_hit[1] - p_actor[1]
        vz = p_hit[2] - p_actor[2]
        dist_to_hit = math.sqrt(vx * vx + vy * vy + vz * vz)
        if dist_to_hit == 0.0:
            continue
        direction = (vx / dist_to_hit, vy / dist_to_hit, vz / dist_to_hit)

        best_id, best_pos = None, None
        best_dist = math.inf
        best_passing = None

        for cand_id in eligible:
            if cand_id == actor_id or team_of.get(cand_id) == actor_team:
                continue
            cand_pos = index.position_at(ts, cand_id, max_gap)
            if cand_pos is None:
                continue  # eliminated or stale position -> not a valid target
            p_cand = cand_pos.location

            ocx = p_cand[0] - p_actor[0]
            ocy = p_cand[1] - p_actor[1]
            ocz = p_cand[2] - p_actor[2]
            dist_to_cand = math.sqrt(ocx * ocx + ocy * ocy + ocz * ocz)

            # Occlusion: the opponent must be in front of whatever stopped the
            # bullet (build OR terrain OR boundary) to have been reachable.
            if dist_to_cand >= dist_to_hit:
                continue

            # Distance-scaled hitbox: larger for far targets (harder to hit).
            radius = 200.0 + 0.5 * (dist_to_cand / 100.0)
            if _ray_sphere_entry(p_actor, direction, p_cand, radius, max_range) is None:
                continue  # the bullet's path missed this player's hitbox

            # Intended recipient = closest such opponent to the shooter.
            if dist_to_cand < best_dist:
                proj = ocx * direction[0] + ocy * direction[1] + ocz * direction[2]
                best_id = cand_id
                best_dist = dist_to_cand
                best_pos = p_cand
                # perpendicular distance from target center to the ray line:
                # a "how centered was the shot" quality measure.
                best_passing = math.sqrt(max(0.0, dist_to_cand * dist_to_cand - proj * proj))

        if best_id is not None:

            attempts.append({
                "match_id": raw.match_id,
                "timestamp": ts,
                "game_time_seconds": game_time_seconds,
                "actor_id": actor_id,
                "recipient_id": best_id,
                "weapon_id": weapon_id,
                "direct_hit": False,
                "passing_distance": best_passing,
                "ax": p_actor[0],
                "ay": p_actor[1],
                "az": p_actor[2],
                "rx": best_pos[0],
                "ry": best_pos[1],
                "rz": best_pos[2],
            })

    return attempts
