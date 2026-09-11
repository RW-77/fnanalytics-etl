import io
import boto3
import json
import numpy as np
import os
from botocore.config import Config
from botocore.exceptions import ClientError


class ObjectWrapper:
    """Encapsulates S3 object actions."""

    def __init__(self, s3_object):
        """
        Params:
            s3_object: A Boto3 Object resource.
        """
        self.object = s3_object
        self.key = self.object.key


    def get(self):
        """
        Returns the object data in bytes.
        """
        try:
            body = self.object.get()["Body"].read()
            print()
        except:
           raise


    def delete(self):
        """
        Deletes the object.
        """
        try:
            self.object.delete()
            self.object.wait_until_not_exists()
            print(
                "Deleted object '%s' from bucket '%s'.",
                self.object.key,
                self.object.bucket_name,
            )
        except ClientError:
            print(
                "Couldn't delete object '%s' from bucket '%s'.",
                self.object.key,
                self.object.bucket_name,
            )
            raise


class S3DataStore:

    def __init__(self, bucket: str, s3_client = None):
        self.bucket = bucket
        self.s3 = s3_client or boto3.client("s3", config=_build_s3_config())

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise


    def get_json(self, key):
        obj = self.s3.get_object(Bucket=self.bucket, Key=key)
        return json.loads(obj["Body"].read())


    def put_json(self, key, value) -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(value).encode("utf-8"),
            ContentType="application/json",
        )


    def get_npy(self, key):
        obj = self.s3.get_object(Bucket=self.bucket, Key=key)
        buffer = io.BytesIO(obj["Body"].read())
        return np.load(buffer, allow_pickle=False)


    def put_npy(self, key, value) -> None:
        buffer = io.BytesIO()
        np.save(buffer, value)
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=buffer.getvalue(),
            ContentType="application/octet-stream",
        )

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def delete_prefix(self, prefix: str) -> int:
        """Delete all objects under a prefix. Returns the number of objects deleted."""
        deleted = 0
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            self.s3.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
            deleted += len(objects)
        return deleted


def _build_s3_config() -> Config:
    return Config(
        connect_timeout=int(os.getenv("S3_CONNECT_TIMEOUT_SECONDS", "10")),
        read_timeout=int(os.getenv("S3_READ_TIMEOUT_SECONDS", "60")),
        retries={
            "max_attempts": int(os.getenv("S3_MAX_ATTEMPTS", "5")),
            "mode": "standard",
        },
        tcp_keepalive=True,
    )


class S3TournamentLogStore:

    def __init__(self, bucket: str, s3_client = None):
        self.store = S3DataStore(bucket=bucket, s3_client=s3_client)


    def put_match_log(self, match_id: str, type: str, value):
        key = f"matches/{match_id}/{type}.json"
        self.store.put_json(key, value)


    def put_event_window_log(self, event_window_id: str, type: str, value):
        key = f"event_windows/{event_window_id}/{type}.json"
        self.store.put_json(key, value)


    def get_match_log(self, match_id: str, type):
        key = f"matches/{match_id}/{type}.json"
        return self.store.get_json(key)


    def get_event_window_log(self, event_window_id: str, type):
        key = f"event_windows/{event_window_id}/{type}.json"
        return self.store.get_json(key)


    def contains_match_log(self, match_id: str, type: str) -> bool:
        key = f"matches/{match_id}/{type}.json"
        return self.store.exists(key)


    def contains_event_window_log(self, event_window_id: str, type: str) -> bool:
        key = f"event_windows/{event_window_id}/{type}.json"
        return self.store.exists(key)


class S3TournamentObjectStore:

    def __init__(self, bucket: str, s3_client = None):
        self.store = S3DataStore(bucket=bucket, s3_client=s3_client)

    def delete_match_movement_chunks(self, match_id: str) -> int:
        prefix = f"replays/matches/{match_id}/movement/"
        return self.store.delete_prefix(prefix)

    def put_match_movement_chunk(self, match_id: str, number: int, data) -> None:
        key = f"replays/matches/{match_id}/movement/{number:05d}.npy"
        self.store.put_npy(key, data)

    def put_match_metadata(self, match_id: str, value) -> None:
        key = f"replays/matches/{match_id}/metadata.json"
        self.store.put_json(key, value)

    def put_match_zones(self, match_id: str, value) -> None:
        key = f"replays/matches/{match_id}/zones.json"
        self.store.put_json(key, value)

    def put_match_shots(self, match_id: str, value) -> None:
        key = f"replays/matches/{match_id}/shots.json"
        self.store.put_json(key, value)

    # ------------------------------------------------------------------
    # Map catalog
    # ------------------------------------------------------------------

    def put_map_snapshot(self, timestamp_str: str, maps: list) -> str:
        """Store a full timestamped snapshot of all map modes.

        timestamp_str should be an ISO-8601 string safe for S3 keys,
        e.g. '2026-06-09T14-00-00'.
        """
        key = f"maps/snapshots/{timestamp_str}.json"
        self.store.put_json(key, maps)
        return key

    def put_map_image(
        self,
        build_major: int,
        build_minor: int,
        mode_id: str,
        data: bytes,
        content_type: str = "image/webp",
    ) -> str:
        key = f"maps/versions/{build_major}.{build_minor:02d}/{mode_id}.webp"
        self.store.put_bytes(key, data, content_type=content_type)
        return key

    def put_map_definition(
        self,
        build_major: int,
        build_minor: int,
        mode_id: str,
        definition: dict,
    ) -> str:
        key = f"maps/versions/{build_major}.{build_minor:02d}/{mode_id}.json"
        self.store.put_json(key, definition)
        return key

    # ------------------------------------------------------------------
    # Weapon catalog
    # ------------------------------------------------------------------

    def put_weapon_image(self, weapon_id: str, data: bytes, content_type: str = "image/webp") -> str:
        key = f"weapons/images/{weapon_id}.webp"
        self.store.put_bytes(key, data, content_type=content_type)
        return key

    def put_weapon_small_image(self, weapon_id: str, data: bytes, content_type: str = "image/webp") -> str:
        key = f"weapons/images/small/{weapon_id}.webp"
        self.store.put_bytes(key, data, content_type=content_type)
        return key

    def put_weapon_snapshot(self, timestamp_str: str, weapons: list) -> str:
        """Store a full timestamped snapshot of the weapons payload.

        timestamp_str should be an ISO-8601 string safe for use in S3 keys,
        e.g. '2026-06-07T14-30-00'.
        """
        key = f"weapons/snapshots/{timestamp_str}.json"
        self.store.put_json(key, weapons)
        return key


if __name__ == "__main__":
    session = boto3.Session(profile_name="fortnite-tournament-logs-s3")
