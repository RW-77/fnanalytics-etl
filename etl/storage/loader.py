import os

from etl.storage.s3_client import S3TournamentObjectStore
from etl.types import ParsedTimelineData

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")
TIMELINE_PROGRESS_EVERY = int(os.getenv("TIMELINE_PROGRESS_EVERY", "25"))


def _format_mib(byte_count: int) -> str:
    return f"{byte_count / (1024 * 1024):.1f} MiB"


def load_match_timeline(parsed: ParsedTimelineData, event_window_id):
    """Upload a match's timeline without ever leaving it unplayable.

    Chunks overwrite the previous build's in place, and metadata.json (which
    carries timeline_version) is written last, so a failure part-way leaves
    the previous timeline in place, with some chunks already rebuilt, and the
    next run retries. Only after everything is up are chunks past the new
    build's end deleted.
    """
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    total_chunks = len(parsed.frame_chunks)
    total_bytes = sum(int(frame_chunk.nbytes) for frame_chunk in parsed.frame_chunks)

    print(
        f"Uploading timeline for match {parsed.match_id}: "
        f"{total_chunks} chunks, {_format_mib(total_bytes)} total"
    )

    for chunk_number, frame_chunk in enumerate(parsed.frame_chunks):
        bucket.put_match_movement_chunk(parsed.match_id, chunk_number, frame_chunk)
        uploaded = chunk_number + 1
        if (
            uploaded == total_chunks
            or uploaded == 1
            or uploaded % max(TIMELINE_PROGRESS_EVERY, 1) == 0
        ):
            print(f"  - Uploaded movement chunk {uploaded}/{total_chunks}")

    bucket.put_match_zones(parsed.match_id, parsed.zone_phases)
    print(f"  - Uploaded zones ({len(parsed.zone_phases)} phases)")
    bucket.put_match_shots(parsed.match_id, parsed.shots)
    print(f"  - Uploaded shots ({len(parsed.shots)} shots)")
    bucket.put_match_metadata(parsed.match_id, parsed.metadata)

    deleted = bucket.delete_match_movement_chunks(parsed.match_id, keep_first=total_chunks)
    if deleted:
        print(f"  - Deleted {deleted} stale movement chunks past chunk {total_chunks - 1}")
    print(f"✅ Uploaded timeline for match {parsed.match_id}")


def load_match_engagements(asset: dict) -> None:
    """Upload a match's engagements file (see build_engagements_asset). One
    object, overwritten on every run, so there is nothing stale to clean up."""
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    bucket.put_match_engagements(asset["match_id"], asset)
    print(f"  - Uploaded engagements ({asset['engagement_count']} engagements)")


def load_match_inventory(asset: dict) -> None:
    """Upload a match's inventory file (see build_inventory_asset). One
    object, overwritten on every run, so there is nothing stale to clean up."""
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    bucket.put_match_inventory(asset["match_id"], asset)
    changes = sum(len(c) for c in asset["players"].values())
    print(f"  - Uploaded inventory ({len(asset['players'])} players, {changes} changes)")
