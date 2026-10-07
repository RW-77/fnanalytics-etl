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
class Phase:
    """A stretch of an engagement with a fixed set of core teams. A new phase
    starts when a team joins; a team that is wiped or leaves doesn't start one."""
    start_s: float
    end_s: float
    teams: set[int]

    def to_dict(self) -> dict:
        return {"start_s": self.start_s, "end_s": self.end_s, "teams": sorted(self.teams)}


@dataclass
class Engagement:
    match_id: str
    start_s: float
    end_s: float

    records: list[InteractionRecord] = field(default_factory=list)  # between core teams
    participants: dict[str, Participant] = field(default_factory=dict)
    teams: set[int] = field(default_factory=set)  # core teams
    phases: list[Phase] = field(default_factory=list)
    # Records between a core team and a team shooting in from outside the
    # fight: kept as data, but that team is not a participant.
    periphery: list[InteractionRecord] = field(default_factory=list)
    activity: float = 0.0  # summed record weight of the core records, set in segment_engagements
    zone: int | None = None  # zone at the first record, set in parse_engagements

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

    # build-time mutation (used by segment_engagements)

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
        self.start_s = min(self.start_s, r.t_s)
        self.end_s = max(self.end_s, r.t_s)
        self.teams.add(r.actor_team)
        self.teams.add(r.recipient_team)
        self._touch_participant(r.actor_id, r.actor_team, r.t_s)
        self._touch_participant(r.recipient_id, r.recipient_team, r.t_s)

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
            "zone": self.zone,
            "phases": [p.to_dict() for p in self.phases],
            "records": [r.to_dict() for r in self.records],
            "periphery": [r.to_dict() for r in self.periphery],
        }


Outcome = Literal["won", "lost", "favorable", "unfavorable", "stalemate"]

# Which evaluator rule decided a team's outcome:
#   wiped         -> lost: every player on the team was eliminated by the end
#   last_standing -> won: the only team left after at least one team was wiped
#   net_elims     -> won/lost: more elims dealt than taken (or fewer)
#   damage_ratio  -> favorable/unfavorable: elims even, decided by damage ratio
#   even          -> stalemate: elims even, damage within DAMAGE_RATIO_FAVOR
Reason = Literal["wiped", "last_standing", "net_elims", "damage_ratio", "even"]

@dataclass(frozen=True)
class TeamOutcome:
    team_id: int
    label: Outcome
    reason: Reason
    elims_dealt: int
    elims_received: int
    knocks_dealt: int
    knocks_received: int
    damage_dealt: float
    damage_received: float


@dataclass(frozen=True)
class PlayerOutcome:
    player_id: str
    team_id: int
    elims_dealt: int
    elims_received: int
    knocks_dealt: int
    knocks_received: int
    damage_dealt: float
    damage_received: float


@dataclass(frozen=True)
class EngagementEvaluation:
    outcomes: dict[int, TeamOutcome]
    players: dict[str, PlayerOutcome]


@dataclass(frozen=True)
class EngagementResult:
    engagement: Engagement
    evaluation: EngagementEvaluation         # the whole engagement
    phases: list[EngagementEvaluation]       # one per engagement.phases entry

    def to_dict(self) -> dict:
        return {
            "engagement": self.engagement.to_dict(),
            "outcomes": [asdict(o) for o in self.evaluation.outcomes.values()],
            "players": [asdict(p) for p in self.evaluation.players.values()],
            "phases": [
                {
                    **phase.to_dict(),
                    "outcomes": [asdict(o) for o in ev.outcomes.values()],
                    "players": [asdict(p) for p in ev.players.values()],
                }
                for phase, ev in zip(self.engagement.phases, self.phases)
            ],
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


# A position this stale (on either side of the query) doesn't count for contact.
CONTACT_MAX_GAP_US = 2_000_000


@dataclass
class SegmentParams:
    # Evidence: each record counts its kind weight times a distance falloff
    # that is ~1 up close, 1/2 at d0_cm and near 0 far away (falloff_p sets how
    # sharply). Evidence fades by a factor of e every tau_s seconds.
    d0_cm: float = 2_500.0
    falloff_p: float = 3.0
    tau_s: float = 5.0
    # Start: both teams fired at each other (the weaker of the two directions,
    # plus any knocks/elims) reaching start_theta, and their closest players
    # came within contact_cm.
    start_theta: float = 0.1
    # Join: a third team's exchange with a core team (both directions summed)
    # reaches join_theta, also within contact_cm.
    join_theta: float = 1.2
    contact_cm: float = 1_200.0
    # A team pair's exchange splits at a quiet gap longer than 3 * tau_s,
    # unless the two teams stayed within sustain_cm for the whole gap.
    sustain_cm: float = 3_000.0
    # Exchanges only link into one engagement when their action is this close.
    join_radius_cm: float = 10_000.0
    zone_cutoff: int | None = 6     # only group records BEFORE this zone (None = no cap)
    kind_weight: dict[str, float] = field(default_factory=lambda: {
        "shot_attempt": 0.25,  # a miss's recipient is inferred, so it's weak evidence
        "hit": 3.0,
        "knock": 10.0,
        "elim": 20.0,
    })


def _record_weight(r: InteractionRecord, params: SegmentParams) -> float:
    """Evidence one record adds: its kind weight times the distance falloff."""
    d = math.dist(r.actor_pos, r.recipient_pos)
    return params.kind_weight.get(r.kind, 0.0) / (1 + (d / params.d0_cm) ** params.falloff_p)


def _team_distance_cm(
    ctx: MatchContext, a: int, b: int, second: int, cache: dict,
) -> float | None:
    """Closest distance between living players of teams a and b at one whole
    second of game time, or None when no position is fresh enough."""
    key = (a, b, second)
    if key not in cache:
        ts = int(ctx.t0 + second * 1e6)
        locs = {
            t: [
                pos.location for pid in ctx.team_members.get(t, ())
                if (pos := ctx.position_index.position_at(ts, pid, CONTACT_MAX_GAP_US))
            ]
            for t in (a, b)
        }
        cache[key] = min(
            (math.dist(pa, pb) for pa in locs[a] for pb in locs[b]), default=None
        )
    return cache[key]


@dataclass(eq=False)
class _Exchange:
    """One team pair's run of records, unbroken by a long quiet gap: the unit
    segment_engagements scores and then links into engagements."""
    teams: tuple[int, int]            # (lower team id, higher team id)
    records: list[InteractionRecord]
    start_score: float = 0.0          # peak of: weaker direction + knocks/elims
    start_t: float | None = None      # when start_score first reached start_theta
    join_score: float = 0.0           # peak of: both directions summed
    join_t: float | None = None       # when join_score first reached join_theta
    closest_cm: float | None = None   # closest the two teams came around the exchange

    @property
    def first_s(self) -> float:
        return self.records[0].t_s

    @property
    def last_s(self) -> float:
        return self.records[-1].t_s

    def score(self, params: SegmentParams) -> None:
        """Run the fading evidence through the exchange, keeping the peaks."""
        a = self.teams[0]
        a_on_b = b_on_a = decisive = 0.0
        prev_t = self.first_s
        for r in self.records:
            fade = math.exp(-(r.t_s - prev_t) / params.tau_s)
            a_on_b, b_on_a, decisive = a_on_b * fade, b_on_a * fade, decisive * fade
            prev_t = r.t_s
            w = _record_weight(r, params)
            if r.kind in ("knock", "elim"):
                decisive += w
            elif r.actor_team == a:
                a_on_b += w
            else:
                b_on_a += w
            start = min(a_on_b, b_on_a) + decisive
            joined = a_on_b + b_on_a + decisive
            self.start_score = max(self.start_score, start)
            self.join_score = max(self.join_score, joined)
            if self.start_t is None and start >= params.start_theta:
                self.start_t = r.t_s
            if self.join_t is None and joined >= params.join_theta:
                self.join_t = r.t_s


def _split_exchanges(
    ctx: MatchContext,
    records: list[InteractionRecord],
    params: SegmentParams,
    cache: dict,
) -> list[_Exchange]:
    """Group records by team pair, then split each pair's records at quiet gaps
    longer than 3 * tau_s, unless the two teams stayed within sustain_cm for
    the whole gap (a fight where nobody landed a shot for a while)."""
    by_pair: dict[tuple[int, int], list[InteractionRecord]] = defaultdict(list)
    for r in records:
        pair = (min(r.actor_team, r.recipient_team), max(r.actor_team, r.recipient_team))
        by_pair[pair].append(r)

    exchanges = []
    for (a, b), rs in by_pair.items():
        run = [rs[0]]
        for prev, r in zip(rs, rs[1:]):
            if r.t_s - prev.t_s > 3 * params.tau_s:
                gap = [
                    _team_distance_cm(ctx, a, b, sec, cache)
                    for sec in range(math.ceil(prev.t_s), math.floor(r.t_s) + 1)
                ]
                known = [d for d in gap if d is not None]
                if not known or max(known) > params.sustain_cm:
                    exchanges.append(_Exchange((a, b), run))
                    run = []
            run.append(r)
        exchanges.append(_Exchange((a, b), run))
    return exchanges


def segment_engagements(
    ctx: MatchContext,
    records: list[InteractionRecord],
    *,
    params: SegmentParams | None = None,
) -> list[Engagement]:
    """Group records into engagements.

    1. Split each team pair's records into exchanges and score each one.
    2. An exchange STARTS a fight when both teams fired at each other enough
       (start_theta) and came within contact_cm. It lets a team JOIN a fight
       when its summed score reaches join_theta, also within contact_cm.
    3. Qualifying exchanges that share a team, overlap in time and act within
       join_radius_cm of each other link into one engagement. Only groups with
       at least one starting exchange become engagements.
    4. Every other exchange that overlaps an engagement and shares a team with
       it attaches: as core records when both its teams are core, otherwise as
       periphery (a team shooting in from outside).
    5. Phases: a team entering more than tau_s after the current phase opened
       starts a new phase. Teams that are wiped or leave don't.

    ``records`` must be time-sorted (build_interaction_records guarantees this).
    """
    params = params or SegmentParams()
    cache: dict = {}
    pad = params.tau_s  # slack around an exchange for contact and time overlap

    exchanges = _split_exchanges(ctx, records, params, cache)
    for x in exchanges:
        x.score(params)
        if x.start_t is None and x.join_t is None:
            continue  # can't qualify either way, so contact doesn't matter
        a, b = x.teams
        seconds = range(math.floor(x.first_s - pad), math.ceil(x.last_s + pad) + 1)
        x.closest_cm = min(
            (d for sec in seconds if (d := _team_distance_cm(ctx, a, b, sec, cache)) is not None),
            default=None,
        )

    in_contact = lambda x: x.closest_cm is not None and x.closest_cm <= params.contact_cm
    starting = {id(x) for x in exchanges if x.start_t is not None and in_contact(x)}
    qualifying = [
        x for x in exchanges
        if id(x) in starting or (x.join_t is not None and in_contact(x))
    ]

    def overlaps(x: _Exchange, first_s: float, last_s: float) -> bool:
        return x.first_s <= last_s + pad and first_s <= x.last_s + pad

    def near(x: _Exchange, recs: list[InteractionRecord]) -> bool:
        return any(
            _link_distance(r, q) <= params.join_radius_cm for r in x.records for q in recs
        )

    # 3. Link qualifying exchanges into groups.
    groups: list[list[_Exchange]] = []
    for x in sorted(qualifying, key=lambda x: x.first_s):
        linked = [
            g for g in groups
            if any(
                set(x.teams) & set(y.teams)
                and overlaps(x, y.first_s, y.last_s)
                and near(x, y.records)
                for y in g
            )
        ]
        merged = [x] + [y for g in linked for y in g]
        groups = [g for g in groups if not any(g is l for l in linked)] + [merged]

    engagements: list[tuple[Engagement, list[_Exchange]]] = []
    placed: set[int] = set()
    for group in groups:
        if not any(id(x) in starting for x in group):
            continue
        first = min(x.first_s for x in group)
        e = Engagement(match_id=ctx.match_id, start_s=first, end_s=first)
        for x in group:
            placed.add(id(x))
            for r in x.records:
                e.add(r)
        engagements.append((e, group))

    # 4. Attach the remaining exchanges as core records or periphery.
    for e, _ in engagements:
        for x in exchanges:
            if id(x) in placed or not overlaps(x, e.start_s, e.end_s):
                continue
            shared = set(x.teams) & e.teams
            if not shared or not near(x, e.records):
                continue
            placed.add(id(x))
            if len(shared) == 2:
                for r in x.records:
                    e.add(r)
            else:
                e.periphery.extend(x.records)

    # 5. Phases, from when each team entered (its first qualifying moment).
    for e, group in engagements:
        entry: dict[int, float] = {}
        for x in group:
            t = x.start_t if id(x) in starting else x.join_t
            for team in x.teams:
                entry[team] = min(entry.get(team, t), t)
        order = sorted(entry.items(), key=lambda kv: kv[1])
        anchor = order[0][1]
        e.phases = [Phase(e.start_s, e.end_s, set())]
        for team, t in order:
            if t - anchor > params.tau_s:  # a later arrival opens a new phase
                e.phases[-1].end_s = t
                e.phases.append(Phase(t, e.end_s, set(e.phases[-1].teams)))
                anchor = t
            e.phases[-1].teams.add(team)

        sort_key = lambda rec: (rec.ts, rec.kind, rec.actor_id)
        e.records.sort(key=sort_key)
        e.periphery.sort(key=sort_key)
        e.activity = sum(_record_weight(r, params) for r in e.records)

    return sorted((e for e, _ in engagements), key=lambda e: e.start_s)


DAMAGE_RATIO_FAVOR = 1.2

# TODO:
# case: Teams A, B in fight. Team C third-parties.
# Team A wipes team B without losses, but team C eliminates one of team A.
# Team A has net positive elims, but their outcome is still unfavorable.
# Per-phase evaluation (phase 1: A won; phase 2: A vs C) covers this; the
# whole-engagement grade below still has the problem.

def _grade(s: dict) -> tuple[Outcome, Reason]:
    # A difference in eliminations is decisive: the team that lost fewer
    # players won, even with no team wiped.
    net_elims = s["ed"] - s["er"]
    if net_elims > 0:
        return "won", "net_elims"
    if net_elims < 0:
        return "lost", "net_elims"
    dd, dr = s["dd"], s["dr"]
    if dr == 0:
        return ("favorable", "damage_ratio") if dd > 0 else ("stalemate", "even")
    ratio = dd / dr
    if ratio >= DAMAGE_RATIO_FAVOR:
        return "favorable", "damage_ratio"
    if ratio <= 1 / DAMAGE_RATIO_FAVOR:
        return "unfavorable", "damage_ratio"

    return "stalemate", "even"


def evaluate_engagement(
    ctx: MatchContext,
    engagement: Engagement,
    phase: Phase | None = None,
) -> EngagementEvaluation:
    """Grade each team over the whole engagement, or over one of its phases
    (that phase's window and teams only)."""
    window = phase or engagement
    start_s, end_s, teams = window.start_s, window.end_s, window.teams
    parts = {
        pid: p for pid, p in engagement.participants.items() if p.team_id in teams
    }

    def relevant(evt: dict) -> bool:
        return (
            start_s <= evt["game_time_seconds"] <= end_s
            and evt["actor_id"] in parts
            and evt["recipient_id"] in parts
        )

    stats = {
        t: { "dd": 0.0, "dr": 0.0, "ed": 0, "er": 0, "kd": 0, "kr": 0, } 
        for t in teams
    }
    # Same tallies per participant, from the same filtered events, so a team's
    # player rows always sum to its team row.
    pstats = {
        pid: { "dd": 0.0, "dr": 0.0, "ed": 0, "er": 0, "kd": 0, "kr": 0, }
        for pid in parts
    }

    for evt in ctx.damage_dealt_events:
        if not relevant(evt):
            continue
        actor_team = parts[evt["actor_id"]].team_id
        recipient_team = parts[evt["recipient_id"]].team_id
        stats[actor_team]["dd"] += evt["damage"]
        stats[recipient_team]["dr"] += evt["damage"]
        pstats[evt["actor_id"]]["dd"] += evt["damage"]
        pstats[evt["recipient_id"]]["dr"] += evt["damage"]

    for evt in ctx.knocks:
        if not relevant(evt):
            continue
        at = parts[evt["actor_id"]].team_id
        rt = parts[evt["recipient_id"]].team_id
        stats[at]["kd"] += 1
        stats[rt]["kr"] += 1
        pstats[evt["actor_id"]]["kd"] += 1
        pstats[evt["recipient_id"]]["kr"] += 1

    for evt in ctx.elims:
        if not relevant(evt):
            continue
        at = parts[evt["actor_id"]].team_id
        rt = parts[evt["recipient_id"]].team_id
        stats[at]["ed"] += 1
        stats[rt]["er"] += 1
        pstats[evt["actor_id"]]["ed"] += 1
        pstats[evt["recipient_id"]]["er"] += 1

    # Wiped means no one on the team's full roster (not just the players in
    # this fight) is alive at the end of the window. +1 ms absorbs float
    # rounding of end_s, so an elim on the last record still counts.
    end_ts = int(ctx.t0 + end_s * 1e6) + 1_000
    wiped = {
        t: not any(
            ctx.position_index.alive_at(pid, end_ts) for pid in ctx.team_members.get(t, ())
        )
        for t in teams
    }
    survivors = [t for t in teams if not wiped[t]]

    verdicts: dict[int, tuple[Outcome, Reason]] = {}
    if any(wiped.values()):
        for t in teams:
            if wiped[t]:
                verdicts[t] = ("lost", "wiped")
        if len(survivors) == 1:
            verdicts[survivors[0]] = ("won", "last_standing")

    for t in teams:
        if t not in verdicts:
            verdicts[t] = _grade(stats[t])

    outcomes = {
        t: TeamOutcome(
            team_id=t, label=verdicts[t][0], reason=verdicts[t][1],
            elims_dealt=stats[t]["ed"], elims_received=stats[t]["er"],
            knocks_dealt=stats[t]["kd"], knocks_received=stats[t]["kr"],
            damage_dealt=stats[t]["dd"], damage_received=stats[t]["dr"],
        )
        for t in teams
    }

    players = {
        pid: PlayerOutcome(
            player_id=pid, team_id=parts[pid].team_id,
            elims_dealt=s["ed"], elims_received=s["er"],
            knocks_dealt=s["kd"], knocks_received=s["kr"],
            damage_dealt=s["dd"], damage_received=s["dr"],
        )
        for pid, s in pstats.items()
    }

    return EngagementEvaluation(outcomes=outcomes, players=players)


def parse_engagements(
    ctx: MatchContext, 
    *,
    params: SegmentParams | None = None
) -> list[EngagementResult]:
    """Public entry point: extract seeder records, then segment them."""
    params = params or SegmentParams()

    records = build_interaction_records(ctx)
    ordered_zones = sorted_zone_events(ctx.raw.zone_update_events)

    # Cap grouping to the early/mid game: drop records at or after the cutoff
    # zone BEFORE segmentation, so no engagement can grow a late-game blob once
    # the circle is small and every team is within linking distance.
    if params.zone_cutoff is not None:
        records = [
            r for r in records
            if zone_for_timestamp(ordered_zones, r.ts) < params.zone_cutoff
        ]

    engagements = segment_engagements(ctx, records, params=params)

    for e in engagements:                      # records are time-sorted
        e.zone = zone_for_timestamp(ordered_zones, e.records[0].ts)

    return [
        EngagementResult(
            e,
            evaluate_engagement(ctx, e),
            [evaluate_engagement(ctx, e, phase) for phase in e.phases],
        )
        for e in engagements
    ]


if __name__ == "__main__":
    pass


# Shape of the per-match engagements file. Bump when the JSON's structure
# changes (not when detection changes: that is ENGAGEMENTS_VERSION in
# etl/orch/registry.py), so the website can tell old files apart.
ASSET_SCHEMA_VERSION = 1


def build_engagements_asset(ctx: MatchContext, params: SegmentParams | None = None) -> dict:
    """Detect and grade a match's engagements and return the JSON the website
    reads: ``replays/matches/<id>/engagements.json`` in S3, and the replay-lab
    fixture (scripts/dump_engagement_fixture.py).

    Shape::

        {
          "schema_version", "match_id", "params", "engagement_count",
          "engagements": [
            { ...Engagement.to_dict(),     # timing, core teams, participants, centroid,
                                           # activity_index, zone, records, periphery
              "id": int,                   # position in time order
              "outcomes": [TeamOutcome],   # whole engagement: one per core team
              "players":  [PlayerOutcome], # whole engagement: one per participant
              "phases": [                  # a new phase each time a team joins
                { "start_s", "end_s", "teams", "outcomes", "players" },
              ],
            },
          ],
        }

    ``id`` is only stable while detection is unchanged: a version bump that
    adds or removes an engagement renumbers the ones after it.
    """
    params = params or SegmentParams()
    results = sorted(parse_engagements(ctx, params=params), key=lambda r: r.engagement.start_s)

    engagements = []
    for i, result in enumerate(results):
        d = result.to_dict()
        engagements.append({
            **d["engagement"],
            "id": i,
            "outcomes": d["outcomes"],
            "players": d["players"],
            "phases": d["phases"],
        })

    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "match_id": ctx.match_id,
        "params": asdict(params),
        "engagement_count": len(engagements),
        "engagements": engagements,
    }
