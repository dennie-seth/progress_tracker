"""Orchestrate a compile request end-to-end.

Pipeline:
    query candidates → select subset → probe → ffmpeg → store → record
    → send to Telegram.

Lives outside the FSM handler so the long-running steps can run in a
background `asyncio.create_task` without keeping a per-update DB session
alive. The FSM hands us a fresh `session_factory` and we open our own
session for the duration.
"""

from __future__ import annotations

import contextlib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import structlog
from aiogram import Bot
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.db.repos import CompilationRepo, VideoRepo
from progress_tracker.storage.base import Storage
from progress_tracker.video.compile import ClipMeta, compile_videos
from progress_tracker.video.probe import probe
from progress_tracker.video.select import select_clips

_log = structlog.get_logger("progress_tracker.compiler")


@dataclass(frozen=True)
class CompileResult:
    storage_key: str
    duration_sec: Decimal
    clip_count: int


async def compile_progress_reel(
    *,
    bot: Bot,
    chat_id: int,
    user_id: int,
    tag_id: int,
    target_duration: int,
    since: datetime | None,
    overlay_dates: bool,
    session_factory: async_sessionmaker[AsyncSession],
    storage: Storage,
) -> CompileResult | None:
    """Build a progress reel for the given user/tag/range and send it back.

    Returns None when no clips match (the caller should reply accordingly);
    otherwise returns a `CompileResult` describing the persisted artefact.
    """
    async with session_factory() as session:
        candidates = await VideoRepo(session).list_for_user_tag(
            user_id=user_id, tag_id=tag_id, since=since
        )

    if not candidates:
        _log.info("no candidates for compile", user_id=user_id, tag_id=tag_id)
        return None

    picked = select_clips(candidates)
    _log.info(
        "compile starting",
        user_id=user_id,
        tag_id=tag_id,
        candidate_count=len(candidates),
        picked_count=len(picked),
        target_duration=target_duration,
        overlay=overlay_dates,
    )

    # Resolve each picked clip's storage_key to a real filesystem path. We use
    # `AsyncExitStack` so every Storage.open context closes regardless of
    # outcome — important for the future S3 backend that downloads to a
    # tempdir per `open()`.
    async with contextlib.AsyncExitStack() as stack:
        input_paths: list[Path] = []
        metas: list[ClipMeta] = []
        for video in picked:
            input_path = await stack.enter_async_context(storage.open(video.storage_key))
            probed = await probe(input_path)
            input_paths.append(input_path)
            # Prefer the container's `creation_time` tag (when the clip was
            # actually recorded) over our DB row's upload time. Falls back to
            # `Video.created_at` when the file has no creation_time metadata.
            label_source = probed.creation_time or video.created_at
            label = label_source.strftime("%Y-%m-%d") if overlay_dates else None
            metas.append(ClipMeta(duration=probed.duration, date_label=label))

        # Compile to a temp file first; copy into our Storage on success so
        # a partial render never lands as our canonical artefact.
        comp_id = uuid.uuid4()
        with tempfile.TemporaryDirectory(prefix="compile-") as tmpdir:
            temp_out = Path(tmpdir) / f"{comp_id}.mp4"
            await compile_videos(
                inputs=input_paths,
                metas=metas,
                target_duration=float(target_duration),
                output=temp_out,
            )

            storage_key = f"{user_id}/compilations/{comp_id}.mp4"
            target = await storage.write_path(storage_key)
            target.write_bytes(temp_out.read_bytes())
            await storage.commit(storage_key)
            final_path = target

    # Probe the rendered output for the actual duration we'll record.
    rendered = await probe(final_path)

    async with session_factory() as session:
        await CompilationRepo(session).create(
            id=comp_id,
            user_id=user_id,
            tag_id=tag_id,
            from_date=since,
            to_date=None,
            duration_sec=rendered.duration,
            storage_key=storage_key,
        )
        await session.commit()

    # Send to the chat. FSInputFile streams from disk so 2 GB compilations
    # don't have to fit in memory.
    await bot.send_video(
        chat_id=chat_id,
        video=FSInputFile(final_path),
        caption=f"{len(picked)} clips · {rendered.duration:.1f}s",
    )

    _log.info(
        "compile finished",
        user_id=user_id,
        compilation_id=str(comp_id),
        duration=str(rendered.duration),
    )
    return CompileResult(
        storage_key=storage_key,
        duration_sec=rendered.duration,
        clip_count=len(picked),
    )
