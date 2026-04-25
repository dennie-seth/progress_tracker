"""Tests for LocalStorage."""

from __future__ import annotations

from pathlib import Path

import pytest

from progress_tracker.storage.local import LocalStorage


async def test_write_path_returns_path_under_root(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    path = await storage.write_path("user123/abc.mp4")
    assert path == tmp_path / "user123" / "abc.mp4"


async def test_write_path_creates_parent_dirs(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    path = await storage.write_path("deep/nested/dir/abc.mp4")
    assert path.parent.is_dir()


async def test_exists_after_write(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    path = await storage.write_path("a/b.mp4")
    path.write_bytes(b"x")
    await storage.commit("a/b.mp4")
    assert await storage.exists("a/b.mp4")


async def test_exists_returns_false_when_missing(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    assert not await storage.exists("nope.mp4")


async def test_open_yields_real_path(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    path = await storage.write_path("a/b.mp4")
    path.write_bytes(b"hello")

    async with storage.open("a/b.mp4") as p:
        assert p.read_bytes() == b"hello"


async def test_open_raises_on_missing(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        async with storage.open("nope.mp4"):
            pass


async def test_delete_removes_file(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    path = await storage.write_path("a/b.mp4")
    path.write_bytes(b"x")
    await storage.delete("a/b.mp4")
    assert not await storage.exists("a/b.mp4")


async def test_delete_is_idempotent(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    # Should not raise even though the file doesn't exist.
    await storage.delete("never-existed.mp4")


async def test_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalStorage(root=tmp_path)
    with pytest.raises(ValueError):
        await storage.write_path("../escape.mp4")
    with pytest.raises(ValueError):
        await storage.write_path("a/../../escape.mp4")


async def test_rejects_absolute_unix_key(tmp_path: Path) -> None:
    """Defensive: an absolute key must not be treated as relative-to-root."""
    storage = LocalStorage(root=tmp_path)
    with pytest.raises(ValueError):
        await storage.write_path("/etc/passwd")


async def test_delete_no_op_when_path_is_directory(tmp_path: Path) -> None:
    """Per the Storage contract, delete() is best-effort and must not raise on
    keys that resolve to directories."""
    storage = LocalStorage(root=tmp_path)
    (tmp_path / "user42").mkdir()
    # Should not raise — directories are not files we manage.
    await storage.delete("user42")
    # Directory is left intact.
    assert (tmp_path / "user42").is_dir()
