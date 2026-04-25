"""File-path helpers for the Bot API client.

When a remote `telegram-bot-api` server is started with `--local`, getFile
returns the file's *absolute filesystem path on the server's host* (e.g.
`/var/lib/telegram-bot-api/<token>/videos/file_0.MOV`) instead of a relative
URL fragment. Our bot is on a different host and reaches the server via SOCKS,
so we can't open that path — but the server still serves the same file via
HTTP at `<base>/file/bot<token>/<relative-path>`. This helper rewrites the
absolute path back to the relative form aiogram expects in non-local mode,
and refuses any value that wouldn't be safe to drop straight into the URL.
"""

from __future__ import annotations

from pathlib import PurePosixPath

_LOCAL_ROOT = "/var/lib/telegram-bot-api"


def normalize_remote_file_path(file_path: str, bot_token: str) -> str:
    """Rewrite a `--local`-style absolute path back to its relative form.

    Strips the `<local_root>/<bot_token>/` prefix when present, then validates
    the result. Raises `ValueError` if the (possibly stripped) path is still
    absolute or contains traversal segments — both would produce a malformed
    or dangerous download URL.
    """
    prefix = f"{_LOCAL_ROOT}/{bot_token}/"
    if file_path.startswith(prefix):
        file_path = file_path[len(prefix) :]
    if file_path.startswith("/"):
        raise ValueError(
            f"unsafe file_path {file_path!r} from bot-api server (absolute)"
        )
    if ".." in PurePosixPath(file_path).parts:
        raise ValueError(
            f"unsafe file_path {file_path!r} from bot-api server (traversal)"
        )
    return file_path
