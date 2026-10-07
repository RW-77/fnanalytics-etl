"""
Score engagement detection against hand labels.

Usage:
    python scripts/score_engagements.py <match_id> [--labels-dir DIR]

Reads <labels-dir>/<match_id>.md, one fight per line:
    start end  name | name | name@joined  [x|?]  (note)
    x = not a fight (false positive), ? = borderline (left out of the counts).
One player name per team; @mm:ss marks a team that joined late. Lines that
don't start with two timestamps continue the previous line's note.

Matching, per label:
    real fight      kept when a detected engagement overlaps its window and has
                    the first two labeled teams as core teams.
    false positive  wrongly kept when a detected engagement overlaps its window
                    and has two of its labeled teams as core teams.
Also lists late-joining teams (@) as core / periphery / absent, and detections
that no label covers (new fights to review in the replay).
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from etl.fetching.match_data_fetching import get_raw_match_data
from etl.parsing.match.context import MatchContext
from etl.parsing.match.clustering.engagements import parse_engagements, SegmentParams


DEFAULT_LABELS_DIR = os.path.join(os.path.dirname(__file__), "..", "labels")
LINE = re.compile(r"^(\d+:\d\d)\s+(\d+:\d\d)\s+(.*)$")


def norm(name: str) -> str:
    """Usernames compared loosely: '!' and 'ǃ' match, case and spacing ignored."""
    return re.sub(r"\s+", " ", name.replace("ǃ", "!").strip().lower())


def secs(clock: str) -> int:
    m, s = clock.split(":")
    return int(m) * 60 + int(s)


def mmss(t_s: float) -> str:
    t = max(0, int(t_s))
    return f"{t // 60}:{t % 60:02d}"


def parse_labels(path: str, team_by_name: dict[str, int]) -> list[dict]:
    labels: list[dict] = []
    for raw_line in open(path, encoding="utf-8"):
        line = raw_line.rstrip("\n")
        m = LINE.match(line)
        if not m:
            if labels and line.strip() and not line.startswith("match:"):
                labels[-1]["note"] += " " + line.strip()
            continue
        start, end, rest = m.groups()
        note = ""
        if "(" in rest:
            rest, note = rest.split("(", 1)
        rest = rest.strip()
        verdict = "real"
        if re.search(r"\s[xX]$", " " + rest):
            verdict, rest = "fp", rest[:-1].strip()
        elif rest.endswith("?"):
            verdict, rest = "borderline", rest[:-1].strip()
        teams, joined = [], []
        for token in rest.split("|"):
            name, _, join = token.strip().partition("@")
            team = team_by_name.get(norm(name))
            if team is None:
                continue
            teams.append(team)
            if join:
                joined.append(team)
        key = (start, end, tuple(teams))
        if labels and any((l["start"], l["end"], tuple(l["teams"])) == key for l in labels):
            continue  # the same fight listed twice (e.g. to note a merge)
        labels.append({
            "start": start, "end": end, "lo": secs(start), "hi": secs(end),
            "teams": teams, "joined": joined, "verdict": verdict,
            "note": note.rstrip(") ").strip(),
        })
    return labels


def score(match_id: str, labels_dir: str, params: SegmentParams) -> None:
    raw = get_raw_match_data(match_id)
    ctx = MatchContext(raw)
    team_by_name = {
        norm(p.get("epicUsername") or ""): ctx.team_of[p["epicId"]]
        for p in raw.players if p["epicId"] in ctx.team_of
    }
    labels = parse_labels(os.path.join(labels_dir, f"{match_id}.md"), team_by_name)
    engagements = [r.engagement for r in parse_engagements(ctx, params=params)]

    def overlapping(label: dict) -> list:
        return [e for e in engagements if e.start_s <= label["hi"] + 1 and label["lo"] - 1 <= e.end_s]

    real = [l for l in labels if l["verdict"] == "real" and len(l["teams"]) >= 2]
    fps = [l for l in labels if l["verdict"] == "fp" and len(l["teams"]) >= 2]
    covered: set[int] = set()

    missed = []
    for l in real:
        hits = [e for e in overlapping(l) if set(l["teams"][:2]) <= e.teams]
        covered.update(id(e) for e in hits)
        if not hits:
            missed.append(l)
    kept_fps = []
    for l in fps:
        hits = [e for e in overlapping(l) if len(set(l["teams"]) & e.teams) >= 2]
        covered.update(id(e) for e in hits)
        if hits:
            kept_fps.append(l)

    kept = len(real) - len(missed)
    print(f"match {match_id}: {len(engagements)} engagements detected, {len(labels)} labels")
    print(f"  real fights kept      {kept} / {len(real)}")
    print(f"  false positives kept  {len(kept_fps)} / {len(fps)}")
    if kept + len(kept_fps):
        print(f"  precision on labeled  {kept / (kept + len(kept_fps)):.0%}")

    print("\nmissed real fights:")
    for l in missed:
        print(f"  {l['start']}-{l['end']}  teams {l['teams']}  {l['note'][:70]}")
    print("\nfalse positives kept:")
    for l in kept_fps:
        print(f"  {l['start']}-{l['end']}  teams {l['teams']}  {l['note'][:70]}")

    print("\nlate joiners (@) in real fights:")
    for l in real:
        for team in l["joined"]:
            where = "absent"
            for e in overlapping(l):
                if team in e.teams:
                    where = "core"
                    break
                if any(team in (r.actor_team, r.recipient_team) for r in e.periphery):
                    where = "periphery"
            print(f"  {l['start']}-{l['end']}  team {team}: {where}  {l['note'][:60]}")

    unlabeled = [e for e in engagements if id(e) not in covered]
    print(f"\ndetections no label covers ({len(unlabeled)}), review in the replay:")
    for e in unlabeled:
        print(f"  {mmss(e.start_s)}-{mmss(e.end_s)}  teams {sorted(e.teams)}")


def main():
    parser = argparse.ArgumentParser(description="Score engagement detection against hand labels.")
    parser.add_argument("match_id", help="Match ID (labels/<match_id>.md must exist)")
    parser.add_argument("--labels-dir", default=DEFAULT_LABELS_DIR, metavar="DIR")
    args = parser.parse_args()
    score(args.match_id, args.labels_dir, SegmentParams())


if __name__ == "__main__":
    main()
