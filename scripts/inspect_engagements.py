"""
Run the engagement-detection algorithm on one or more matches and write
human-readable reports for in-replay validation.

Usage:
    python scripts/inspect_engagements.py <match_id> [<match_id> ...]
                                          [--output-dir DIR]

For each match it writes, under <output-dir>/<match_id>/:
    joined.txt       — every engagement in one chronological log: a scrub INDEX
                       up top, then full record-by-record detail per engagement.
    team_<id>.txt    — one file PER team, containing only that team's engagements
                       (same detail, that team marked with '*'), so you can follow
                       a single team through its fights.

Engagement numbers (#N) are the chronological index and are identical across
joined.txt and every team_<id>.txt, so a fight found in a team file can be looked
up in the joined log. Game time (mm:ss) is seconds since aircraftStartTime, i.e.
InteractionRecord.t_s, matching the replay scrub bar once aligned on bus launch.

Large engagements (more records than FULL_TIMELINE_MAX) print decisive events
(knocks/elims) only, to keep the endgame blob readable.
"""

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from etl.fetching.match_data_fetching import ensure_match_raw
from etl.parsing.match.context import MatchContext
from etl.parsing.match.clustering.engagements import parse_engagements, SegmentParams


DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "engagement_reports"
)

# Fixed-width tags so columns line up in a monospace viewer.
KIND_TAG = {
    "shot_attempt": "shot ",
    "hit": "HIT  ",
    "knock": "KNOCK",
    "elim": "ELIM ",
}

# Engagements with more records than this print decisive events only.
FULL_TIMELINE_MAX = 60


def mmss(t_s: float) -> str:
    """Game-time seconds -> mm:ss.d (negative clamps to 0)."""
    if t_s < 0:
        t_s = 0.0
    m, s = divmod(t_s, 60)
    return f"{int(m):02d}:{s:04.1f}"


def name_map(raw) -> dict[str, str]:
    """epicId -> epicUsername (falls back to a short epicId slice)."""
    return {p["epicId"]: (p.get("epicUsername") or p["epicId"][:8]) for p in raw.players}


def who(pid: str, team: int, names: dict[str, str]) -> str:
    return f"{names.get(pid, pid[:8])}({team})"


def counts(engagement) -> dict[str, int]:
    c = {"shot_attempt": 0, "hit": 0, "knock": 0, "elim": 0}
    for r in engagement.records:
        if r.kind in c:
            c[r.kind] += 1
    return c


def slash_counts(c: dict[str, int]) -> str:
    return f"{c['shot_attempt']}/{c['hit']}/{c['knock']}/{c['elim']}"


def index_line(num: int, e, params, focus_team: int | None = None) -> str:
    """One compact line for the scrub INDEX at the top of a report."""
    c = counts(e)
    if focus_team is None:
        scope = f"{len(e.teams)}t {len(e.participants)}p"
    else:
        scope = "vs " + ",".join(str(t) for t in sorted(e.teams) if t != focus_team)
    return (
        f"#{num:<3d} {mmss(e.start_s)}->{mmss(e.end_s)} {e.duration:5.1f}s  "
        f"{scope:<20s} s/h/k/e {slash_counts(c)}  AI={e.activity:.1f}"
    )


def render_engagement(num: int, e, params, names, focus_team: int | None = None) -> list[str]:
    """Full detail block for one engagement, shared by joined and per-team files."""
    c = counts(e)
    lines = ["-" * 72]
    lines.append(
        f"#{num:<3d} {mmss(e.start_s)} -> {mmss(e.end_s)}  (dur {e.duration:5.1f}s)   "
        f"activity={e.activity:.1f}"
    )
    lines.append(
        f"     teams={sorted(e.teams)}  ({len(e.teams)} teams, {len(e.participants)} players)   "
        f"{c['shot_attempt']} shot, {c['hit']} hit, {c['knock']} knock, {c['elim']} elim"
    )

    # Players grouped by team, with what each team dealt/took (decisive only).
    by_team: dict[int, list[str]] = {}
    for p in e.participants.values():
        by_team.setdefault(p.team_id, []).append(names.get(p.player_id, p.player_id[:8]))
    for t in sorted(by_team):
        mark = "*" if t == focus_team else " "
        dk = sum(1 for r in e.records if r.kind == "knock" and r.actor_team == t)
        de = sum(1 for r in e.records if r.kind == "elim" and r.actor_team == t)
        tk = sum(1 for r in e.records if r.kind == "knock" and r.recipient_team == t)
        te = sum(1 for r in e.records if r.kind == "elim" and r.recipient_team == t)
        lines.append(
            f"   {mark}[team {t}] {', '.join(sorted(by_team[t]))}"
            f"   (dealt {dk}K/{de}E, took {tk}K/{te}E)"
        )

    # Timeline (capped for very large engagements).
    if len(e.records) <= FULL_TIMELINE_MAX:
        shown = e.records
        lines.append("     timeline:")
    else:
        shown = [r for r in e.records if r.kind in ("knock", "elim")]
        lines.append(
            f"     timeline ({c['shot_attempt']} shots + {c['hit']} hits omitted, "
            f"decisive events only):"
        )
    for r in shown:
        tag = KIND_TAG.get(r.kind, r.kind)
        dmg = f"  dmg {r.damage:.0f}" if r.damage else ""
        wpn = f"  [{r.weapon_id}]" if r.weapon_id else ""
        lines.append(
            f"       {mmss(r.t_s)}  {tag}  "
            f"{who(r.actor_id, r.actor_team, names):>22s} -> "
            f"{who(r.recipient_id, r.recipient_team, names):<22s}{dmg}{wpn}"
        )
    lines.append("")
    return lines


def write_report(path, title_lines, index_engs, detail_engs, numof, params, names, focus_team=None):
    """Write one report file: banner + scrub INDEX + full detail blocks."""
    lines = ["=" * 72, *title_lines, "=" * 72, "", "INDEX:"]
    for e in index_engs:
        lines.append("  " + index_line(numof[id(e)], e, params, focus_team))
    lines.append("")
    for e in detail_engs:
        lines.extend(render_engagement(numof[id(e)], e, params, names, focus_team))
    with open(path, "w") as f:
        f.write("\n".join(lines))


def inspect(match_id: str, output_dir: str, params: SegmentParams) -> None:
    print(f"[{match_id}] fetching raw match data …")
    raw = ensure_match_raw(match_id)
    ctx = MatchContext(raw)
    names = name_map(raw)

    print(f"[{match_id}] running parse_engagements …")
    # parse_engagements returns EngagementResults; this report only needs the
    # engagements themselves (it prints records, not evaluations).
    engagements = [r.engagement for r in parse_engagements(ctx, params=params)]
    ordered = sorted(engagements, key=lambda e: e.start_s)
    numof = {id(e): i for i, e in enumerate(ordered)}  # chronological #, shared across files

    match_dir = os.path.join(output_dir, match_id)
    os.makedirs(match_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(match_dir, "team_*.txt")):
        os.remove(stale)  # teams can change between runs; drop old per-team files

    total_records = sum(len(e.records) for e in ordered)
    write_report(
        os.path.join(match_dir, "joined.txt"),
        [
            f"JOINED ENGAGEMENT LOG  —  match {match_id}",
            f"params: d0_cm={params.d0_cm}  tau_s={params.tau_s}  start_theta={params.start_theta}  "
            f"join_theta={params.join_theta}  contact_cm={params.contact_cm}  sustain_cm={params.sustain_cm}",
            f"{len(ordered)} engagements  |  {total_records} records  |  "
            f"{len(raw.players)} players in match",
        ],
        ordered, ordered, numof, params, names,
    )

    per_team: dict[int, list] = {}
    for e in ordered:
        for t in e.teams:
            per_team.setdefault(t, []).append(e)
    for t in sorted(per_team):
        engs = per_team[t]
        roster = sorted({
            names.get(p.player_id, p.player_id[:8])
            for e in engs for p in e.participants.values() if p.team_id == t
        })
        write_report(
            os.path.join(match_dir, f"team_{t:03d}.txt"),
            [
                f"TEAM {t}  —  match {match_id}",
                f"roster: {', '.join(roster)}",
                f"{len(engs)} engagements (this team marked '*')",
            ],
            engs, engs, numof, params, names, focus_team=t,
        )

    print(
        f"[{match_id}] {len(ordered)} engagements, {total_records} records, "
        f"{len(per_team)} team files\n"
        f"           -> {os.path.relpath(match_dir)}/  (joined.txt + team_*.txt)"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run engagement detection and write per-match + per-team reports."
    )
    parser.add_argument("match_ids", nargs="+", help="Match ID(s) to inspect")
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help="Directory for reports (default: <repo>/engagement_reports)",
    )
    args = parser.parse_args()

    params = SegmentParams()
    for mid in args.match_ids:
        inspect(mid, args.output_dir, params)


if __name__ == "__main__":
    main()
