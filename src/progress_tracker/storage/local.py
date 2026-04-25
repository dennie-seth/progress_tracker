"""Local-filesystem implementation of the Storage Protocol."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator


class LocalStorage:
    """Stores files under a single root directory on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _resolve(self, key: str) -> Path:
        # Reject keys that escape the root via `..` segments.
        parts = Path(key).parts
        if ".." in parts:
            raise ValueError(f"unsafe key {key!r} contains '..'")
        target = (self._root / key).resolve()
        try:
            target.relative_to(self._root.resolve())
        except ValueError as exc:
            raise ValueError(f"unsafe key {key!r} resolves outside storage root") from exc
        return target

    async def write_path(self, key: str) -> Path:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    async def commit(self, key: str) -> None:
        # Files written via `write_path` are already at their final location.
        return None

    @asynccontextmanager
    async def open(self, key: str) -> AsyncIterator[Path]:
        target = self._resolve(key)
        if not target.exists():
            raise FileNotFoundError(target)
        yield target

    async def delete(self, key: str) -> None:
        target = self._resolve(key)
        # `unlink(missing_ok=True)` raises IsADirectoryError on directories;
        # the Storage contract is no-op-on-unknown, so guard explicitly.
        if target.is_file():
            target.unlink()

    async def exists(self, key: str) -> bool:
        return self._resolve(key).exists()
