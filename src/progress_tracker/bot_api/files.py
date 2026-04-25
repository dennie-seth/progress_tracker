"""File-path helpers for the Bot API client.

When a remote `telegram-bot-api` server is started with `--local`, getFile
returns the file's *absolute filesystem path on the server's host* (e.g.
`/var/lib/telegram-bot-api/<token>/videos/file_0.MOV`) instead of a relative
URL fragment. Our bot is on a different host and reaches the server via SOCKS,
so we can't open that path — but the server still serves the same file via
HTTP at `<base>/file/bot<token>/<relative-path>`. This helper rewrites the
absolute path back to the relative form aiogram expects in non-local mode.
"""

from __future__ import annotations

_LOCAL_ROOT = "/var/lib/telegram-bot-api"


def normalize_remote_file_path(file_path: str, bot_token: str) -> str:
    """Rewrite a `--local`-style absolute path back to its relative form.

    Returns `file_path` unchanged if it doesn't begin with the expected
    `<local_root>/<bot_token>/` prefix — including already-relative paths and
    paths that belong to a different bot.
    """
    prefix = f"{_LOCAL_ROOT}/{bot_token}/"
    if file_path.startswith(prefix):
        return file_path[len(prefix) :]
    return file_path
