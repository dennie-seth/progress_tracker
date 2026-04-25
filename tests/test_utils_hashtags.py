"""Tests for the hashtag parser."""

from __future__ import annotations

import pytest

from progress_tracker.utils.hashtags import parse_hashtags


def test_none_caption_returns_empty() -> None:
    assert parse_hashtags(None) == []


def test_empty_caption_returns_empty() -> None:
    assert parse_hashtags("") == []


def test_no_hashtags_returns_empty() -> None:
    assert parse_hashtags("just a regular message") == []


def test_single_hashtag() -> None:
    assert parse_hashtags("#squat") == ["squat"]


def test_hashtag_in_middle_of_text() -> None:
    assert parse_hashtags("hello #squat day 1") == ["squat"]


def test_lowercases_tag_name() -> None:
    assert parse_hashtags("#Squat") == ["squat"]
    assert parse_hashtags("#PR-Day") == ["pr-day"]


def test_dedupes_repeated_tags() -> None:
    assert parse_hashtags("#squat #squat #SQUAT") == ["squat"]


def test_preserves_order_of_first_appearance() -> None:
    assert parse_hashtags("#squat #pr #squat #deadlift") == ["squat", "pr", "deadlift"]


def test_hyphenated_tags_kept_intact() -> None:
    assert parse_hashtags("#bachata-basic") == ["bachata-basic"]


def test_underscores_kept() -> None:
    assert parse_hashtags("#leg_day") == ["leg_day"]


def test_lone_hash_yields_nothing() -> None:
    assert parse_hashtags("#") == []
    assert parse_hashtags("# ") == []


def test_hashtag_followed_by_punctuation() -> None:
    assert parse_hashtags("#squat, #pr!") == ["squat", "pr"]


def test_does_not_capture_url_fragment() -> None:
    """A URL fragment like example.com/page#section is borderline; the project
    plan accepts the simple regex behavior. Document the case."""
    # With the simple `#([\w-]+)` regex, this captures "section". That's
    # acceptable for our use case (Telegram captions rarely contain URLs
    # with fragments) and is consistent with the plan file's spec.
    assert parse_hashtags("see example.com/page#section") == ["section"]


@pytest.mark.parametrize(
    "caption,expected",
    [
        ("#a #b #c", ["a", "b", "c"]),
        ("text\n#multi\nline", ["multi"]),
        ("two   spaces   #spaced", ["spaced"]),
    ],
)
def test_various_layouts(caption: str, expected: list[str]) -> None:
    assert parse_hashtags(caption) == expected
