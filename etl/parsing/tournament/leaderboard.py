"""Shape a window's raw leaderboard into DB rows.

Consumes the leaderboard ``entries`` (:func:`etl.api.fnapi_osirion_client.
fetch_tournament_leaderboard`) plus the window's ``scoringRules`` and returns
rows for two tables:

* ``event_window_teams`` — one summary row per team: the API's authoritative
  final rank/points/score for the window, plus matches/wins/kills.
* ``event_window_team_matches`` — one row per (team, game) with the raw per-game
  stats and the points computed by :mod:`etl.parsing.tournament.scoring`.

Each team's ``sessionHistory`` is ordered by ``endTime`` to assign a 1-based
``game_number`` — the per-window match index the leaderboard's match buttons map
to. A tournament-wide (multi-window) "games 1..N" view is a downstream
aggregation of the match rows across the tournament's windows, ordered by
``end_time``; the API has no combined leaderboard to store.
"""

from datetime import datetime

from etl.types import JsonDict, JsonList
from etl.parsing.tournament.scoring import (
    PLACEMENT_STAT,
    TEAM_ELIMS_STAT,
    game_points_breakdown,
)


VICTORY_ROYALE_STAT = "VICTORY_ROYALE_STAT"
TIME_ALIVE_STAT = "TIME_ALIVE_STAT"
PLACEMENT_TIEBREAKER_STAT = "PLACEMENT_TIEBREAKER_STAT"


def canonical_team_key(team_id: str) -> str:
    """Order-independent identity for a team's exact set of players.

    The API ``teamId`` is the members' account IDs joined by ':'. Sorting makes
    the same set of players map to one key regardless of ordering, so results
    can later be grouped across tournaments. Derived from ``teamId`` (not
    ``players``) because privacy-hidden members are dropped from ``players`` but
    remain in ``teamId``.
    """
    return ":".join(sorted(team_id.split(":")))


def _parse_end_time(value: str) -> datetime:
    # API timestamps are UTC ISO-8601 with a trailing 'Z'; store naive (UTC) to
    # match the pipeline's other DateTime columns.
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def build_leaderboard_rows(
    entries: JsonList,
    scoring_rules: JsonList,
    event_window_id: str,
    match_point_rule: JsonDict | None = None,
) -> tuple[JsonList, JsonList]:
    """Return ``(team_rows, team_match_rows)`` for one event window.

    ``match_point_rule`` (``{"threshold", "bonus"}``, EWC-only, from the cached
    scoring.json) adds a flat bonus to the game in which a team already at or
    above ``threshold`` cumulative points wins — the "match point" clinch. It is
    not a per-game ``*_INDEX`` scoring rule (it depends on the running total), so
    it lands in its own ``match_point_bonus`` column rather than in
    placement/elim points. When the rule is set, every team's per-game points are
    asserted to reconcile to the API's authoritative ``pointsEarned``.
    """
    team_rows: JsonList = []
    team_match_rows: JsonList = []

    for entry in entries:
        team_id = entry["teamId"]
        sessions = sorted(
            entry["sessionHistory"], key=lambda s: _parse_end_time(s["endTime"])
        )

        wins = 0
        kills = 0
        running_points = 0  # this team's cumulative points *before* the game below
        for game_number, session in enumerate(sessions, start=1):
            stats = session["trackedStats"]
            breakdown = game_points_breakdown(stats, scoring_rules)

            team_elims = int(stats.get(TEAM_ELIMS_STAT, 0))
            victory_royale = bool(stats.get(VICTORY_ROYALE_STAT, 0))
            wins += int(victory_royale)
            kills += team_elims

            # Match-point clinch: a win while already at/above the threshold
            # earns the bonus (and, in-game, the tournament). Checked against the
            # standing *before* this game, matching how a team "reaches match
            # point" and then must win a subsequent game.
            match_point_bonus = 0
            if (
                match_point_rule is not None
                and victory_royale
                and running_points >= match_point_rule["threshold"]
            ):
                match_point_bonus = match_point_rule["bonus"]

            total_points = sum(breakdown.values()) + match_point_bonus
            running_points += total_points

            team_match_rows.append({
                "event_window_id": event_window_id,
                "team_id": team_id,
                "session_id": session["sessionId"],
                "game_number": game_number,
                "end_time": _parse_end_time(session["endTime"]),
                "placement": stats.get(PLACEMENT_STAT),
                "team_elims": team_elims,
                "victory_royale": victory_royale,
                "time_alive": stats.get(TIME_ALIVE_STAT),
                "placement_tiebreaker": stats.get(PLACEMENT_TIEBREAKER_STAT),
                "placement_points": breakdown.get(PLACEMENT_STAT, 0),
                "elim_points": breakdown.get(TEAM_ELIMS_STAT, 0),
                "match_point_bonus": match_point_bonus,
                "total_points": total_points,
            })

        # With a match-point rule in play, the per-game points (bonus included)
        # must sum to the API's authoritative total; a mismatch means our bonus
        # modelling is wrong for this event. Scoped to configured windows so it
        # never trips normal tournaments.
        if match_point_rule is not None and running_points != entry["pointsEarned"]:
            raise ValueError(
                f"Match-point reconciliation failed for team {team_id} in "
                f"{event_window_id}: computed {running_points} != API "
                f"{entry['pointsEarned']}."
            )

        team_rows.append({
            "event_window_id": event_window_id,
            "team_id": team_id,
            "team_key": canonical_team_key(team_id),
            "final_rank": entry["rank"],
            "final_points": entry["pointsEarned"],
            "final_score": entry["score"],
            "percentile": entry["percentile"],
            "matches": len(sessions),
            "wins": wins,
            "kills": kills,
        })

    return team_rows, team_match_rows


def build_leaderboard_player_rows(
    entries: JsonList,
    event_window_id: str,
) -> JsonList:
    """Return ``player_rows`` for one event window's ``event_window_players``.

    One dict per player present in the leaderboard's ``players`` arrays: their
    display name (``None`` when the API reports ``username: null``) and raw
    ``flagToken`` (``None`` when unset). Privacy-hidden members are omitted from
    ``players`` by the API, so this yields one row per *visible* player rather
    than per account id in ``teamId``.
    """
    player_rows: JsonList = []
    for entry in entries:
        for player in entry.get("players", []):
            player_rows.append({
                "event_window_id": event_window_id,
                "epic_id": player["accountId"],
                "epic_username": player.get("username"),
                "flag_token": player.get("flagToken"),
            })
    return player_rows
