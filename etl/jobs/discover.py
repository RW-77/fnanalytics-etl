import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from etl.api.osirion_client import fetch_tournaments


DEFAULT_INTERVAL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_PAGE_SIZE = 100
SECONDS_PER_DAY = 86400
DEFAULT_OUTPUT_DIR = Path("data") / "tournaments"


def fetch_recent_tournaments(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    include_progress: bool = True,
    limit: int = DEFAULT_PAGE_SIZE,
) -> list[dict[str, Any]]:
    return fetch_tournaments(
        interval_seconds=interval_seconds,
        include_progress=include_progress,
        limit=limit,
    )


def dedupe_tournaments_by_event_window(tournaments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}

    for tournament in tournaments:
        event_window_id = tournament.get("eventWindowId")
        if isinstance(event_window_id, str) and event_window_id not in unique:
            unique[event_window_id] = tournament

    return list(unique.values())


def resolve_output_path(out: str | Path | None, interval_seconds: int) -> Path:
    """Resolve the destination file for discovered tournaments.

    A directory (or ``None``) yields a timestamped filename inside it; an
    explicit ``.json`` path is used as-is.
    """
    if out is None:
        out_path = DEFAULT_OUTPUT_DIR
    else:
        out_path = Path(out)

    if out_path.suffix.lower() == ".json":
        return out_path

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    days = round(interval_seconds / SECONDS_PER_DAY)
    return out_path / f"tournaments_{days}d_{timestamp}.json"


def discover_recent_tournaments(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    include_progress: bool = True,
    limit: int = DEFAULT_PAGE_SIZE,
    out: str | Path | None = None,
) -> Path | None:
    tournaments = fetch_recent_tournaments(
        interval_seconds=interval_seconds,
        include_progress=include_progress,
        limit=limit,
    )
    unique_tournaments = dedupe_tournaments_by_event_window(tournaments)

    if not unique_tournaments:
        print("No tournaments found in the requested interval.")
        return None

    output_path = resolve_output_path(out, interval_seconds)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(unique_tournaments, f, indent=2)

    print(
        f"Found {len(unique_tournaments)} unique event windows from the last "
        f"{interval_seconds / SECONDS_PER_DAY:.1f} day(s)."
    )
    print(f"Wrote {output_path}")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m etl.jobs.discover",
        description="Discover recent Fortnite tournaments / event windows from Osirion.",
    )
    interval = parser.add_mutually_exclusive_group()
    interval.add_argument(
        "--days",
        type=float,
        help="Look back this many days (default: 7). Use 30 for the last month.",
    )
    interval.add_argument(
        "--interval-seconds",
        type=int,
        help="Look back this many seconds (takes precedence over --days).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size per API request (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Exclude progress data from the results.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Output directory or .json file path "
            f"(default: a timestamped file in {DEFAULT_OUTPUT_DIR}/)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if args.interval_seconds is not None:
        interval_seconds = args.interval_seconds
    elif args.days is not None:
        interval_seconds = int(args.days * SECONDS_PER_DAY)
    else:
        interval_seconds = DEFAULT_INTERVAL_SECONDS

    discover_recent_tournaments(
        interval_seconds=interval_seconds,
        include_progress=not args.no_progress,
        limit=args.limit,
        out=args.out,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}")
