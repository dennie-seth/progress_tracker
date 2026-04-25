"""Storage Protocol — contract for any persistent file backend.

ffmpeg requires real filesystem paths, so the Protocol exposes both a write
location (`write_path`) and a read context manager (`open`) that yield real
paths. A future S3 backend will use a tempdir under the hood.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    async def write_path(self, key: str) -> Path:
        """Return the local filesystem path where the caller should write the file.

        For LocalStorage this is the canonical location; for a future S3
        backend it would be a tempfile staged before upload. Parent directories
        are created as needed. Implementations must reject path-traversal keys.
        """
        ...

    async def commit(self, key: str) -> None:
        """Finalize a previously written key (e.g. upload to remote backend).

        For LocalStorage this is a no-op; for S3 it would upload the staged
        tempfile and remove it.
        """
        ...

    def open(self, key: str) -> AbstractAsyncContextManager[Path]:
        """Yield a local filesystem path the caller can read from.

        Implementations may download the object to a tempdir and clean up on
        context exit. Raises FileNotFoundError if the key is unknown.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove the stored file. No error if the key doesn't exist."""
        ...

    async def exists(self, key: str) -> bool:
        """Whether a file is stored under the given key."""
        ...
