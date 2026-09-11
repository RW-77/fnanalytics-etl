import json
from pprint import pprint
import cProfile
import math
import pandas as pd
import numpy as np
from glob import glob
from collections import defaultdict
from bisect import bisect_left, bisect_right
import matplotlib.pyplot as plt

from etl.api import osirion_client as osr
from geometry.vec3 import Vec3, normalize, dot
from geometry.ray import Ray
from geometry.sphere import Sphere


def get_hit_attempt_events(shot_events_path: str, movement_events_path: str):
    """
    Returns a list of all shots events which are attempts to hit exposed players
    """

    with open(movement_events_path, "r") as f:
        movement_events = json.load(f)["events"]
    with open(shot_events_path, "r") as f:
        shot_events = json.load(f)["hitscanEvents"]
    with open("data/processed/test_teammate_map.json", "r") as f:
        team_player_ids = json.load(f)
        print(f"Found {len(team_player_ids)} in match.")

    hit_attempts = []

    # target timestamps
    target_ts = np.sort(np.array([se["timestamp"] for se in shot_events]), kind="stable")

    sorted_movements_by_player = defaultdict(list)
    for me in movement_events:
        sorted_movements_by_player[me["epicId"]].append(me)
    for player_id in sorted_movements_by_player:
        sorted_movements_by_player[player_id].sort(key=lambda e: e["timestamp"])
    
    print(len(sorted_movements_by_player))

    pos_cache = defaultdict(dict)
    for player_id, movement_events in sorted_movements_by_player.items():
        movement_ts = np.array([me["timestamp"] for me in movement_events])
        indices = np.searchsorted(movement_ts, target_ts)
        indices = np.clip(indices, 1, len(movement_ts)-1)

        before = movement_ts[indices - 1]
        after = movement_ts[indices]

        # pick the closer shot (in time)
        choose_after = np.abs(after - target_ts) < np.abs(before - target_ts)
        closest_idxs = np.where(choose_after, indices, indices - 1)

        # Extract the movement events
        closest_movements = [movement_events[i] for i in closest_idxs]

        # Store results
        pos_cache[player_id]["timestamps"] = movement_ts
        pos_cache[player_id]["closest_indices"] = closest_idxs
        pos_cache[player_id]["closest_events"] = closest_movements


    shot_events.sort(key=lambda e: e["timestamp"])
    counter = 0
    for i, se in enumerate(shot_events):
        if se["hitPlayer"]:
            counter += 1
        print(f"Evaluating shot {i}:")
        t = se["timestamp"]

        # get position of the actor (at the time)
        actor_move_event = pos_cache[se["epicId"]]["closest_events"][i]
        actor_loc = actor_move_event["movementData"]["location"]
        p_actor = np.array([actor_loc["x"], actor_loc["y"], actor_loc["z"]]) # broadcasted

        # get ending position of the shot
        p_hit = se["location"]
        p_hit = np.array([p_hit["x"], p_hit["y"], p_hit["z"]])

        # distance from actor to hit
        dist_to_hit = np.linalg.norm(p_hit - p_actor)

        # get positions of all candidates (at the time)
        cand_ids = [
            player_id
            for player_id in pos_cache.keys()
            if player_id not in team_player_ids[se["epicId"]]
        ]
        p_cands = np.array([
            [
                pos_cache[player_id]["closest_events"][i]["movementData"]["location"]["x"],
                pos_cache[player_id]["closest_events"][i]["movementData"]["location"]["y"],
                pos_cache[player_id]["closest_events"][i]["movementData"]["location"]["z"]
            ]
            for player_id in cand_ids
        ], dtype=np.float32)

        # print(f"number of candidates for shot {i}: {len(p_cands)}")
        # print(p_cands)

        v_dir = p_hit - p_actor
        v_dir = v_dir / np.linalg.norm(v_dir)
        
        v_ac = p_cands - p_actor # (N, 3)
        dist_to_cands = np.linalg.norm(v_ac, axis=1) # (N,)
        radii = 200 + 0.5 * (dist_to_cands / 100.0) # (N,)

        # print(radii)

        OC = p_actor - p_cands  # (N, 3)
        b = 2 * np.einsum('ij,j->i', OC, v_dir)
        c = np.einsum('ij,ij->i', OC, OC) - radii**2
        discriminant = b**2 - 4 * c  # (N,)

        hit_mask = discriminant >= 0
        # print(hit_mask)

        if np.any(hit_mask):
            sqrt_disc = np.sqrt(discriminant[hit_mask])
            t1 = (-b[hit_mask] - sqrt_disc) / 2
            t2 = (-b[hit_mask] + sqrt_disc) / 2
            tmin = np.minimum(t1, t2)
            hit_mask[hit_mask] = (tmin > 0) & (tmin < 25000)

            hit_indices = np.nonzero(hit_mask)[0]
            if len(hit_indices) > 0:
                # Compute all distances from actor to each hit candidate
                cand_distances = np.linalg.norm(p_cands[hit_indices] - p_actor, axis=1)
                
                # Get the index of the closest candidate
                min_idx = np.argmin(cand_distances)
                closest_idx = hit_indices[min_idx]
                
                # Pull data for this closest candidate
                closest_cand_id = cand_ids[closest_idx]
                closest_position = p_cands[closest_idx]
                closest_event = pos_cache[closest_cand_id]["closest_events"][i]

                # Distance to the shot's end point
                dist_to_build = np.linalg.norm(p_hit - p_actor)
                
                # Condition: only count it if no build was hit, or the player was closer than the build
                if (not se["hitPlayerBuild"]) or (cand_distances[min_idx] < dist_to_build):
                    hit_attempts.append({
                        **se,
                        "intendedRecipient": closest_cand_id,
                        "targetMovement": closest_event,
                    })
                    print(f"✅ Shot {i} added as hit attempt (exposed BB)")
                else:
                    print("❌ No potential candidates (stray shot)")
            else:
                print("❌ No potential candidates (stray shot)")

    print(len(hit_attempts))
    print(f"number of hits: {counter}")
    return hit_attempts





if __name__ == '__main__':
    movement_events_path = "data/raw/match_movement_events.json"
    shot_events_path = "data/raw/match_shot_events.json"
    # attempts = get_hit_attempt_events(shot_events_path, movement_events_path)
    res = get_hit_attempt_events(shot_events_path, movement_events_path)

    # cProfile.run('print(get_hit_attempt_events(shot_events_path, movement_events_path))', sort='tottime')
