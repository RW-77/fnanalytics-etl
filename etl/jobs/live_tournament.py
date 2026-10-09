"""Live tournament loop — keep leaderboards fresh and process each game as soon
as Osirion has it, spending as few paid Osirion requests as possible.

Two APIs, two cadences:

* fnapi (free) — every ``--interval`` seconds each active window's leaderboard
  is re-pulled and loaded (:func:`ingest_event_window_leaderboard`, which makes
  no Osirion calls once ``scoring.json`` is cached). The same leaderboard is the
  game-finished detector, so no paid request is made until a game has ended.
* Osirion (paid per request) — only after a game ends: the window's match list
  is probed (one request) on a learned schedule, and once the game is listed
  the full pipeline (``process_event_window(..., refresh=True)``, i.e.
  ``process_tournaments --refresh``) runs in a subprocess, so leaderboard ticks
  continue while it works.

Detecting a finished game from ``sessionHistory``
-------------------------------------------------
A team's ``sessionHistory`` entry appears when *that team* is eliminated, so a
new ``sessionId`` only means a game is under way. A session is finished once it
carries the winning team's entry (``VICTORY_ROYALE_STAT``); that entry's
``endTime`` is the game's end (Osirion's ``endTimestamp`` lands ~6 s later).
``sessionId`` equals the Osirion match's ``info.serverId`` (``Match.session_id``),
so "is it listed yet" and "is it processed" are exact lookups, not guesses.

Learning the availability delay
-------------------------------
Each probed game yields ``(last miss, first hit]`` in seconds after its end.
The next game probes every ``--probe-step`` seconds from just before the
fastest availability seen to just after the slowest, then backs off until
``GIVE_UP_AFTER_S``. Because probing starts at the fastest-seen point, a game
that is ready even earlier is caught on the first probe and pulls the window
earlier next time. Observations persist in the state file, so Day 2 starts
with what Day 1 learned.

State (sessions, probes, learned plan, paid-request count) lives in
``data/live/<event_id>.json`` so a restart resumes instead of re-probing.

Usage::

    python -m etl.jobs.live_tournament --event-id epicgames_MannekenPis_Official
    python -m etl.jobs.live_tournament --event-id ... --dry-run --once
"""

import argparse
import contextlib
import io
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

import etl.api.fnapi_osirion_client as fnapi
import etl.api.osirion_client as osr
from etl.db.models import EventWindow, Match
from etl.db.session import get_session
from etl.db.status import STATUS_PROCESSED
from etl.fetching.match_data_fetching import ensure_event_window_leaderboard_raw
from etl.jobs.process_tournaments import (
    ingest_event_window_leaderboard,
    process_event_window,
)
from etl.parsing.tournament.classification import get_region_code
from etl.parsing.tournament.leaderboard import VICTORY_ROYALE_STAT
from etl.parsing.tournament.scoring import PLACEMENT_STAT
from etl.types import JsonList, RawLeaderboardData


REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = REPO_ROOT / "data" / "live"

# Keep polling a window's leaderboard this long after it closes, to pick up
# late corrections (penalties, DQs) to the final standings.
LEADERBOARD_GRACE = timedelta(hours=1)
# A session with no winner entry whose team count hasn't moved for this long is
# treated as ended (safety net; a 50-team game never goes 20 min without an
# elimination).
STALE_SESSION_AFTER = timedelta(minutes=20)

# Probe schedule, in seconds after a game's end. Defaults apply until a game's
# availability has been observed.
DEFAULT_START_S = 60
DEFAULT_DENSE_UNTIL_S = 10 * 60
MIN_START_S = 15
BACKOFF_FACTOR = 1.5
# Past the learned window keep probing at least once a minute: a listed game
# should never sit unprocessed for long, and a probe is one cheap request.
MAX_PROBE_GAP_S = 60
GIVE_UP_AFTER_S = 90 * 60
RECENT_OBSERVATIONS = 8

PIPELINE_RETRY_AFTER = timedelta(minutes=3)
MAX_PIPELINE_RUNS = 3

# Session lifecycle: live → waiting (ended, not yet listed by Osirion) →
# listed → processing (pipeline running) → done | failed (retried) | gave_up.
ACTIVE_STATUSES = {"live", "waiting", "listed", "processing", "failed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[{_now().strftime('%H:%M:%SZ')}] {message}", flush=True)


# ---------------------------------------------------------------------------
# Paid-request accounting
# ---------------------------------------------------------------------------

class _CountingRequests:
    """Stand-in for the ``requests`` module inside ``osirion_client`` that
    counts every HTTP attempt (retries included — each one is billed)."""

    def __init__(self, real, forbid: bool = False):
        self._real = real
        self._forbid = forbid
        self.calls = 0

    def get(self, *args, **kwargs):
        if self._forbid:
            raise RuntimeError("dry run: refusing to make a paid Osirion request")
        self.calls += 1
        return self._real.get(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def install_paid_request_counter(forbid: bool = False) -> _CountingRequests:
    counter = _CountingRequests(osr.requests, forbid=forbid)
    osr.requests = counter
    return counter


# ---------------------------------------------------------------------------
# Probe schedule
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbePlan:
    start_s: float
    dense_until_s: float
    step_s: float

    def offsets(self) -> list[float]:
        """Seconds after a game's end at which to probe, ascending."""
        offsets: list[float] = []
        t = self.start_s
        while t <= self.dense_until_s:
            offsets.append(t)
            t += self.step_s
        gap = self.step_s
        while t <= GIVE_UP_AFTER_S:
            offsets.append(t)
            gap = min(MAX_PROBE_GAP_S, gap * BACKOFF_FACTOR)
            t += gap
        return offsets

    def describe(self) -> str:
        return (
            f"probe every {self.step_s:.0f}s from +{self.start_s:.0f}s to "
            f"+{self.dense_until_s:.0f}s, then back off (give up at "
            f"+{GIVE_UP_AFTER_S // 60}m)"
        )


def learn_plan(observations: list[dict], step_s: float) -> ProbePlan:
    """Dense-probe window spanning the recently observed availability delays."""
    recent = observations[-RECENT_OBSERVATIONS:]
    if not recent:
        return ProbePlan(DEFAULT_START_S, DEFAULT_DENSE_UNTIL_S, step_s)
    fastest = min(o["upper_s"] for o in recent)
    slowest = max(o["upper_s"] for o in recent)
    return ProbePlan(
        start_s=max(MIN_START_S, fastest - 2 * step_s),
        dense_until_s=slowest + 2 * step_s,
        step_s=step_s,
    )


def next_probe_due(record: dict, plan: ProbePlan) -> datetime | None:
    """When *record* should next be probed; ``None`` once the schedule is spent.

    Offsets sit on a grid relative to the game's end, so after an off-grid probe
    (the loop started late) the next grid point can be seconds away — require
    at least half a step between probes.
    """
    ended = _parse(record["ended_at"])
    last_offset = max(
        ((_parse(p["at"]) - ended).total_seconds() for p in record["probes"]),
        default=-math.inf,
    )
    for offset in plan.offsets():
        if offset >= last_offset + plan.step_s / 2:
            return ended + timedelta(seconds=offset)
    return None


# ---------------------------------------------------------------------------
# Leaderboard → sessions
# ---------------------------------------------------------------------------

def summarize_sessions(entries: JsonList) -> dict[str, dict]:
    """Per ``sessionId``: teams reported so far, first/last team end, and the
    winner's end time (``None`` while the game is still running)."""
    sessions: dict[str, dict] = {}
    for entry in entries:
        for played in entry["sessionHistory"]:
            end = played["endTime"]
            stats = played["trackedStats"]
            summary = sessions.setdefault(
                played["sessionId"],
                {"teams": 0, "first_end": end, "last_end": end, "winner_end": None},
            )
            summary["teams"] += 1
            if _parse(end) < _parse(summary["first_end"]):
                summary["first_end"] = end
            if _parse(end) > _parse(summary["last_end"]):
                summary["last_end"] = end
            if stats.get(VICTORY_ROYALE_STAT, 0) >= 1 or stats.get(PLACEMENT_STAT) == 1:
                summary["winner_end"] = end
    return sessions


def _event_window_row_exists(event_window_id: str) -> bool:
    with get_session() as session:
        return session.get(EventWindow, event_window_id) is not None


def refresh_leaderboard(
    event_window_id: str, min_teams: int = 0
) -> tuple[RawLeaderboardData, bool]:
    """Re-pull *event_window_id*'s leaderboard from fnapi; load it into the DB
    when possible. Returns ``(raw, loaded_to_db)``.

    Leaderboard rows FK to ``event_windows``, a row the pipeline creates when it
    ingests the window's first game — until then the leaderboard is only fetched
    (and cached to S3). A response with fewer than *min_teams* entries is not
    loaded: fnapi occasionally returns a partial leaderboard (seen: 24 of 50),
    and loading it would blank half the standings until the next tick.
    """
    raw = ensure_event_window_leaderboard_raw(
        event_window_id, get_region_code(event_window_id), refresh=True
    )
    if len(raw.entries) < min_teams or not _event_window_row_exists(event_window_id):
        return raw, False
    # Loads the copy just fetched (refresh=False reads it back from S3).
    with contextlib.redirect_stdout(io.StringIO()):
        raw = ingest_event_window_leaderboard(event_window_id, refresh=False)
    return raw, True


def processed_status(session_ids: list[str]) -> dict[str, str]:
    """``Match.status`` for each session id that has a Match row."""
    if not session_ids:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(Match.session_id, Match.status).where(
                Match.session_id.in_(session_ids)
            )
        ).all()
    return {session_id: status for session_id, status in rows}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def load_windows(event_id: str) -> list[dict]:
    for tournament in fnapi.fetch_tournaments(region=None, include_historic=False):
        if tournament["eventId"] == event_id:
            windows = [
                {"id": w["eventWindowId"], "begin": w["beginTime"], "end": w["endTime"]}
                for w in tournament["eventWindows"]
            ]
            return sorted(windows, key=lambda w: _parse(w["begin"]))
    raise LookupError(f"{event_id} is not in fnapi's current tournament listing")


class LiveTournamentLoop:
    def __init__(self, event_id: str, args: argparse.Namespace):
        self.event_id = event_id
        self.args = args
        suffix = ".dryrun.json" if args.dry_run else ".json"
        self.state_path = STATE_DIR / f"{event_id}{suffix}"
        self.pipeline_log_dir = STATE_DIR / "pipeline"
        self.counter = install_paid_request_counter(forbid=args.dry_run)
        self.pipeline_proc: subprocess.Popen | None = None
        self.state = self._load_state()

    # -- state -------------------------------------------------------------

    def _load_state(self) -> dict:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            # Fold the previous process's own (probe) requests into the total.
            state["paid_requests"] += state.pop("paid_requests_this_process", 0)
            # A pipeline that was running when the loop died is re-run: its raw
            # logs are cached in S3, so this costs only the two window requests.
            for record in state["sessions"].values():
                if record["status"] == "processing":
                    record["status"] = "failed"
                    record["retry_at"] = _iso(_now())
            log(f"Resumed state from {self.state_path}")
        else:
            state = {
                "event_id": self.event_id,
                "sessions": {},
                "observations": [],
                "paid_requests": 0,
                "windows": [],
            }
        state["windows"] = load_windows(self.event_id)
        state.setdefault("leaderboard", {})
        return state

    def _save_state(self) -> None:
        self.state["paid_requests_this_process"] = self.counter.calls
        self.state["plan"] = self.plan().describe()
        self.state["updated_at"] = _iso(_now())
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.state_path)

    def plan(self) -> ProbePlan:
        return learn_plan(self.state["observations"], self.args.probe_step)

    def paid_total(self) -> int:
        return self.state["paid_requests"] + self.counter.calls

    def budget_left(self) -> bool:
        return self.paid_total() < self.args.max_paid_requests

    def _game_label(self, session_id: str) -> str:
        record = self.state["sessions"][session_id]
        same_window = sorted(
            (_parse(r["first_team_end"]), sid)
            for sid, r in self.state["sessions"].items()
            if r["window"] == record["window"]
        )
        number = [sid for _, sid in same_window].index(session_id) + 1
        return f"{record['window']} game {number} ({session_id[:8]})"

    # -- leaderboard -------------------------------------------------------

    def leaderboard_windows(self, now: datetime) -> list[str]:
        return [
            w["id"]
            for w in self.state["windows"]
            if _parse(w["begin"]) <= now <= _parse(w["end"]) + LEADERBOARD_GRACE
        ]

    def tick_leaderboards(self, now: datetime) -> None:
        polled = self.leaderboard_windows(now)
        for session_id, record in self.state["sessions"].items():
            if record["status"] == "live" and record["window"] not in polled:
                # Its leaderboard is no longer polled, so no winner will arrive.
                record.update(
                    status="waiting",
                    ended_at=record["last_team_end"],
                    end_source="stale",
                )
                log(f"⚠️  {self._game_label(session_id)} never showed a winner; treating as ended")
        for window in polled:
            lb_state = self.state["leaderboard"].get(window, {})
            prev_teams = lb_state.get("teams", 0)
            # fnapi occasionally returns a short page for one fetch (seen: 24 and
            # 48 of 50). Load a smaller leaderboard only once it persists for a
            # second tick — a glitch is skipped, a real DQ lands a minute later.
            min_teams = prev_teams if lb_state.get("shrink_ticks", 0) < 1 else 0
            try:
                raw, loaded = refresh_leaderboard(window, min_teams)
            except Exception as e:
                # A concurrent pipeline run ingesting the same leaderboard can
                # trip the unique indexes; the next tick simply retries.
                log(f"⚠️  Leaderboard refresh failed for {window}: {e!r}")
                self.state["leaderboard"][window] = {
                    **self.state["leaderboard"].get(window, {}),
                    "last_error": f"{_iso(now)} {e!r}",
                }
                continue
            if len(raw.entries) < min_teams:
                lb_state["shrink_ticks"] = lb_state.get("shrink_ticks", 0) + 1
                self.state["leaderboard"][window] = lb_state
                log(
                    f"LB {window}: short response ({len(raw.entries)} of {prev_teams} "
                    f"teams) — kept the previous DB copy; loading it if it persists"
                )
                continue
            self._track_sessions(window, raw.entries, now)
            live = sum(
                1 for r in self.state["sessions"].values()
                if r["window"] == window and r["status"] == "live"
            )
            games = sum(1 for r in self.state["sessions"].values() if r["window"] == window)
            self.state["leaderboard"][window] = {
                "last_ok": _iso(now),
                "teams": len(raw.entries),
                "shrink_ticks": 0,
                "loaded_to_db": loaded,
            }
            log(
                f"LB {window}: {len(raw.entries)} teams, {games} games seen "
                f"({live} live){'' if loaded else ' [S3 only: no event_windows row yet]'}"
                f" | paid requests: {self.paid_total()}"
            )

    def _track_sessions(self, window: str, entries: JsonList, now: datetime) -> None:
        sessions = self.state["sessions"]
        present = summarize_sessions(entries)
        self._track_voided(window, present)
        for session_id, summary in present.items():
            record = sessions.get(session_id)
            if record is None:
                record = sessions[session_id] = {
                    "window": window,
                    "status": "live",
                    "first_seen": _iso(now),
                    "first_team_end": summary["first_end"],
                    "teams": summary["teams"],
                    "last_change": _iso(now),
                    "ended_at": None,
                    "end_source": None,
                    "probes": [],
                    "listed_at": None,
                    "pipeline_runs": [],
                }
                log(f"🎮 New session on leaderboard: {self._game_label(session_id)}")
            if record["status"] == "voided":
                record["status"] = "live"
                log(f"↩️  {self._game_label(session_id)} is back on the leaderboard; tracking it again")
            record["last_team_end"] = summary["last_end"]
            if summary["teams"] != record["teams"]:
                record["teams"] = summary["teams"]
                record["last_change"] = _iso(now)
            if record["status"] != "live":
                continue
            if summary["winner_end"]:
                record.update(
                    status="waiting",
                    ended_at=summary["winner_end"],
                    end_source="winner",
                )
                log(
                    f"🏁 Finished: {self._game_label(session_id)} — winner at "
                    f"{summary['winner_end']}, {summary['teams']} teams reported. "
                    f"Plan: {self.plan().describe()}"
                )
            elif now - _parse(record["last_change"]) > STALE_SESSION_AFTER:
                record.update(
                    status="waiting",
                    ended_at=summary["last_end"],
                    end_source="stale",
                )
                log(
                    f"⚠️  {self._game_label(session_id)} has no winner entry and no "
                    f"new teams for {STALE_SESSION_AFTER}; treating it as ended at "
                    f"{summary['last_end']}"
                )

    def _track_voided(self, window: str, present: dict[str, dict]) -> None:
        """Stop chasing games Epic drops from the leaderboard (voided/cancelled).

        The pipeline skips such games anyway (``drop_unscored_matches``), so
        probing for them would only burn paid requests. A game must be missing
        on two consecutive ticks, and an empty leaderboard is ignored — both
        more likely a transient fnapi glitch than a void.
        """
        if not present:
            return
        for session_id, record in self.state["sessions"].items():
            if record["window"] != window or session_id in present:
                record.pop("missing_ticks", None)
                continue
            if record["status"] == "voided" or record.get("void_warned"):
                continue
            record["missing_ticks"] = record.get("missing_ticks", 0) + 1
            if record["missing_ticks"] < 2:
                continue
            if record["status"] == "done":
                record["void_warned"] = True
                log(
                    f"🚫 {self._game_label(session_id)} was already processed but is no "
                    f"longer on the leaderboard (voided?) — its match is still in the DB "
                    f"and must be removed by hand"
                )
                continue
            record["status"] = "voided"
            log(
                f"🚫 {self._game_label(session_id)} dropped off the leaderboard "
                f"(voided/cancelled); no longer probing or processing it"
            )

    # -- processing --------------------------------------------------------

    def reconcile_processed(self) -> None:
        pending = [
            sid for sid, r in self.state["sessions"].items()
            if r["status"] in ACTIVE_STATUSES | {"gave_up"}
        ]
        for session_id, status in processed_status(pending).items():
            record = self.state["sessions"][session_id]
            if status == STATUS_PROCESSED and record["status"] != "processing":
                record["status"] = "done"
                log(f"✅ Processed: {self._game_label(session_id)}")

    def probe(self, now: datetime) -> None:
        plan = self.plan()
        waiting = [
            (sid, r) for sid, r in self.state["sessions"].items()
            if r["status"] == "waiting"
        ]
        due_windows: set[str] = set()
        for session_id, record in waiting:
            due = next_probe_due(record, plan)
            if due is None:
                record["status"] = "gave_up"
                log(
                    f"🛑 Gave up on {self._game_label(session_id)}: not listed by "
                    f"Osirion {GIVE_UP_AFTER_S // 60} min after it ended. A later "
                    f"pipeline run will still pick it up if it appears."
                )
            elif due <= now:
                due_windows.add(record["window"])
            ended = _parse(record["ended_at"])
            if (
                not record.get("overdue_logged")
                and (now - ended).total_seconds() > plan.dense_until_s
            ):
                record["overdue_logged"] = True
                log(
                    f"⏳ {self._game_label(session_id)} not listed by Osirion "
                    f"+{plan.dense_until_s:.0f}s after it ended (slower than learned); "
                    f"still probing every {MAX_PROBE_GAP_S}s"
                )

        for window in sorted(due_windows):
            if not self.budget_left():
                log(f"💸 Paid-request budget ({self.args.max_paid_requests}) spent; not probing")
                return
            if self.args.dry_run:
                log(f"[dry-run] would probe Osirion match list for {window} (1 paid request)")
                continue
            try:
                matches = osr.fetch_event_window_matches(window)
            except Exception as e:
                log(f"⚠️  Probe failed for {window}: {e!r}")
                # Record the attempt so the schedule advances — otherwise a
                # persistent API error re-probes (and is billed) every second.
                failed_at = _iso(_now())
                for _, record in waiting:
                    if record["window"] == window and record["status"] == "waiting":
                        record["probes"].append({"at": failed_at, "found": False, "error": True})
                continue
            listed = {(m.get("info") or {}).get("serverId") for m in matches} - {None}
            probed_at = _iso(_now())
            # One probe answers for every waiting session of the window.
            for session_id, record in waiting:
                if record["window"] != window or record["status"] != "waiting":
                    continue
                found = session_id in listed
                record["probes"].append({"at": probed_at, "found": found})
                if found:
                    self._record_availability(session_id, record, now, plan)
            if any(
                r["window"] == window and r["status"] == "listed"
                for r in self.state["sessions"].values()
            ):
                self.start_pipeline(window, now)
            else:
                log(f"🔎 Probed {window}: not listed yet ({len(listed)} matches listed)")

    def _record_availability(
        self, session_id: str, record: dict, now: datetime, plan: ProbePlan
    ) -> None:
        ended = _parse(record["ended_at"])
        # Errored probes say nothing about availability; only true misses bound it.
        misses = [p for p in record["probes"] if not p["found"] and not p.get("error")]
        upper_s = (now - ended).total_seconds()
        lower_s = max(
            ((_parse(p["at"]) - ended).total_seconds() for p in misses), default=0.0
        )
        record["status"] = "listed"
        record["listed_at"] = _iso(now)
        # Only learn from probes that ran on schedule; a hit on the first probe
        # after a restart (game ended long ago) says nothing about the delay.
        on_schedule = bool(misses) or upper_s <= plan.start_s + self.args.interval + plan.step_s
        if on_schedule and record["end_source"] == "winner":
            self.state["observations"].append({
                "session_id": session_id,
                "lower_s": round(lower_s),
                "upper_s": round(upper_s),
            })
        log(
            f"📡 Osirion lists {self._game_label(session_id)}: available "
            f"{lower_s:.0f}–{upper_s:.0f}s after the game ended "
            f"({len(record['probes'])} probes){'' if on_schedule else ' [not learned: off-schedule]'}"
        )

    def start_pipeline(self, window: str, now: datetime) -> None:
        if self.pipeline_proc is not None:
            return
        if self.args.dry_run:
            log(f"[dry-run] would run process_event_window({window!r}, refresh=True)")
            return
        if not self.budget_left():
            log(f"💸 Paid-request budget ({self.args.max_paid_requests}) spent; not running pipeline")
            return
        self.pipeline_log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.pipeline_log_dir / f"{window}-{now.strftime('%Y%m%dT%H%M%SZ')}.log"
        with open(log_path, "w") as log_file:
            self.pipeline_proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "etl.jobs.live_tournament", "--run-pipeline", window],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=REPO_ROOT,
            )
        self.pipeline_window = window
        self.pipeline_log_path = log_path
        for record in self.state["sessions"].values():
            if record["window"] == window and record["status"] in {"listed", "failed"}:
                record["status"] = "processing"
                record["pipeline_runs"].append({"started": _iso(now), "log": str(log_path)})
        self.state["pipeline"] = {"window": window, "pid": self.pipeline_proc.pid, "log": str(log_path)}
        log(f"⚙️  Pipeline started for {window} (pid {self.pipeline_proc.pid}) → {log_path}")

    def poll_pipeline(self, now: datetime) -> None:
        if self.pipeline_proc is None or self.pipeline_proc.poll() is None:
            return
        exit_code = self.pipeline_proc.returncode
        window = self.pipeline_window
        self.pipeline_proc = None
        self.state.pop("pipeline", None)

        text = self.pipeline_log_path.read_text(errors="replace")
        paid = re.search(r"^PAID_REQUESTS=(\d+)$", text, re.MULTILINE)
        result = re.search(r"^PIPELINE_RESULT=(.*)$", text, re.MULTILINE)
        self.state["paid_requests"] += int(paid.group(1)) if paid else 0
        log(
            f"⚙️  Pipeline for {window} exited {exit_code}: "
            f"{result.group(1) if result else 'no result (crashed?)'}, "
            f"{paid.group(1) if paid else '?'} paid requests"
        )

        statuses = processed_status([
            sid for sid, r in self.state["sessions"].items() if r["status"] == "processing"
        ])
        for session_id, record in self.state["sessions"].items():
            if record["status"] != "processing":
                continue
            record["pipeline_runs"][-1].update(finished=_iso(now), exit_code=exit_code)
            if statuses.get(session_id) == STATUS_PROCESSED:
                record["status"] = "done"
                ended = _parse(record["ended_at"])
                log(
                    f"✅ Processed: {self._game_label(session_id)} — live in DB "
                    f"{(now - ended).total_seconds() / 60:.1f} min after the game ended"
                )
            elif len(record["pipeline_runs"]) >= MAX_PIPELINE_RUNS:
                record["status"] = "gave_up"
                log(
                    f"🛑 {self._game_label(session_id)} still not processed after "
                    f"{MAX_PIPELINE_RUNS} pipeline runs (DB status: "
                    f"{statuses.get(session_id)}); see {self.pipeline_log_path}"
                )
            else:
                record["status"] = "failed"
                record["retry_at"] = _iso(now + PIPELINE_RETRY_AFTER)
                log(
                    f"❌ {self._game_label(session_id)} not processed (DB status: "
                    f"{statuses.get(session_id)}); retrying at {record['retry_at']}. "
                    f"Log: {self.pipeline_log_path}"
                )
        self.reconcile_processed()

    def start_pending_pipeline(self, now: datetime) -> None:
        """Run the pipeline for a listed game (e.g. left unstarted by a restart)
        or a failed one whose retry is due."""
        for record in self.state["sessions"].values():
            if record["status"] == "listed" or (
                record["status"] == "failed" and _parse(record["retry_at"]) <= now
            ):
                self.start_pipeline(record["window"], now)
                return

    # -- driver ------------------------------------------------------------

    def event_complete(self, now: datetime) -> bool:
        last_end = max(_parse(w["end"]) for w in self.state["windows"])
        busy = any(r["status"] in ACTIVE_STATUSES for r in self.state["sessions"].values())
        return now > last_end + LEADERBOARD_GRACE and not busy and self.pipeline_proc is None

    def next_wake(self, now: datetime, next_leaderboard: datetime) -> datetime:
        wake = next_leaderboard
        if self.pipeline_proc is not None:
            wake = min(wake, now + timedelta(seconds=5))
        plan = self.plan()
        for record in self.state["sessions"].values():
            if record["status"] == "waiting":
                due = next_probe_due(record, plan)
                if due is not None:
                    wake = min(wake, due)
            elif record["status"] == "failed":
                wake = min(wake, _parse(record["retry_at"]))
        return max(wake, now + timedelta(seconds=1))

    def run(self) -> None:
        windows = ", ".join(f"{w['id']} [{w['begin']} → {w['end']}]" for w in self.state["windows"])
        log(f"Live loop for {self.event_id}: {windows}")
        log(f"Initial plan: {self.plan().describe()}; budget {self.args.max_paid_requests} paid requests")
        next_leaderboard = _now()
        while True:
            now = _now()
            if now >= next_leaderboard:
                self.tick_leaderboards(now)
                next_leaderboard = now + timedelta(seconds=self.args.interval)
            try:
                self.reconcile_processed()
                self.poll_pipeline(now)
                if self.pipeline_proc is None:
                    self.probe(now)
                if self.pipeline_proc is None:
                    self.start_pending_pipeline(now)
            except Exception as e:
                log(f"⚠️  Processing step failed: {e!r}")
                traceback.print_exc()
            self._save_state()

            if self.args.once:
                return
            if self.event_complete(now):
                gave_up = [self._game_label(s) for s, r in self.state["sessions"].items() if r["status"] == "gave_up"]
                log(
                    f"EVENT COMPLETE — {self.paid_total()} paid requests; "
                    f"unprocessed: {gave_up or 'none'}"
                )
                return
            wake = self.next_wake(_now(), next_leaderboard)
            time.sleep(max(0.0, (wake - _now()).total_seconds()))


def run_pipeline_subprocess(event_window_id: str) -> int:
    """Entry point for the pipeline subprocess: the ``--refresh`` pipeline for
    one window, reporting its paid-request count for the parent's budget."""
    counter = install_paid_request_counter()
    exit_code = 1
    try:
        result = process_event_window(event_window_id, refresh=True)
        print(f"PIPELINE_RESULT={json.dumps(result)}")
        exit_code = 0 if result.get("failed") == 0 else 1
    finally:
        print(f"PAID_REQUESTS={counter.calls}", flush=True)
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event-id", help="fnapi eventId, e.g. epicgames_MannekenPis_Official")
    parser.add_argument("--interval", type=int, default=60, help="Leaderboard refresh period (s).")
    # 60s: Osirion listing delays vary 10–25 min, so the learned window is wide;
    # 30s spacing across it cost ~30 requests/game and would exhaust the budget.
    parser.add_argument("--probe-step", type=int, default=60, help="Dense probe spacing (s).")
    parser.add_argument(
        "--max-paid-requests", type=int, default=1000,
        help="Hard cap on paid Osirion requests (probes + pipeline), persisted across restarts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Never make a paid Osirion request.")
    parser.add_argument("--once", action="store_true", help="Run a single tick and exit.")
    parser.add_argument("--run-pipeline", metavar="EVENT_WINDOW_ID", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.run_pipeline:
        sys.exit(run_pipeline_subprocess(args.run_pipeline))
    if not args.event_id:
        parser.error("--event-id is required")
    LiveTournamentLoop(args.event_id, args).run()
