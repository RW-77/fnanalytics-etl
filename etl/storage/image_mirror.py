"""Parallel download → S3 upload of catalog images (fnapi CDN → our bucket).

Shared by the catalog sync jobs (``sync_cosmetics``, ``sync_augments``). Same
threading discipline as ``sync_weapons``: worker threads only do HTTP downloads
and S3 uploads; the resulting keys are handed back to the main thread, which
owns every DB write. Unlike ``sync_weapons``, keys are written back in batches
(``save_keys`` is called every :data:`SAVE_BATCH_SIZE` results) — the cosmetics
catalog is ~20k items, and one transaction per item against a remote Postgres
would dominate the runtime. Each batch is committed by ``save_keys``, so an
interrupted run keeps its progress and the next run mirrors only the remainder.
"""

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

IMAGE_MIRROR_WORKERS = int(os.getenv("IMAGE_MIRROR_WORKERS", "8"))
DOWNLOAD_TIMEOUT_S = 30
SAVE_BATCH_SIZE = 250

# (item_id, data, content_type) -> S3 key
PutImage = Callable[[str, bytes, str], str]
# (item_id, image_key, small_image_key)
MirrorResult = tuple[str, str | None, str | None]

# requests.Session isn't documented as thread-safe, so each worker gets its own.
_thread_local = threading.local()


def _http() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        _thread_local.session = session
    return session


def download_image(url: str) -> tuple[bytes, str]:
    """Download one image, returning ``(data, content_type)``.

    Raises on an HTTP error or a non-image response, so an error page is never
    uploaded in place of an image.
    """
    response = _http().get(url, timeout=DOWNLOAD_TIMEOUT_S)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"{url} returned {content_type or 'no content type'}, not an image")
    return response.content, content_type


def _mirror_one(item: dict, put_image: PutImage, put_small_image: PutImage) -> MirrorResult:
    """Mirror one item's large and small images. Runs on a worker thread."""
    item_id = item["id"]
    image_key = small_image_key = None

    if item.get("image_url"):
        data, content_type = download_image(item["image_url"])
        image_key = put_image(item_id, data, content_type)

    if item.get("small_image_url"):
        data, content_type = download_image(item["small_image_url"])
        small_image_key = put_small_image(item_id, data, content_type)

    return item_id, image_key, small_image_key


def mirror_images(
    items: list[dict],
    put_image: PutImage,
    put_small_image: PutImage,
    save_keys: Callable[[list[MirrorResult]], None],
    *,
    label: str,
    workers: int = IMAGE_MIRROR_WORKERS,
) -> None:
    """Download and upload images for ``items`` in parallel.

    Each item is a dict with ``id``, ``image_url`` and ``small_image_url``
    (either URL may be None). ``save_keys`` receives batches of
    ``(id, image_key, small_image_key)`` on the main thread and must commit
    them. A failed item is logged and skipped — its keys stay NULL, so the next
    sync retries it.
    """
    if not items:
        return

    print(f"Mirroring images for {len(items)} {label} ({workers} concurrent workers)...")

    succeeded = failed = 0
    pending: list[MirrorResult] = []

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(_mirror_one, item, put_image, put_small_image): item["id"]
            for item in items
        }

        for future in as_completed(futures):
            item_id = futures[future]
            try:
                pending.append(future.result())
                succeeded += 1
            except Exception as e:
                # Log and continue — one bad URL won't abort the whole batch
                failed += 1
                print(f"  ❌ {item_id}: {e}")

            if len(pending) >= SAVE_BATCH_SIZE:
                save_keys(pending)
                pending = []
                print(f"  {succeeded + failed}/{len(items)} done ({failed} failed)")

        if pending:
            save_keys(pending)
    finally:
        # On a main-thread error (e.g. save_keys losing its DB connection) or
        # Ctrl-C, drop the queued downloads rather than finishing all of them.
        pool.shutdown(wait=True, cancel_futures=True)

    print(f"Image mirroring complete — {succeeded} succeeded, {failed} failed.")
