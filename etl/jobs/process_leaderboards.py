"""Standalone leaderboard ingestion — process tournament leaderboards without
the match pipeline.

Leaderboards are independent of match parsing (their per-game stats come
pre-aggregated from the leaderboard endpoint) and exist for far more tournaments
than are deep-parsed. This job (re)ingests leaderboards for a chosen set of event
windows on demand. Window metadata is ingested first so the EventWindow row the
leaderboard rows reference exists.
"""

import argparse
import traceback

from etl.jobs.process_tournaments import (
    ingest_event_window_metadata,
    ingest_event_window_leaderboard,
)


def process_leaderboards(
    event_window_ids: list[str], refresh: bool = False
) -> dict[str, str]:
    results: dict[str, str] = {}
    for event_window_id in event_window_ids:
        try:
            ingest_event_window_metadata(event_window_id, refresh=refresh)
            ingest_event_window_leaderboard(event_window_id, refresh=refresh)
            results[event_window_id] = "ok"
        except LookupError as e:
            # Expected for events older than fnapi's rolling listing — skip cleanly.
            print(f"⏭️  Skipping {event_window_id}: {e}")
            results[event_window_id] = "skipped (not in fnapi)"
        except Exception as e:
            print(f"❌ Leaderboard ingest failed for {event_window_id}: {e}")
            traceback.print_exc()
            results[event_window_id] = f"error: {e}"
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest tournament leaderboards for a set of event windows."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-pull from fnapi, ignoring the S3 cache (use for live windows).",
    )
    args = parser.parse_args()

    '''
    event_window_ids: list[str] = [
        "S33_FNCSMajor1_Final_Day1_EU",
        "S33_FNCSMajor1_Final_Day2_EU",
        "S33_FNCSMajor1_Final_Day1_NAC",
        "S33_FNCSMajor1_Final_Day2_NAC",
        "S34_FNCSMajor2_Final_Day1_EU",
        "S34_FNCSMajor2_Final_Day2_EU",
        "S34_FNCSMajor2_Final_Day1_NAC",
        "S34_FNCSMajor2_Final_Day2_NAC",
        "S36_FNCSMajor3_Final_Day1_EU",
        "S36_FNCSMajor3_Final_Day2_EU",
        "S36_FNCSMajor3_Final_Day1_NAC",
        "S36_FNCSMajor3_Final_Day2_NAC",
        "Dinosauron_Day1",
        "Dinosauron_Day2",
        "S39_FNCSDivisionalCup_Division1_Week6Final_EU",
        "S39_FNCSDivisionalCup_Division1_Week6Final_NAC",
        "S40_FNCSMajor1_Final_Day1_EU",
        "S40_FNCSMajor1_Final_Day2_EU",
        "S40_FNCSMajor1_Final_Day1_NAC",
        "S40_FNCSMajor1_Final_Day2_NAC",
        "S40_FNCSDivisionalCup_Division1_Week3Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week3Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week4Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week4Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week5Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week5Final_NAC",
        "S40_FNCSDivisionalCup_Division1_Week2Final_EU",
        "S40_FNCSDivisionalCup_Division1_Week2Final_NAC",
        "Bratwurst_Finals_Day3",
        "S41_PerformanceEvaluation_Event1Round2_EU",
        "S41_PerformanceEvaluation_Event1Round2_NAC",
        "S41_FNCSDivisionalCup_Division1_Week1Final_EU",
        "S41_FNCSDivisionalCup_Division1_Week1Final_NAC",
        "S41_PerformanceEvaluation_Event2Round2_EU",
        "S41_PerformanceEvaluation_Event2Round2_NAC",
        "S41_FNCSDivisionalCup_Division1_Week2Final_EU",
        "S41_FNCSDivisionalCup_Division1_Week2Final_NAC",
        "S41_FNCSDivisionalCup_Division1_Week3Final_EU",
        "S41_FNCSDivisionalCup_Division1_Week3Final_NAC",
        "S41_PerformanceEvaluation_Event4Round2_NAC",
        "S41_PerformanceEvaluation_Event4Round2_EU",
        "S41_FNCSDivisionalCup_Division1_Week4Final_NAC",
        "S41_FNCSDivisionalCup_Division1_Week4Final_EU",
        "S41_PerformanceEvaluation_Event5Round2_EU",
        "S41_PerformanceEvaluation_Event5Round2_NAC",
        "S41_PerformanceEvaluation_Event6Round2_EU",
        "S41_PerformanceEvaluation_Event6Round2_NAC",
        "S41_FNCSMajor2_Final_Day1_EU",
        "S41_FNCSMajor2_Final_Day1_NAC",
        "S41_FNCSMajor2_Final_Day2_EU",
        "S41_FNCSMajor2_Final_Day2_NAC",
        "S41_PerformanceEvaluation_Event9Round2_EU",
        "S41_PerformanceEvaluation_Event9Round2_NAC",
        "S41_FNCSLastChanceMajor_Final_EU",
        "S41_FNCSLastChanceMajor_Final_NAC",
    ]
    '''

    event_window_ids = [
        "S42_FNCSDivisionalCup_Division1_Week1Final_EU",
                        ]


    results = process_leaderboards(event_window_ids, refresh=args.refresh)

    print("\nLeaderboard summary:")
    for event_window_id, status in results.items():
        print(f"- {event_window_id}: {status}")
