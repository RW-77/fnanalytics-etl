import os
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from etl.types import RawMatchData
from etl.parsing.common.eligibility import eligible_player_ids


# Where per-match debug reports are written when debug=True.
DCE_REPORT_ROOT = "dce_reports"


def _safe_filename(username: str, epic_id: str) -> str:
    """Build a filesystem-safe, collision-free per-player report name."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", username).strip("_") or "player"
    return f"{base}_{epic_id[:8]}.txt"


# Causal ordering for events that share a timestamp. Lower is processed
# first: the hit that knocks/kills must land before the knock freezes the
# buffer or the elimination flushes it.
_EVENT_PRIORITY = {
    "damage": 0,
    "knock": 1,
    "health": 2,
    "shield": 2,
    "revive": 3,
    "reboot": 3,
    "elimination": 4,
}


def _build_timeline(raw: RawMatchData) -> list[dict]:
    """Merge every event stream this stat needs into one time-ordered list.

    Each item is tagged with a ``type`` so the consumer can branch on it, and
    carries the original event under ``data``. Items are sorted by timestamp,
    then by ``_EVENT_PRIORITY`` so that — for events sharing a timestamp — the
    damage that causes a knock/kill is processed before the knock freezes the
    buffer or the elimination flushes it.

    HP/shield updates additionally carry ``coincident_damage``: True when the
    same player takes a damage hit at the *same microsecond*. Rapid fire stamps
    several hits on one microsecond and the resulting HP/shield updates can
    arrive out of order or include a stale high reading; flagging them lets the
    consumer treat them as a damage progression (never a heal). Updates without
    coincident damage are genuine heal-over-time / regen ticks and stay normal.
    """
    # (victim, timestamp) pairs that take damage this microsecond. Source
    # fireWeaponEvents (not shot_events): identical fields, but the /events/shots
    # endpoint truncates under load whereas fireWeaponEvents (bulk /events) is
    # complete.
    damage_keys = {
        (e["hitEpicId"], e["timestamp"])
        for e in raw.fire_weapon_events
        if e.get("hitPlayer") and e.get("hitEpicId")
    }

    timeline: list[dict] = []

    def add(events, type_tag, *, predicate=None):
        for evt in events:
            if predicate is not None and not predicate(evt):
                continue
            item = {"type": type_tag, "timestamp": evt["timestamp"], "data": evt}
            if type_tag in ("health", "shield"):
                item["coincident_damage"] = (evt["epicId"], evt["timestamp"]) in damage_keys
            timeline.append(item)

    # Only player-damage shots matter; drop misses/harvests up front.
    add(raw.fire_weapon_events, "damage", predicate=lambda e: e.get("hitPlayer"))
    add(raw.knocked_events, "knock")
    add(raw.health_update_events, "health")
    add(raw.shield_update_events, "shield")
    add(raw.revive_events, "revive")
    add(raw.reboot_events, "reboot")
    add(raw.elimination_events, "elimination")

    timeline.sort(key=lambda item: (item["timestamp"], _EVENT_PRIORITY[item["type"]]))
    return timeline


def scale(reg: dict[str, float], H: float) -> None:
    T = sum(reg.values())
    if T <= 0:
        return
    factor = max(0.0, (T - H)/T)
    for dealer in reg:
        reg[dealer] *= factor


def _fmt_reg(reg: dict[str, float], name_of: dict[str, str]) -> dict[str, float]:
    """Render a buffer with usernames and rounded amounts for debug output."""
    return {
        name_of.get(dealer, dealer): round(amt, 1)
        for dealer, amt in reg.items()
        if amt > 0
    }


@dataclass(frozen=True)
class KillContext:
    """One elimination plus the un-healed, overkill-capped damage the killer's
    team had standing on the victim at the moment of death.

    ``team_contributions`` maps each crediting dealer (Epic id) on the killer's
    team to their outstanding damage. Storm / self / non-player deaths yield an
    empty mapping. This is the single output of the replay engine; every
    kill-causal stat is just a different way of reading it.
    """
    timestamp: int
    game_time_seconds: float
    victim_id: str
    killer_id: str
    team_contributions: dict[str, float]


def iter_kill_contributions(raw: RawMatchData, *, debug: bool = False) -> Iterator[KillContext]:
    """Replay the match and *yield* a :class:`KillContext` at each elimination.

    This is a **generator**: calling it does not run the loop immediately, it
    returns a lazy iterator. Each time the caller asks for the next item, the
    function runs up to the next ``yield`` (here, the next elimination), hands
    back that ``KillContext``, and pauses — its local variables (the buffers,
    the per-player HP/shield state) stay alive between yields. That is exactly
    what we want: one continuous replay that emits a result per kill, while the
    callers stay oblivious to all the bookkeeping.

    All the hard-won logic lives here and nowhere else — overkill capping, heal
    detection, the coincident-damage de-noise, knock freezing. A stat consumer
    only decides what to *do* with each ``KillContext``.

    When ``debug=True`` it also accumulates the human-readable trace and writes
    the per-match report once the generator is fully consumed (the code after
    the loop runs when iteration ends).
    """
    eligible = eligible_player_ids(raw)
    team_of = {pid: t["teamId"] for t in raw.teams for pid in t["epicId"]}
    name_of = {p["epicId"]: p["epicUsername"] for p in raw.players}

    match_start = raw.info["aircraftStartTime"]

    hit_reg = defaultdict(lambda: defaultdict(float))
    knocked = defaultdict(bool)

    # Reported HP/shield from the update streams — the source of truth for heal
    # detection (a heal is when a *reported* value rises between updates).
    rep_hp = defaultdict(lambda: 100.0)
    rep_shield = defaultdict(lambda: 0.0)

    # Drained running estimate, used only for the overkill cap. Damage drains it
    # immediately so mid-burst hits cap correctly before the next update lands.
    est_hp = defaultdict(lambda: 100.0)
    est_shield = defaultdict(lambda: 0.0)

    epsilon = 1.0

    n_elims = 0
    n_storm_deaths = 0

    def nm(pid: str) -> str:
        return name_of.get(pid, pid)

    # Debug routing: every emitted line goes to the full stream and to the
    # per-player stream of each player it involves.
    full_lines: list[str] = []
    player_lines: defaultdict[str, list[str]] = defaultdict(list)

    def emit(line: str, *players: str) -> None:
        if not debug:
            return
        full_lines.append(line)
        for pid in players:
            player_lines[pid].append(line)

    for evt in _build_timeline(raw):
        etype, t, d = evt["type"], evt["timestamp"], evt["data"]
        gt = (t - match_start) / 1e6

        if etype == "damage":
            actor = d["epicId"]
            recip = d["hitEpicId"]
            # Both ends must be real match players (matches parse_damage_dealt):
            # damage onto bots/spectators isn't a contribution toward an
            # opponent kill, and every emitted row must map to a MatchPlayer.
            if not knocked[recip] and actor in eligible and recip in eligible:
                # Cap overkill: a dealer is only credited for damage that
                # actually depletes the victim's remaining HP+shield. A 90 hit
                # on a player with 30 left counts as 30, not 90.
                remaining = est_hp[recip] + est_shield[recip]
                applied = min(d["damage"], remaining)
                if applied > 0:
                    hit_reg[recip][actor] += applied
                    # Drain the running estimate (shield absorbs first) so a
                    # follow-up hit landing before the next health update still
                    # caps against the correct remaining health.
                    absorbed = min(applied, est_shield[recip])
                    est_shield[recip] -= absorbed
                    est_hp[recip] -= applied - absorbed
                    note = "" if applied >= d["damage"] else f" [capped from {d['damage']:.1f}]"
                    emit(
                        f"[{gt:7.1f}s] DMG  {nm(actor)} → {nm(recip)} +{applied:.1f}{note} "
                        f"| {nm(recip)} reg={_fmt_reg(hit_reg[recip], name_of)}",
                        actor, recip,
                    )

        elif etype == "knock":
            recip = d["targetId"]
            knocked[recip] = True
            emit(f"[{gt:7.1f}s] KNOCK {nm(recip)} (buffer frozen)", recip)

        elif etype in ("health", "shield"):
            player = d["epicId"]
            value = d["value"]
            rep = rep_hp if etype == "health" else rep_shield
            est = est_hp if etype == "health" else est_shield

            if evt.get("coincident_damage"):
                # This update shares a microsecond with a damage hit, so it is
                # part of a damage progression — never a heal. Take the lowest
                # reading and ignore any stale high one the stream emits at the
                # same instant (order-independent, so timeline order is moot).
                rep[player] = min(rep[player], value)
                est[player] = min(est[player], value)
            else:
                # A heal is a rise vs the previous *reported* value. Comparing
                # to the reported stream (not the drained estimate) means a
                # lagged update confirming earlier damage — a value below the
                # last report, like a victim shown at 13 HP after we already
                # drained them to 0 — reads as a decrease, never a phantom heal.
                is_heal = not knocked[player] and value > rep[player] + epsilon
                if is_heal and hit_reg[player]:
                    scale(hit_reg[player], value - rep[player])
                    emit(
                        f"[{gt:7.1f}s] HEAL {nm(player)} +{value - rep[player]:.1f} ({etype}) "
                        f"| reg→{_fmt_reg(hit_reg[player], name_of)}",
                        player, *hit_reg[player].keys(),
                    )
                rep[player] = value
                # Estimate rises to a confirmed heal; otherwise trust the lower
                # of our drained value and this reading (catches untracked
                # storm/fall damage that has no shot event).
                est[player] = value if is_heal else min(est[player], value)

        elif etype == "revive":
            revived = d["epicId"]
            knocked[revived] = False
            # Treat a revive as a 30 HP heal (revived players come up at ~30).
            scale(hit_reg[revived], 30)
            rep_hp[revived] = est_hp[revived] = 30.0
            rep_shield[revived] = est_shield[revived] = 0.0
            emit(
                f"[{gt:7.1f}s] REVIVE {nm(revived)} "
                f"| reg→{_fmt_reg(hit_reg[revived], name_of)}",
                revived, *hit_reg[revived].keys(),
            )

        elif etype == "reboot":
            rebooted = d["epicId"]
            hit_reg[rebooted].clear()
            knocked[rebooted] = False
            rep_hp[rebooted], rep_shield[rebooted] = 100.0, 0.0
            est_hp[rebooted], est_shield[rebooted] = 100.0, 0.0
            emit(f"[{gt:7.1f}s] REBOOT {nm(rebooted)} (buffer reset)", rebooted)

        elif etype == "elimination":
            killer = d["epicId"]
            recipient = d["targetId"]
            killer_team = team_of.get(killer)
            n_elims += 1

            # Split the victim's buffer into the killer's team (credited) and
            # everyone else (dropped) — the team split is the one rule common
            # to every kill-causal stat, so the engine applies it here.
            team_contributions: dict[str, float] = {}
            dropped = {}
            for dealer, amt in hit_reg[recipient].items():
                if amt <= 0:
                    continue
                if killer_team is not None and team_of.get(dealer) == killer_team:
                    team_contributions[dealer] = amt
                else:
                    dropped[nm(dealer)] = round(amt, 1)

            if killer_team is None:
                n_storm_deaths += 1

            # Everyone who touched this victim should see the resolution.
            involved = {killer, recipient, *hit_reg[recipient].keys()}
            emit(
                f"[{gt:7.1f}s] ELIM {nm(recipient)} ← {nm(killer)} "
                f"| credit={ {nm(p): round(a, 1) for p, a in team_contributions.items()} } "
                f"| dropped={dropped}",
                *involved,
            )

            hit_reg[recipient].clear()
            knocked[recipient] = False

            yield KillContext(
                timestamp=t,
                game_time_seconds=gt,
                victim_id=recipient,
                killer_id=killer,
                team_contributions=team_contributions,
            )

    if debug:
        summary = (
            f"=== {n_elims} eliminations "
            f"({n_storm_deaths} non-player deaths skipped) ==="
        )
        _write_reports(raw.match_id, full_lines, player_lines, name_of, summary)


def parse_damage_contribution_on_elims(raw: RawMatchData) -> list[dict]:
    """Damage-contribution-on-elimination rows: one per (dealer, victim-death),
    carrying the dealer's credited (un-healed, overkill-capped) damage.

    A thin consumer of :func:`iter_kill_contributions` — it keeps every credited
    contribution and records the amount.
    """
    return [
        {
            "match_id": raw.match_id,
            "timestamp": kc.timestamp,
            "game_time_seconds": kc.game_time_seconds,
            "actor_id": dealer,
            "victim_id": kc.victim_id,
            "killer_id": kc.killer_id,
            "amount": amount,
        }
        for kc in iter_kill_contributions(raw)
        for dealer, amount in kc.team_contributions.items()
    ]


def parse_assists(raw: RawMatchData) -> list[dict]:
    """Assist rows: one per (assister, victim-death). An assist is any teammate
    who dealt un-healed damage to an opponent their team then eliminated,
    excluding the finisher themselves. The amount is irrelevant — only that it
    was non-zero, which ``team_contributions`` already guarantees.
    """
    return [
        {
            "match_id": raw.match_id,
            "timestamp": kc.timestamp,
            "game_time_seconds": kc.game_time_seconds,
            "actor_id": dealer,
            "victim_id": kc.victim_id,
            "killer_id": kc.killer_id,
        }
        for kc in iter_kill_contributions(raw)
        for dealer in kc.team_contributions
        if dealer != kc.killer_id
    ]


def _write_reports(
    match_id: str,
    full_lines: list[str],
    player_lines: dict[str, list[str]],
    name_of: dict[str, str],
    summary: str,
) -> None:
    """Write the full event stream and one filtered file per player."""
    out_dir = os.path.join(DCE_REPORT_ROOT, match_id)
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "_full.txt"), "w") as f:
        f.write("\n".join(full_lines))
        f.write(f"\n\n{summary}\n")

    for pid, lines in player_lines.items():
        fname = _safe_filename(name_of.get(pid, pid), pid)
        with open(os.path.join(out_dir, fname), "w") as f:
            f.write("\n".join(lines))
            f.write("\n")

    print(f"{summary}\nWrote {len(player_lines) + 1} files to {out_dir}/")


if __name__ == "__main__":
    import sys
    from etl.fetching.match_data_fetching import ensure_match_raw

    match_id = sys.argv[1] if len(sys.argv) > 1 else "9a7e9e011f76c7995cdf4ba4736f7ff6"
    raw = ensure_match_raw(match_id)
    # Fully consume the generator so the debug report is written, and report
    # the two derived stats side by side.
    list(iter_kill_contributions(raw, debug=True))
    print(f"DCE rows:     {len(parse_damage_contribution_on_elims(raw))}")
    print(f"Assist rows:  {len(parse_assists(raw))}")
