import os

from etl.storage.s3_client import S3TournamentObjectStore
from etl.types import ParsedTimelineData

OBJECTS_BUCKET = os.getenv("TOURNAMENT_OBJECTS_BUCKET", "fortnite-tournament-objects")
TIMELINE_PROGRESS_EVERY = int(os.getenv("TIMELINE_PROGRESS_EVERY", "25"))


def _format_mib(byte_count: int) -> str:
    return f"{byte_count / (1024 * 1024):.1f} MiB"


def load_match_timeline(parsed: ParsedTimelineData, event_window_id):
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    total_chunks = len(parsed.frame_chunks)
    total_bytes = sum(int(frame_chunk.nbytes) for frame_chunk in parsed.frame_chunks)

    deleted = bucket.delete_match_movement_chunks(parsed.match_id)
    if deleted:
        print(f"Deleted {deleted} stale movement chunks for match {parsed.match_id}")

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

    bucket.put_match_metadata(parsed.match_id, parsed.metadata)
    bucket.put_match_zones(parsed.match_id, parsed.zone_phases)
    print(f"  - Uploaded zones ({len(parsed.zone_phases)} phases)")
    bucket.put_match_shots(parsed.match_id, parsed.shots)
    print(f"  - Uploaded shots ({len(parsed.shots)} shots)")
    print(f"✅ Uploaded timeline for match {parsed.match_id}")


def cleanup_match_timeline(match_id: str) -> int:
    """Delete any movement chunks stored for *match_id*.

    Used to remove orphaned objects left behind by a failed upload. Returns
    the number of chunks deleted.
    """
    bucket = S3TournamentObjectStore(bucket=OBJECTS_BUCKET)
    return bucket.delete_match_movement_chunks(match_id)
