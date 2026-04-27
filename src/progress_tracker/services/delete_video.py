"""Delete a video the user owns: row + on-disk file.

Two-step orchestration:
  1. `VideoRepo.delete_for_user` removes the row inside the caller's
     transaction and returns the storage_key (or None on miss).
  2. If the row went, ask `Storage` to remove the file.

If step 2 fails after step 1 succeeded, we report success: the row is gone
from the user's library either way, and a leftover orphan file is an
operator-side disk-usage concern rather than a correctness one. The same
trade-off applies to bot-api's local-mode source copy in the VDS deploy —
per `bot_api/fetcher.py` the rule is "files persist on the VDS
indefinitely" and `LocalFileFetcher.cleanup` is a no-op; that policy is
unchanged here. We only manage what we put in our own `Storage`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.repos import VideoRepo
from progress_tracker.storage.base import Storage

_log = structlog.get_logger("progress_tracker.delete_video")


@dataclass(frozen=True)
class DeleteResult:
    deleted: bool
    storage_key: str | None


async def delete_video(
    *,
    session: AsyncSession,
    storage: Storage,
    user_id: int,
    video_id: uuid.UUID,
) -> DeleteResult:
    """Remove the video row and its on-disk file.

    Returns `DeleteResult(deleted=True, storage_key=...)` when the row was
    found and removed; `DeleteResult(deleted=False, storage_key=None)` when
    the id is unknown or belongs to a different user (same response either
    way to avoid leaking ownership).
    """
    storage_key = await VideoRepo(session).delete_for_user(
        video_id=video_id, user_id=user_id
    )
    if storage_key is None:
        return DeleteResult(deleted=False, storage_key=None)

    try:
        await storage.delete(storage_key)
    except Exception:
        # The DB row is gone (and will be committed by the middleware);
        # the file is now an orphan. Log and report success — re-raising
        # would tell the user "delete failed" when from their perspective
        # the video has already disappeared from the library.
        _log.warning(
            "storage.delete failed after row removed; orphan file remains",
            storage_key=storage_key,
            user_id=user_id,
            video_id=str(video_id),
            exc_info=True,
        )

    return DeleteResult(deleted=True, storage_key=storage_key)
