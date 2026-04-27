"""Tests for normalize_remote_file_path and validate_local_file_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from progress_tracker.bot_api.files import (
    normalize_remote_file_path,
    validate_local_file_path,
)

TOKEN = "8697336546:AAEFbdz_BjQ6d2JnsUTAipsNJQbIgKS_wYg"
LOCAL_ROOT = "/var/lib/telegram-bot-api"


def test_strips_local_mode_prefix() -> None:
    file_path = f"/var/lib/telegram-bot-api/{TOKEN}/videos/file_0.MOV"
    assert (
        normalize_remote_file_path(file_path, TOKEN)
        == "videos/file_0.MOV"
    )


def test_passes_relative_path_through_unchanged() -> None:
    assert (
        normalize_remote_file_path("videos/file_0.MOV", TOKEN)
        == "videos/file_0.MOV"
    )


def test_unrelated_absolute_path_is_rejected() -> None:
    """An absolute path that isn't ours after stripping is unsafe — reject it
    rather than passing it on to download_file (where it would either yield a
    malformed URL or, in the worst case, traverse to an unintended location).
    """
    fp = "/var/lib/telegram-bot-api/some-other-token/videos/x.mp4"
    with pytest.raises(ValueError):
        normalize_remote_file_path(fp, TOKEN)


def test_handles_documents_and_photos_subdirs() -> None:
    for subdir in ("documents", "photos", "audios", "voice"):
        fp = f"/var/lib/telegram-bot-api/{TOKEN}/{subdir}/x"
        assert normalize_remote_file_path(fp, TOKEN) == f"{subdir}/x"


def test_empty_string_returns_empty() -> None:
    assert normalize_remote_file_path("", TOKEN) == ""


def test_rejects_absolute_path_unrelated_to_us() -> None:
    """If a remote bot-api emits an absolute path that ISN'T under our token's
    directory, refuse it — passing such a path to download_file would yield a
    malformed URL or, worse, fetch from an unintended location.
    """
    with pytest.raises(ValueError):
        normalize_remote_file_path("/etc/passwd", TOKEN)


def test_rejects_traversal_segments() -> None:
    with pytest.raises(ValueError):
        normalize_remote_file_path("videos/../../etc/passwd", TOKEN)


def test_rejects_traversal_after_local_strip() -> None:
    """After stripping the local-mode prefix, the remainder must still be safe."""
    with pytest.raises(ValueError):
        normalize_remote_file_path(
            f"/var/lib/telegram-bot-api/{TOKEN}/../escape.mp4", TOKEN
        )


# ---------- validate_local_file_path ----------
#
# Local-files mode reads the source file directly from disk via the absolute
# path returned by `getFile` in bot-api `--local` mode. Unlike the remote
# helper above, we MUST end up with an absolute filesystem path (so the
# fetcher can `shutil.copyfile` from it) — but only one we trust came from
# our own bot-api server's storage tree.


def test_validate_local_returns_path_under_root() -> None:
    fp = f"{LOCAL_ROOT}/{TOKEN}/videos/file_0.MOV"
    result = validate_local_file_path(fp, LOCAL_ROOT, TOKEN)
    assert isinstance(result, Path)
    assert str(result).replace("\\", "/") == fp


def test_validate_local_rejects_relative_path() -> None:
    """In local-files mode, bot-api ALWAYS returns absolute paths — a relative
    one means something's misconfigured and we shouldn't guess."""
    with pytest.raises(ValueError):
        validate_local_file_path("videos/file_0.MOV", LOCAL_ROOT, TOKEN)


def test_validate_local_rejects_path_outside_root() -> None:
    """Absolute path outside our trusted storage tree → reject."""
    with pytest.raises(ValueError):
        validate_local_file_path("/etc/passwd", LOCAL_ROOT, TOKEN)


def test_validate_local_rejects_path_under_different_token() -> None:
    """A different token's directory could exist on the server — reading from
    it would mix tenants. Refuse."""
    other_token = "1111111111:OTHER"
    fp = f"{LOCAL_ROOT}/{other_token}/videos/file_0.MOV"
    with pytest.raises(ValueError):
        validate_local_file_path(fp, LOCAL_ROOT, TOKEN)


def test_validate_local_rejects_traversal_segments() -> None:
    fp = f"{LOCAL_ROOT}/{TOKEN}/../{TOKEN}/videos/file_0.MOV"
    with pytest.raises(ValueError):
        validate_local_file_path(fp, LOCAL_ROOT, TOKEN)


def test_validate_local_rejects_empty_string() -> None:
    """Empty input is a server-side bug — fail loud."""
    with pytest.raises(ValueError):
        validate_local_file_path("", LOCAL_ROOT, TOKEN)


def test_validate_local_accepts_alternate_root() -> None:
    """The caller can pass any root — we trust the caller's root config and
    only enforce the `<root>/<token>/...` shape from there."""
    alt_root = "/srv/tg"
    fp = f"{alt_root}/{TOKEN}/videos/file_0.MOV"
    result = validate_local_file_path(fp, alt_root, TOKEN)
    assert str(result).replace("\\", "/") == fp


def test_validate_local_handles_subdirs() -> None:
    for subdir in ("documents", "photos", "audios", "voice"):
        fp = f"{LOCAL_ROOT}/{TOKEN}/{subdir}/x"
        result = validate_local_file_path(fp, LOCAL_ROOT, TOKEN)
        assert str(result).replace("\\", "/") == fp
