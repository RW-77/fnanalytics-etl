"""
Run engagement detection + evaluation on one match and write the result as a
JSON fixture for the website's replay-lab prototype.

Usage:
    python scripts/dump_engagement_fixture.py <match_id> [--output-dir DIR]

Writes <output-dir>/engagements-<match_id>.json. By default <output-dir> is the
sibling website repo's lib/replay/fixtures/.

Read-only: raw logs come from S3 via get_raw_match_data (no Osirion fetch, no S3
or DB writes), so it is safe to run while a pipeline job is live. The match's
raw logs must already be in S3.

The fixture is the same JSON the pipeline uploads to
replays/matches/<match_id>/engagements.json (see build_engagements_asset).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from etl.fetching.match_data_fetching import get_raw_match_data
from etl.parsing.match.context import MatchContext
from etl.parsing.match.clustering.engagements import build_engagements_asset, SegmentParams


DEFAULT_OUTPUT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "fortnite-stats-website", "lib", "replay", "fixtures",
)


def dump(match_id: str, output_dir: str, params: SegmentParams) -> str:
    print(f"[{match_id}] reading raw logs from S3 …")
    raw = get_raw_match_data(match_id)
    ctx = MatchContext(raw)

    print(f"[{match_id}] detecting engagements …")
    fixture = build_engagements_asset(ctx, params)
    engagements = fixture["engagements"]

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"engagements-{match_id}.json")
    with open(path, "w") as f:
        json.dump(fixture, f)

    records = sum(len(e["records"]) for e in engagements)
    print(
        f"[{match_id}] {len(engagements)} engagements, {records} records, "
        f"{os.path.getsize(path) / 1024:.0f} KB\n"
        f"           -> {os.path.relpath(path)}"
    )
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Write one match's engagements as a replay-lab JSON fixture."
    )
    parser.add_argument("match_id", help="Match ID (raw logs must already be in S3)")
    parser.add_argument(
        "--output-dir", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help="Directory for the fixture (default: <website>/lib/replay/fixtures)",
    )
    args = parser.parse_args()
    dump(args.match_id, args.output_dir, SegmentParams())


if __name__ == "__main__":
    main()
