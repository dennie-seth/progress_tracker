"""File-fetcher strategies for retrieving an uploaded video's bytes.

There are two operationally distinct deployments for this bot:

* **Dev-from-home (remote bot-api).** The bot-app runs on the developer's
  machine and reaches a `telegram-bot-api` server on a VDS over the public
  internet (via SOCKS5 + HTTP Basic Auth). Each video is downloaded over
  HTTPS via aiogram's `bot.download_file`. After ingest succeeds, we ask
  bot-api to drop its redundant copy via the local-only `deleteFile` method.
  This is `RemoteFileFetcher`.

* **Co-located on the VDS.** The bot-app and bot-api processes share a
  filesystem (a bind-mounted directory). When bot-api is started with
  `--local`, `getFile` returns the file's *absolute path on disk*, which —
  thanks to the shared mount — is also a valid path inside the bot-app
  container. We read it directly with `shutil.copyfile`. No HTTP, no auth,
  no `deleteFile` (per user direction, source files persist on the VDS
  indefinitely; retention is operator-managed via bot-api's
  `cleanup-policy` flag). This is `LocalFileFetcher`.

Selection happens at startup based on `BOT_API_LOCAL_FILES`. Ingest itself
is mode-blind: it just calls `fetch` then `cleanup` on whatever fetcher the
middleware hands it.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Protocol

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramNotFound
from aiogram.types import Message

from progress_tracker.bot_api.files import (
    normalize_remote_file_path,
    validate_local_file_path,
)
from progress_tracker.bot_api.methods import DeleteFile

_log = structlog.get_logger("progress_tracker.bot_api.fetcher")


class FileFetcher(Protocol):
    """Pulls the bytes of an uploaded video into our storage and, optionally,
    asks the bot-api server to clean up its own copy afterwards."""

    async def fetch(self, *, bot: Bot, message: Message, target: Path) -> None:
        """Write the file referenced by `message.video.file_id` to `target`."""
        ...

    async def cleanup(self, *, bot: Bot, message: Message) -> None:
        """Best-effort post-ingest cleanup on the bot-api side."""
        ...


def _file_id(message: Message) -> str:
    """Narrow `message.video` for mypy. The handler that calls us only
    triggers on `F.video`, so `message.video` is guaranteed non-None — but
    mypy can't see through the aiogram Message type."""
    if message.video is None:
        raise ValueError("FileFetcher invoked on a message with no video")
    return message.video.file_id


class RemoteFileFetcher:
    """HTTPS download via aiogram's `bot.download_file`.

    `bot.download(...)` would build the URL from `getFile.file_path` verbatim;
    when bot-api runs with `--local`, that field is an absolute filesystem
    path on the *server's* host — useless to us over HTTP. We resolve
    `getFile` ourselves, normalize the path back to its relative form (which
    bot-api still serves at `/file/bot<token>/<rel>`), then call
    `download_file`.
    """

    async def fetch(self, *, bot: Bot, message: Message, target: Path) -> None:
        file_id = _file_id(message)
        tg_file = await bot.get_file(file_id)
        relative_path = normalize_remote_file_path(
            tg_file.file_path or "", bot.token
        )
        _log.info(
            "downloading video",
            file_id=file_id,
            raw_file_path=tg_file.file_path,
            relative_path=relative_path,
            file_size=getattr(tg_file, "file_size", None),
        )
        await bot.download_file(relative_path, destination=target)

    async def cleanup(self, *, bot: Bot, message: Message) -> None:
        file_id = _file_id(message)
        # `deleteFile` is a local-bot-api-server-only method (not in the
        # cloud API), so aiogram doesn't ship a convenience wrapper — we
        # invoke it via the explicit method form.
        try:
            await bot(DeleteFile(file_id=file_id))
        except TelegramNotFound:
            # Some older `telegram-bot-api` builds don't expose `deleteFile`.
            # The upload already succeeded; we just can't reclaim the
            # server's disk. Log at debug so verbose runs can see it.
            _log.debug(
                "bot-api has no deleteFile method; cleanup unavailable",
                file_id=file_id,
            )
        except Exception:
            _log.warning(
                "delete_file failed; bot-api copy may persist",
                file_id=file_id,
                exc_info=True,
            )


class LocalFileFetcher:
    """Direct-from-disk read for the co-located VDS deployment.

    `root` is the bot-api server's storage root *as visible inside this
    container* (typically `/var/lib/telegram-bot-api`, bind-mounted in).
    `token` is our bot's token — only paths under `<root>/<token>/` are
    accepted, so a different tenant's files (or anything outside the trusted
    tree) get rejected before we open them.
    """

    def __init__(self, *, root: str, token: str) -> None:
        self._root = root
        self._token = token

    async def fetch(self, *, bot: Bot, message: Message, target: Path) -> None:
        file_id = _file_id(message)
        tg_file = await bot.get_file(file_id)
        src = validate_local_file_path(
            tg_file.file_path or "", self._root, self._token
        )
        _log.info(
            "copying local video",
            file_id=file_id,
            src=str(src),
            target=str(target),
            file_size=getattr(tg_file, "file_size", None),
        )
        # `shutil.copyfile` is blocking and the videos can be ~2 GB — push it
        # to a thread so the event loop keeps serving updates.
        await asyncio.to_thread(shutil.copyfile, src, target)

    async def cleanup(self, *, bot: Bot, message: Message) -> None:
        # No-op by design: in the co-located deployment, source files live
        # on the same disk the bot reads from. The compile path may want
        # to read them again later, and operator-side `cleanup-policy`
        # handles retention.
        return None
