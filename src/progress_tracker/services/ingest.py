"""Video ingest: download from Telegram, persist to storage + DB."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import structlog
from aiogram import Bot
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.bot_api.fetcher import FileFetcher
from progress_tracker.db.models import Video
from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.storage.base import Storage
from progress_tracker.utils.hashtags import parse_hashtags

_log = structlog.get_logger("progress_tracker.ingest")


@dataclass(frozen=True)
class IngestResult:
    video: Video
    tag_names: list[str]
    # Count of this user's prior videos that share at least one tag with the
    # newly-uploaded clip — drives the "Generate a reel?" prompt.
    prior_count: int


async def ingest_video(
    *,
    bot: Bot,
    message: Message,
    session: AsyncSession,
    storage: Storage,
    fetcher: FileFetcher,
) -> IngestResult | None:
    """Persist an uploaded video and return its IngestResult.

    Returns None when the message has no video or no hashtag in its caption —
    the caller should reply with a help message in those cases.

    Caller is responsible for `session.commit()` after this returns. The
    ingest service stays transaction-agnostic so it can be wrapped by a
    middleware later. The `fetcher` strategy decides how the bytes get from
    Telegram into storage (HTTP vs direct disk read) and what cleanup, if
    any, happens on the bot-api side afterwards — see `bot_api.fetcher`.
    """
    if message.video is None:
        return None

    tag_names = parse_hashtags(message.caption)
    if not tag_names:
        return None

    tg_user = message.from_user
    user = await UserRepo(session).upsert(
        user_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
    )

    tags = await TagRepo(session).upsert_many(user.id, tag_names)
    tag_ids = [t.id for t in tags]

    video_id = uuid.uuid4()
    storage_key = f"{user.id}/{video_id}.mp4"
    target = await storage.write_path(storage_key)

    try:
        await fetcher.fetch(bot=bot, message=message, target=target)
        await storage.commit(storage_key)

        tg_video = message.video
        video = await VideoRepo(session).create(
            id=video_id,
            user_id=user.id,
            telegram_file_id=tg_video.file_id,
            storage_key=storage_key,
            duration_sec=Decimal(int(tg_video.duration)),
            width=tg_video.width,
            height=tg_video.height,
            caption=message.caption,
            tag_ids=tag_ids,
        )

        prior_count = await VideoRepo(session).count_for_tags(
            user_id=user.id, tag_ids=tag_ids, exclude_video_id=video.id
        )
    except Exception:
        # The DB transaction will be rolled back by the middleware; remove the
        # on-disk artefact so we don't accumulate orphaned files.
        await storage.delete(storage_key)
        raise

    # Best-effort post-ingest cleanup. RemoteFileFetcher asks bot-api to drop
    # its redundant copy via deleteFile; LocalFileFetcher is a no-op (files
    # persist on the shared VDS disk for indefinite reuse).
    await fetcher.cleanup(bot=bot, message=message)

    return IngestResult(video=video, tag_names=tag_names, prior_count=prior_count)
