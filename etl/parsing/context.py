from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

from etl.parsing.indexing import PlayerPositionIndex
from etl.parsing.shot_attempts import parse_shot_attempts
from etl.parsing.basic import parse_shots, parse_elims, parse_knocks, parse_damage_dealt
from etl.types import RawMatchData

@dataclass
class MatchContext:
    """
    Wraps RawMatchData and lazily derives the shared products the
    engagement parsers all need — each computed at most once.
    """

    raw: RawMatchData
    event_window_id: str | None = None

    @property
    def match_id(self) -> str:
        return self.raw.match_id

    @property
    def t0(self):
        return self.raw.info["aircraftStartTime"]

    @cached_property
    def eligible(self) -> set[str]:
        return {
            p["epicId"] for p in self.raw.players
            if not p["isBot"] and not p["isSpectator"]
        }

    @cached_property
    def damage_dealt_events(self) -> list[dict]:
        return parse_damage_dealt(self.raw)

    @cached_property
    def _teams(self) -> tuple[dict[str, int], dict[int, set[str]]]:
        team_of = {
            pid: t["teamId"]
            for t in self.raw.teams
            for pid in t["epicId"]
            if pid in self.eligible
        }
        members: dict[int, set[str]] = defaultdict(set)
        for pid, tid in team_of.items():
            members[tid].add(pid)
        return team_of, dict(members)

    @property
    def team_of(self) -> dict[str, int]:
        return self._teams[0]

    @property
    def team_members(self) -> dict[int, set[str]]:
        return self._teams[1]

    @cached_property
    def shot_attempts(self):
        # parse_shot_attempts applies its own MAX_GAP_US default; override here
        # only if the stat table ever needs a looser gap than the ray-test.
        return parse_shot_attempts(self.raw)

    @cached_property
    def shots(self):
        return parse_shots(self.raw)

    @cached_property
    def elims(self):
        return parse_elims(self.raw)

    @cached_property
    def knocks(self):
        return parse_knocks(self.raw)
