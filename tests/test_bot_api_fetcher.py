"""Tests for the FileFetcher Protocol and its two implementations.

`RemoteFileFetcher` preserves the existing dev-from-home behaviour:
`bot.get_file` → strip the local-mode prefix → `bot.download_file`. Cleanup
asks the remote bot-api server to drop its copy via `DeleteFile`.

`LocalFileFetcher` is the VDS-co-located mode: read the absolute path
straight off shared disk and copy bytes into our storage. No cleanup —
files persist on the VDS for indefinite reuse by the compile path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from progress_tracker.bot_api.fetcher import (
    FileFetcher,
    LocalFileFetcher,
    RemoteFileFetcher,
)
from progress_tracker.bot_api.methods import DeleteFile

TOKEN = "12345:ABC"
LOCAL_ROOT = "/var/lib/telegram-bot-api"


def _fake_message(file_id: str = "tg-1") -> MagicMock:
    msg = MagicMock(spec_set=("video",))
    msg.video = SimpleNamespace(file_id=file_id)
    return msg


# ---------- RemoteFileFetcher ----------


async def test_remote_fetcher_calls_get_file_then_download(tmp_path: Path) -> None:
    """The two-step path: `get_file` returns metadata, `download_file` is
    handed the relative URL fragment and the destination path."""

    async def _download(_path: str, destination: Path) -> None:
        destination.write_bytes(b"remote-bytes")

    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_id="x", file_path="videos/file_0.mp4")
    )
    bot.download_file = AsyncMock(side_effect=_download)

    target = tmp_path / "out.mp4"
    fetcher = RemoteFileFetcher()
    await fetcher.fetch(bot=bot, message=_fake_message(), target=target)

    assert target.read_bytes() == b"remote-bytes"
    assert bot.get_file.await_count == 1
    download_call = bot.download_file.await_args
    assert download_call.args[0] == "videos/file_0.mp4"
    assert download_call.kwargs["destination"] == target


async def test_remote_fetcher_strips_local_mode_prefix(tmp_path: Path) -> None:
    """In `--local` mode bot-api returns an absolute path; the remote fetcher
    must hand the *relative* form to `download_file` (download_file builds the
    URL from the fragment, not the absolute path)."""

    async def _download(_path: str, destination: Path) -> None:
        destination.write_bytes(b"x")

    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(
            file_id="x",
            file_path=f"{LOCAL_ROOT}/{TOKEN}/videos/file_99.mp4",
        )
    )
    bot.download_file = AsyncMock(side_effect=_download)

    fetcher = RemoteFileFetcher()
    await fetcher.fetch(bot=bot, message=_fake_message(), target=tmp_path / "out.mp4")

    download_call = bot.download_file.await_args
    assert download_call.args[0] == "videos/file_99.mp4"


async def test_remote_fetcher_cleanup_calls_delete_file() -> None:
    """Cleanup tells the *remote* bot-api server to drop its redundant copy."""
    bot = AsyncMock()
    bot.token = TOKEN

    fetcher = RemoteFileFetcher()
    await fetcher.cleanup(bot=bot, message=_fake_message(file_id="tg-fid-7"))

    bot.assert_awaited_once()
    (called_method,), _ = bot.await_args
    assert isinstance(called_method, DeleteFile)
    assert called_method.file_id == "tg-fid-7"


async def test_remote_fetcher_cleanup_swallows_telegram_not_found() -> None:
    """Older bot-api builds don't expose `deleteFile` and reply with 404. That
    isn't a failure worth raising — the upload itself already succeeded."""
    from aiogram.exceptions import TelegramNotFound
    from aiogram.methods import GetMe

    bot = AsyncMock()
    bot.token = TOKEN
    bot.side_effect = TelegramNotFound(method=GetMe(), message="method not found")

    fetcher = RemoteFileFetcher()
    # Must not raise.
    await fetcher.cleanup(bot=bot, message=_fake_message())
    bot.assert_awaited_once()


async def test_remote_fetcher_cleanup_swallows_generic_failures() -> None:
    """Any other delete_file failure is logged but never re-raised — disk-
    usage on the bot-api server isn't a correctness concern for ingest."""
    bot = AsyncMock()
    bot.token = TOKEN
    bot.side_effect = RuntimeError("server unavailable")

    fetcher = RemoteFileFetcher()
    await fetcher.cleanup(bot=bot, message=_fake_message())
    bot.assert_awaited_once()


# ---------- LocalFileFetcher ----------


async def test_local_fetcher_copies_file_from_disk(tmp_path: Path) -> None:
    """Local mode: bot-api returns an absolute path to a real file we can
    read (because we share its filesystem); the fetcher copies bytes to
    our target. No HTTP, no `download_file` call."""
    # Simulate the bot-api storage layout: <root>/<token>/videos/<name>
    src_root = tmp_path / "tg-data"
    src_dir = src_root / TOKEN / "videos"
    src_dir.mkdir(parents=True)
    src_file = src_dir / "file_42.MOV"
    src_file.write_bytes(b"local-bytes")

    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_id="x", file_path=str(src_file))
    )
    bot.download_file = AsyncMock()

    target = tmp_path / "out.mov"
    fetcher = LocalFileFetcher(root=str(src_root), token=TOKEN)
    await fetcher.fetch(bot=bot, message=_fake_message(), target=target)

    assert target.read_bytes() == b"local-bytes"
    bot.download_file.assert_not_called()


async def test_local_fetcher_rejects_path_outside_root(tmp_path: Path) -> None:
    """If the server returns a path outside our trusted tree, refuse rather
    than copy — defence-in-depth against a misconfigured/compromised server."""
    src_root = tmp_path / "tg-data"
    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_id="x", file_path="/etc/passwd")
    )

    fetcher = LocalFileFetcher(root=str(src_root), token=TOKEN)
    with pytest.raises(ValueError):
        await fetcher.fetch(
            bot=bot, message=_fake_message(), target=tmp_path / "out.bin"
        )


async def test_local_fetcher_rejects_traversal(tmp_path: Path) -> None:
    src_root = tmp_path / "tg-data"
    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(
            file_id="x",
            file_path=f"{src_root}/{TOKEN}/../{TOKEN}/videos/x.mp4",
        )
    )

    fetcher = LocalFileFetcher(root=str(src_root), token=TOKEN)
    with pytest.raises(ValueError):
        await fetcher.fetch(
            bot=bot, message=_fake_message(), target=tmp_path / "out.bin"
        )


async def test_local_fetcher_rejects_relative_path(tmp_path: Path) -> None:
    """In local mode bot-api ALWAYS returns absolute paths; a relative one
    means something's misconfigured — fail loud rather than guess."""
    src_root = tmp_path / "tg-data"
    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_id="x", file_path="videos/x.mp4")
    )

    fetcher = LocalFileFetcher(root=str(src_root), token=TOKEN)
    with pytest.raises(ValueError):
        await fetcher.fetch(
            bot=bot, message=_fake_message(), target=tmp_path / "out.bin"
        )


async def test_local_fetcher_rejects_empty_file_path(tmp_path: Path) -> None:
    src_root = tmp_path / "tg-data"
    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_id="x", file_path="")
    )

    fetcher = LocalFileFetcher(root=str(src_root), token=TOKEN)
    with pytest.raises(ValueError):
        await fetcher.fetch(
            bot=bot, message=_fake_message(), target=tmp_path / "out.bin"
        )


async def test_local_fetcher_surfaces_missing_source(tmp_path: Path) -> None:
    """Race with bot-api `cleanup-policy`: between `get_file` and our copy
    the file may vanish. The fetcher surfaces FileNotFoundError; ingest's
    existing orphan-cleanup will then unwind."""
    src_root = tmp_path / "tg-data"
    (src_root / TOKEN / "videos").mkdir(parents=True)
    # Note: file_X.mp4 is NOT actually written.
    fp = src_root / TOKEN / "videos" / "missing.mp4"

    bot = AsyncMock()
    bot.token = TOKEN
    bot.get_file = AsyncMock(
        return_value=SimpleNamespace(file_id="x", file_path=str(fp))
    )

    fetcher = LocalFileFetcher(root=str(src_root), token=TOKEN)
    with pytest.raises(FileNotFoundError):
        await fetcher.fetch(
            bot=bot, message=_fake_message(), target=tmp_path / "out.mp4"
        )


async def test_local_fetcher_cleanup_is_noop() -> None:
    """Per user direction: source files persist on the VDS indefinitely.
    Local cleanup must NOT call DeleteFile (or anything else on the bot)."""
    bot = AsyncMock()
    bot.token = TOKEN

    fetcher = LocalFileFetcher(root=LOCAL_ROOT, token=TOKEN)
    await fetcher.cleanup(bot=bot, message=_fake_message())

    bot.assert_not_awaited()


# ---------- Protocol structural conformance ----------


def test_both_implementations_satisfy_protocol() -> None:
    """A small structural check so we catch signature drift early."""
    remote: FileFetcher = RemoteFileFetcher()
    local: FileFetcher = LocalFileFetcher(root=LOCAL_ROOT, token=TOKEN)
    assert hasattr(remote, "fetch") and hasattr(remote, "cleanup")
    assert hasattr(local, "fetch") and hasattr(local, "cleanup")
