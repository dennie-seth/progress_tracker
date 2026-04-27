"""File-path helpers for the Bot API client.

Two helpers, one for each fetcher mode:

* `normalize_remote_file_path` — when the bot lives on a different host than
  bot-api and downloads files over HTTPS, we want the *relative* URL fragment.
  bot-api in `--local` mode returns absolute paths; this strips them back.

* `validate_local_file_path` — when the bot is co-located with bot-api on the
  same host and shares the storage volume, we read files directly from disk.
  Here we want the *absolute* path back, but only after asserting it sits
  under our trusted `<root>/<token>/` tree (no escapes, no other tenants).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

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


def validate_local_file_path(file_path: str, root: str, bot_token: str) -> Path:
    """Validate a `--local`-mode absolute path and return it as a `Path`.

    Used by `LocalFileFetcher` when bot-app and bot-api share a filesystem.
    bot-api always returns absolute paths in this mode; we accept only those
    that sit under `<root>/<bot_token>/` and contain no `..` segments. Anything
    else means a misconfigured server, a different tenant's directory, or an
    attempted traversal — all of which we refuse rather than guess.

    Path computation is done in `PurePosixPath` because bot-api runs on Linux;
    keeping it POSIX avoids `WindowsPath` weirdness on the dev host. The
    return value is a regular `Path` so the caller can hand it to
    `shutil.copyfile` directly.
    """
    if not file_path:
        raise ValueError("validate_local_file_path: empty file_path")
    posix = PurePosixPath(file_path)
    if not posix.is_absolute():
        raise ValueError(
            f"unsafe local file_path {file_path!r} (must be absolute)"
        )
    if ".." in posix.parts:
        raise ValueError(
            f"unsafe local file_path {file_path!r} (traversal)"
        )
    expected_root = PurePosixPath(root) / bot_token
    if not posix.is_relative_to(expected_root):
        raise ValueError(
            f"unsafe local file_path {file_path!r} (outside {expected_root})"
        )
    return Path(file_path)
