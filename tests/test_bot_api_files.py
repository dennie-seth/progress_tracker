"""Tests for normalize_remote_file_path."""

from __future__ import annotations

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


def test_passes_unrelated_absolute_path_through_unchanged() -> None:
    """Defensive: don't strip prefixes from paths that don't carry our token."""
    fp = "/var/lib/telegram-bot-api/some-other-token/videos/x.mp4"
    assert normalize_remote_file_path(fp, TOKEN) == fp


def test_handles_documents_and_photos_subdirs() -> None:
    for subdir in ("documents", "photos", "audios", "voice"):
        fp = f"/var/lib/telegram-bot-api/{TOKEN}/{subdir}/x"
        assert normalize_remote_file_path(fp, TOKEN) == f"{subdir}/x"


def test_empty_string_returns_empty() -> None:
    assert normalize_remote_file_path("", TOKEN) == ""
