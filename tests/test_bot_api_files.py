"""Tests for normalize_remote_file_path."""

from __future__ import annotations

import pytest

from progress_tracker.bot_api.files import normalize_remote_file_path

TOKEN = "8697336546:AAEFbdz_BjQ6d2JnsUTAipsNJQbIgKS_wYg"


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
