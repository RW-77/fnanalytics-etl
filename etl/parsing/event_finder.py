import json
from glob import glob
import math
import pandas as pd
from collections import defaultdict
from bisect import bisect_left, bisect_right

from etl.api import osirion_client as osr
from geometry.vec3 import Vec3, normalize, dot
from geometry.ray import Ray
from geometry.sphere import Sphere

time_diffs = []

def get_closest(player_id: str, target_ts: int, player_movements) -> dict:
    """
    Returns the closest movement event to the target timestamp target_ts
    belonging to player_id
    """
    data = player_movements[player_id]
    if not data or not data["events"]:
        print(f"No movement events found for player {player_id}.")
        return {}
    
    timestamps = data["timestamps"]
    events = data["events"]

    idx = bisect_left(timestamps, target_ts)
    
    # Handle edge cases
    if idx == 0:
        return events[0]
    if idx == len(events):
        return events[-1]
    
    # Choose closer of the two neighboring timestamps
    before = events[idx - 1]
    after = events[idx]
    if abs(before["timestamp"] - target_ts) <= abs(after["timestamp"] - target_ts):
        return before
    return after


def get_hit_events():
    global time_diffs
    """
    Returns a dict containing all the shot events, each containing the movement
    event of the actor and recipient occuring closest to the time of the shot
    """

    with open("data/match_movement_events.json", "r") as f:
        movement_events = json.load(f)["events"]
    with open("data/match_shot_events.json", "r") as f:
        shot_events = json.load(f)["hitscanEvents"]

    # preprocessing
    # maps player -> list of movements
    player_movements = defaultdict(list)
    for me in movement_events:
        player_movements[me["epicId"]].append(me)
    for player_id in player_movements:
        player_movements[player_id].sort(key=lambda e: e["timestamp"])

    enriched_shots = []
    for se in shot_events:
        if not se["hitPlayer"]:
            continue
        ts = se["timestamp"]
        actor_id = se["epicId"]
        recipient_id = se["hitEpicId"]

        actor_move_event = get_closest(actor_id, ts, player_movements)
        recipient_move_event = get_closest(recipient_id, ts, player_movements)

        time_diff = abs(actor_move_event["timestamp"] - recipient_move_event["timestamp"])

        time_diffs.append(time_diff)

        enriched_shots.append({
            **se,
            "shooterMovement": actor_move_event,
            "targetMovement": recipient_move_event,
        })

    return enriched_shots


def closest_to_ray(point: Vec3, ray: Ray) -> float:
    origin, direction = ray.origin, ray.dir
    d_len = direction.length()
    if d_len == 0:
        raise ValueError("Ray direction cannot be zero-length")

    dir_norm = direction / d_len
    op = point - origin

    # perpendicular distance from point to the ray line
    proj_len = dot(op, dir_norm)
    if proj_len < 0:
        # point is behind the ray origin
        return op.length()
    closest_point_on_ray = origin + dir_norm * proj_len
    return (point - closest_point_on_ray).length()


def get_hit_attempt_events(shot_events_path: str, movement_events_path: str):
    """
    Returns a list of all shots events which are attempts to hit exposed players
    """

    with open(movement_events_path, "r") as f:
        movement_events = json.load(f)["events"]
    with open(shot_events_path, "r") as f:
        shot_events = json.load(f)["hitscanEvents"]

    hit_attempts = []
    # match_players = get_id_to_name_map("832ceecc424df110d58e3e96d3dff834")
    with open("data/processed/test_teammate_map.json", "r") as f:
        team_player_ids = json.load(f)
        print(len(team_player_ids))

    with open("data/processed/test_teammate_map.json", "w") as f:
        json.dump(team_player_ids, f, indent=2)

    # get lists of movement events for each player, sorted by timestamp
    player_movements = {}
    for me in movement_events:
        pid = me["epicId"]
        player_movements.setdefault(pid, {"events": [], "timestamps": []})
        player_movements[pid]["events"].append(me)
    for pid, data in player_movements.items():
        data["events"].sort(key=lambda e: e["timestamp"])
        data["timestamps"] = [e["timestamp"] for e in data["events"]]

    i = 1
    for se in shot_events:
        print(f"Evaluating shot {i}:")
        i += 1
        ts = se["timestamp"]
        actor_id = se["epicId"]
        # get the closest known position of the actor
        actor_move_event = get_closest(actor_id, ts, player_movements)
        actor_loc = actor_move_event["movementData"]["location"]
        p_actor = Vec3(actor_loc["x"], actor_loc["y"], actor_loc["z"])

        hit_loc = se["location"]
        p_hit = Vec3(hit_loc["x"], hit_loc["y"], hit_loc["z"])

        ts = se["timestamp"]

        if se["hitPlayer"]:
            # get the closest known position of the recipient
            recipient_id = se["hitEpicId"]
            print(f"✅💥 Shot added as hit attempt (hit player {recipient_id})")
            hit_attempts.append({
                **se,
                "intendedRecipient": recipient_id,
                "targetMovement": get_closest(recipient_id, ts, player_movements),
            })
        else: # hits player build, terrain, or map boundary
            # go through all the positions of opponent players at the current time
            target_candidates = []
            for cand_id in player_movements:
                # make sure candidate is not on the same team as actor
                if cand_id in team_player_ids[actor_id]:
                    continue
                candidate_move_event = get_closest(cand_id, ts, player_movements)
                cand_loc = candidate_move_event["movementData"]["location"]
                p_cand = Vec3(cand_loc["x"], cand_loc["y"], cand_loc["z"])

                # bullet ray from actor and bullet hit positions
                v_dir = p_hit - p_actor
                bullet_ray = Ray(p_actor, normalize(v_dir))

                # define spherical bounding box around player (centimeters)
                radius = 200 + 0.5 * ( (p_cand - p_actor).length()/100 )
                player_bb = Sphere(p_cand, radius)

                # check if the bullet vector intersects sphere
                # max range is 250m
                if player_bb.hit(bullet_ray, 25000):
                    target_candidates.append({
                        "cand_id": cand_id,
                        "position": p_cand,
                        "move_event": candidate_move_event
                    })
            # find the closest candidate
            if len(target_candidates) > 0:
                print(f"Comparing {len(target_candidates)} candidates")
                min_dist = math.inf
                closest = target_candidates[0]
                for candidate_info in target_candidates:
                    dist = (p_actor - candidate_info["position"]).length()
                    if dist < min_dist:
                        closest = candidate_info
                        min_dist = dist

                # check if closest candidate appeared before the build (in event where bullet hit build)
                if (not se["hitPlayerBuild"] or
                        ((closest["position"] - p_actor).length()) < (p_hit - p_actor).length()):
                    if not se["hitPlayerBuild"]:
                        print("✅ Shot added as hit attempt (exposed BB)")
                    elif (closest["position"] - p_actor).length() < (p_hit - p_actor).length():
                        print("✅ Shot added as hit attempt (hit build behind exposed BB)")
                    print(f"\tOpponent id: {closest['cand_id']}")
                    print(f"\tOpponent distance: {min_dist}")
                    print(f"\tPassing distance: {closest_to_ray(closest['position'], Ray(p_actor, normalize(p_hit-p_actor)))}")
                    hit_attempts.append({
                        **se,
                        "intendedRecipient": closest["cand_id"],
                        "targetMovement": closest["move_event"],
                    })
                else:
                    print("❌ Potential candidates rejected (build in front of closest candidate)")
            else:
                print("❌ No potential candidates (stray shot)")
    return hit_attempts


if __name__ == '__main__':
    movement_events_path = "data/raw/match_movement_events.json"
    shot_events_path = "data/raw/match_shot_events.json"
    attempts = get_hit_attempt_events(shot_events_path, movement_events_path)
