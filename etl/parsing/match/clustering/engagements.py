import math
from typing import Literal
from collections import defaultdict
from dataclasses import dataclass, field, asdict

from etl.types import RawMatchData
from etl.parsing.match.context import MatchContext
from etl.parsing.common.zones import sorted_zone_events, zone_for_timestamp


@dataclass(frozen=True)
class InteractionRecord:
    ts: int          # raw microseconds since epoch (for PlayerPositionIndex + ordering)
    t_s: float       # seconds since aircraftStartTime
    kind: str
    actor_id: str
    recipient_id: str
    actor_team: int
    recipient_team: int
    ax: float; ay: float; az: float
    rx: float; ry: float; rz: float
    weapon_id: str | None = None
    damage: float = 0.0

    @property
    def actor_pos(self) -> tuple[float, float, float]:
        return (self.ax, self.ay, self.az)

    @property
    def recipient_pos(self) -> tuple[float, float, float]:
        return (self.rx, self.ry, self.rz)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts, "t_s": self.t_s, "kind": self.kind,
            "actor_id": self.actor_id, "recipient_id": self.recipient_id,
            "actor_team": self.actor_team, "recipient_team": self.recipient_team,
            "ax": self.ax, "ay": self.ay, "az": self.az,
            "rx": self.rx, "ry": self.ry, "rz": self.rz,
            "weapon_id": self.weapon_id, "damage": self.damage,
        }
    
@dataclass
class Participant:
    player_id: str
    team_id: int
    first_s: float = 0.0
    last_s: float = 0.0


@dataclass
class Engagement:
    match_id: str
    start_s: float
    end_s: float
    last_s: float # build-time cursor, not persisted

    records: list[InteractionRecord] = field(default_factory=list)
    participants: dict[str, Participant] = field(default_factory=dict)
    teams: set[int] = field(default_factory=set)
    activity: float = 0.0  # activity_index, set at gate time in segment_engagements

    @property
    def duration(self):
        return self.end_s - self.start_s

    @property
    def centroid(self) -> tuple[float, float, float]:
        pts = [(r.ax, r.ay, r.az) for r in self.records] + \
              [(r.rx, r.ry, r.rz) for r in self.records]
        n = len(pts)
        return (
            sum(p[0] for p in pts)/n,
            sum(p[1] for p in pts)/n,
            sum(p[2] for p in pts)/n,
        )

    # build-time mutation (used by the segmentation sweep)

    def _touch_participant(self, pid: str, team: int, t_s: float) -> None:
        p = self.participants.get(pid)
        if p is None:
            self.participants[pid] = Participant(
                player_id=pid, team_id=team, first_s=t_s, last_s=t_s
            )
        else:
            p.first_s = min(p.first_s, t_s)
            p.last_s = max(p.last_s, t_s)

    def add(self, r: InteractionRecord) -> None:
        self.records.append(r)
        self.end_s = max(self.end_s, r.t_s)
        self.last_s = max(self.last_s, r.t_s)
        self.teams.add(r.actor_team)
        self.teams.add(r.recipient_team)
        self._touch_participant(r.actor_id, r.actor_team, r.t_s)
        self._touch_participant(r.recipient_id, r.recipient_team, r.t_s)

    def absorb(self, other: "Engagement") -> None:
        """Fold another open engagement into this one (multi-party merge)."""
        self.records.extend(other.records)
        self.start_s = min(self.start_s, other.start_s)
        self.end_s = max(self.end_s, other.end_s)
        self.last_s = max(self.last_s, other.last_s)
        self.teams |= other.teams
        for pid, op in other.participants.items():
            p = self.participants.get(pid)
            if p is None:
                self.participants[pid] = op
            else:
                p.first_s = min(p.first_s, op.first_s)
                p.last_s = max(p.last_s, op.last_s)

    # This is currently shit O(n^2) needs to be fixed later
    def matches(self, r: InteractionRecord, params: "SegmentParams") -> bool:
        """True if r links to this engagement: shares a team AND is within the
        linking radius of a record still inside the time window."""
        if r.actor_team not in self.teams and r.recipient_team not in self.teams:
            return False
        cutoff = r.t_s - params.window_s
        for rec in self.records:
            if rec.t_s >= cutoff and _link_distance(r, rec) <= params.max_dist_cm:
                return True
        return False

    def activity_index(self, params: "SegmentParams") -> float:
        """Intensity score and gate value: weighted action volume, discounted
        when only one team ever acted (a one-sided poke, not a fight)."""

        raw = sum(params.kind_weight.get(r.kind, 0.0) for r in self.records)
        offense_teams = {r.actor_team for r in self.records}
        reciprocity = 1.0 if len(offense_teams) >= 2 else params.oneside_factor
        return raw * reciprocity


    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "teams": sorted(self.teams),
            "participants": [
                {"player_id": p.player_id, "team_id": p.team_id,
                 "first_s": p.first_s, "last_s": p.last_s}
                for p in self.participants.values()
            ],
            "centroid": self.centroid,
            "activity_index": self.activity,
            "records": [r.to_dict() for r in self.records],
        }


Outcome = Literal["won", "lost", "favorable", "unfavorable", "stalemate"]

@dataclass(frozen=True)
class TeamOutcome:
    team_id: int
    label: Outcome
    elims_dealt: int
    elims_received: int
    knocks_dealt: int
    knocks_received: int
    damage_dealt: float
    damage_received: float


@dataclass(frozen=True)
class EngagementEvaluation:
    outcomes: dict[int, TeamOutcome]


@dataclass(frozen=True)
class EngagementResult:
    engagement: Engagement
    evaluation: EngagementEvaluation

    def to_dict(self) -> dict:                                 # on EngagementResult
        return {
            "engagement": self.engagement.to_dict(),
            "outcomes": [asdict(o) for o in self.evaluation.outcomes.values()],
        }


def build_teams(raw: RawMatchData) -> tuple[dict[str, int], dict[int, set[str]]]:
    eligible = {
        p["epicId"] for p in raw.players
        if not p["isBot"] and not p["isSpectator"]
    }
    team_of = {
        pid: t["teamId"] 
        for t in raw.teams 
        for pid in t["epicId"]
        if pid in eligible
    }
    team_members: dict[int, set[str]] = defaultdict(set)
    for pid, tid in team_of.items():
        team_members[tid].add(pid)

    return team_of, dict(team_members)


def _make_record(
    ctx: MatchContext, *, kind, ts, actor_id, recipient_id,
    actor_pos, recipient_pos, weapon_id=None, damage=0.0,
) -> InteractionRecord | None:
    if actor_id not in ctx.eligible or recipient_id not in ctx.eligible:
        return None
    team_actor = ctx.team_of.get(actor_id)
    team_recipient = ctx.team_of.get(recipient_id)
    if team_actor is None or team_recipient is None:
        return None
    if team_actor == team_recipient:
        return None  # a record is by definition a cross-team interaction
    if actor_pos is None or recipient_pos is None:
        return None
    ax, ay, az = actor_pos
    rx, ry, rz = recipient_pos

    return InteractionRecord(
        ts=ts,
        t_s=(ts - ctx.t0) / 1e6,
        kind=kind,
        actor_id=actor_id,
        recipient_id=recipient_id,
        actor_team=team_actor,
        recipient_team=team_recipient,
        ax=ax, ay=ay, az=az,
        rx=rx, ry=ry, rz=rz,
        weapon_id=weapon_id,
        damage=damage,
    )


def _from_shot_attempts(ctx: MatchContext) -> list[InteractionRecord]:
    # Positions are already resolved inside parse_shot_attempts (both the
    # shooter and the inferred recipient), so no index lookup is needed here.
    records = []
    for evt in ctx.shot_attempts:
        if evt["direct_hit"]:
            continue  # direct hits are seeded from fire_weapon_events instead
        if (
            record := _make_record(
                ctx=ctx,
                kind="shot_attempt",
                ts=evt["timestamp"],
                actor_id=evt["actor_id"],
                recipient_id=evt["recipient_id"],
                actor_pos=(evt["ax"], evt["ay"], evt["az"]),
                recipient_pos=(evt["rx"], evt["ry"], evt["rz"]),
                weapon_id=evt["weapon_id"],
            )
        ):
            records.append(record)

    return records


def _from_hits(ctx: MatchContext) -> list[InteractionRecord]:
    # parse_shots keeps every fire event (so hits on knocked enemies survive,
    # unlike parse_damage_dealt's HIT_PLAYER narrowing), resolves the shooter via
    # PlayerPositionIndex (actor_x/y/z, None when unresolved), and exposes the
    # bullet impact as end_x/y/z ≈ the recipient. Direct hits here are the same
    # events parse_shot_attempts flags direct_hit=True, which _from_shot_attempts
    # skips — no double count.
    records = []
    for evt in ctx.shots:
        if not evt["hit_player"]:
            continue
        ax = evt["actor_x"]
        if (record := _make_record(
            ctx=ctx,
            kind="hit",
            ts=evt["timestamp"],
            actor_id=evt["epic_id"],
            recipient_id=evt["hit_epic_id"],
            actor_pos=None if ax is None else (ax, evt["actor_y"], evt["actor_z"]),
            recipient_pos=(evt["end_x"], evt["end_y"], evt["end_z"]),
            weapon_id=evt["weapon_id"],
            damage=evt["damage"],
        )):
            records.append(record)
    return records


def _from_knocks(ctx: MatchContext) -> list[InteractionRecord]:
    records = []
    for evt in ctx.knocks:
        if (record := _make_record(
            ctx=ctx,
            kind="knock",
            ts=evt["timestamp"],
            actor_id=evt["actor_id"],
            recipient_id=evt["recipient_id"],
            actor_pos=(evt["ax"], evt["ay"], evt["az"]),
            recipient_pos=(evt["rx"], evt["ry"], evt["rz"]),
        )):
            records.append(record)
    return records


def _from_elims(ctx: MatchContext) -> list[InteractionRecord]:
    records = []
    for evt in ctx.elims:
        if (record := _make_record(
            ctx=ctx,
            kind="elim",
            ts=evt["timestamp"],
            actor_id=evt["actor_id"],
            recipient_id=evt["recipient_id"],
            actor_pos=(evt["ax"], evt["ay"], evt["az"]),
            recipient_pos=(evt["rx"], evt["ry"], evt["rz"]),
        )):
            records.append(record)
    return records


def build_interaction_records(ctx: MatchContext) -> list[InteractionRecord]:

    records = [
        *_from_shot_attempts(ctx),
        *_from_hits(ctx),
        *_from_knocks(ctx),
        *_from_elims(ctx),
    ]
    records.sort(key=lambda r: (r.ts, r.kind, r.actor_id))
    return records


def _link_distance(a: InteractionRecord, b: InteractionRecord) -> float:
    """Closest approach between the two records' player endpoints.

    Small whenever any of the four players involved were near each other, which
    keeps a long-range exchange linked (endpoints chain shooter-to-shooter) and a
    drifting fight linked burst-to-burst.
    """
    a_pts = (a.actor_pos, a.recipient_pos)
    b_pts = (b.actor_pos, b.recipient_pos)
    return min(math.dist(p, q) for p in a_pts for q in b_pts)


@dataclass
class SegmentParams:
    window_s: float = 12.0          # T: max quiet gap; also the edge time-window
    max_dist_cm: float = 10_000.0   # D: linking radius (~100 m)
    min_activity: float = 3.0       # activity_index gate
    oneside_factor: float = 0.5     # discount when only one team acted
    zone_cutoff: int | None = 6     # only group records BEFORE this zone (None = no cap)
    kind_weight: dict[str, float] = field(default_factory=lambda: {
        "shot_attempt": 1.0,
        "hit": 3.0,
        "knock": 20.0,
        "elim": 50.0,
    })


def segment_engagements(
    records: list[InteractionRecord],
    match_id: str,
    *,
    params: SegmentParams | None = None,
) -> list[Engagement]:
    """Path B sweep: one time-ordered pass that grows live Engagements.

    ``records`` must be time-sorted (build_interaction_records guarantees this).
    Returns only engagements that clear the activity gate.
    """
    params = params or SegmentParams()
    open_engagements: list[Engagement] = []
    finished: list[Engagement] = []

    for r in records:
        # 1. Close engagements that have gone quiet (no activity within window_s).
        #    Afterwards every open engagement has activity within T of r.
        still_open = []
        for e in open_engagements:
            if r.t_s - e.last_s > params.window_s:
                finished.append(e)
            else:
                still_open.append(e)
        open_engagements = still_open

        # 2. Every open engagement this record links to.
        matched = [e for e in open_engagements if e.matches(r, params)]

        # 3. Seed / add / merge.
        if not matched:
            e = Engagement(
                match_id=match_id, start_s=r.t_s, end_s=r.t_s, last_s=r.t_s
            )
            e.add(r)
            open_engagements.append(e)
        else:
            primary = matched[0]
            for other in matched[1:]:          # multi-party: fold the rest in
                primary.absorb(other)
                open_engagements.remove(other)
            primary.add(r)

    finished.extend(open_engagements)

    for e in finished:                         # merges can interleave record order
        e.records.sort(
            key=lambda rec: 
            (rec.ts, rec.kind, rec.actor_id)
        )

    # 4. Gate: drop trivial clusters (lone strays) below the activity threshold.
    #    Store the score we compute here on each engagement so it serializes
    #    (to_dict) without needing params downstream.
    kept = []
    for e in finished:
        e.activity = e.activity_index(params)
        if e.activity >= params.min_activity:
            kept.append(e)
    return kept


DAMAGE_RATIO_FAVOR = 1.2

# TODO:
# case: Teams A, B in fight. Team C third-parties.
# Team A wipes team B without losses, but team C eliminates one of team A.
# Team A has net positive elims, but their outcome is still unfavorable.

def _grade(s: dict) -> Outcome:
    net_elims = s["ed"] - s["er"]
    if net_elims > 0:
        return "favorable"
    if net_elims < 0:
        return "unfavorable"
    dd, dr = s["dd"], s["dr"]
    if dr == 0:
        return "favorable" if dd > 0 else "stalemate"
    ratio = dd / dr
    if ratio >= DAMAGE_RATIO_FAVOR:
        return "favorable"
    if ratio <= 1 / DAMAGE_RATIO_FAVOR:
        return "unfavorable"

    return "stalemate"


def evaluate_engagement(
    ctx: MatchContext, 
    engagement: Engagement,
) -> EngagementEvaluation:

    parts = engagement.participants
    start_s, end_s = engagement.start_s, engagement.end_s

    def relevant(evt: dict) -> bool:
        return (
            start_s <= evt["game_time_seconds"] <= end_s
            and evt["actor_id"] in parts
            and evt["recipient_id"] in parts
        )

    stats = {
        t: { "dd": 0.0, "dr": 0.0, "ed": 0, "er": 0, "kd": 0, "kr": 0, } 
        for t in engagement.teams
    }
    eliminated: dict[int, set[str]] = {t: set() for t in engagement.teams}

    for evt in ctx.damage_dealt_events:
        if not relevant(evt):
            continue
        actor_team = parts[evt["actor_id"]].team_id
        recipient_team = parts[evt["recipient_id"]].team_id
        stats[actor_team]["dd"] += evt["damage"]
        stats[recipient_team]["dr"] += evt["damage"]

    for evt in ctx.knocks:
        if not relevant(evt):
            continue
        at = parts[evt["actor_id"]].team_id
        rt = parts[evt["recipient_id"]].team_id
        stats[at]["kd"] += 1
        stats[rt]["kr"] += 1

    for evt in ctx.elims:
        if not relevant(evt):
            continue
        at = parts[evt["actor_id"]].team_id
        rt = parts[evt["recipient_id"]].team_id
        stats[at]["ed"] += 1
        stats[rt]["er"] += 1
        eliminated[rt].add(evt["recipient_id"])

    roster: dict[int, set[str]] = defaultdict(set)
    for pid, p in parts.items():
        roster[p.team_id].add(pid)
    
    wiped = {
        t: len(eliminated[t]) >= len(roster[t]) for t in engagement.teams
    }
    survivors = [t for t in engagement.teams if not wiped[t]]

    labels: dict[int, Outcome] = {}
    if any(wiped.values()):
        for t in engagement.teams:
            if wiped[t]:
                labels[t] = "lost"
        if len(survivors) == 1:
            labels[survivors[0]] = "won"

    for t in engagement.teams:
        if t not in labels:
            labels[t] = _grade(stats[t])
    
    outcomes = {
        t: TeamOutcome(
            team_id=t, label=labels[t],
            elims_dealt=stats[t]["ed"], elims_received=stats[t]["er"],
            knocks_dealt=stats[t]["kd"], knocks_received=stats[t]["kr"],
            damage_dealt=stats[t]["dd"], damage_received=stats[t]["dr"],
        )
        for t in engagement.teams
    }

    return EngagementEvaluation(outcomes=outcomes)


def parse_engagements(
    ctx: MatchContext, 
    *,
    params: SegmentParams | None = None
) -> list[EngagementResult]:
    """Public entry point: extract seeder records, then segment them."""
    params = params or SegmentParams()

    records = build_interaction_records(ctx)

    # Cap grouping to the early/mid game: drop records at or after the cutoff
    # zone BEFORE segmentation, so no engagement can grow a late-game blob once
    # the circle is small and every team is within linking distance.
    if params.zone_cutoff is not None:
        ordered_zones = sorted_zone_events(ctx.raw.zone_update_events)
        records = [
            r for r in records
            if zone_for_timestamp(ordered_zones, r.ts) < params.zone_cutoff
        ]

    engagements = segment_engagements(records, ctx.match_id, params=params)

    return [EngagementResult(e, evaluate_engagement(ctx, e)) for e in engagements]


if __name__ == "__main__":
    pass
