"""Bot API methods aiogram doesn't ship convenience wrappers for.

aiogram only auto-generates classes for methods documented in the cloud
Bot API. `deleteFile` is a local-Bot-API-server-only method
(https://core.telegram.org/bots/api#deletefile), so we declare it here and
invoke via `await bot(DeleteFile(file_id=...))`.
"""

from __future__ import annotations

from aiogram.methods.base import TelegramMethod


class DeleteFile(TelegramMethod[bool]):
    """Free a previously-downloaded file from the local Bot API server's cache."""

    __returning__ = bool
    __api_method__ = "deleteFile"

    file_id: str
