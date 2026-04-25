"""Tests for the ffprobe wrapper. Runs ffprobe on real fixture MP4s."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from progress_tracker.video.probe import ProbeResult, probe


async def test_probe_returns_duration_close_to_expected(sample_clips: list[Path]) -> None:
    expected_durations = (Decimal("1"), Decimal("2"), Decimal("3"))
    for clip, expected in zip(sample_clips, expected_durations, strict=True):
        result = await probe(clip)
        assert isinstance(result, ProbeResult)
        # ffmpeg's container duration may drift by a frame or two from -t.
        diff = abs(result.duration - expected)
        assert diff < Decimal("0.2"), f"{clip.name}: probed {result.duration}, expected ~{expected}"


async def test_probe_returns_dimensions(sample_clips: list[Path]) -> None:
    result = await probe(sample_clips[0])
    assert result.width == 320
    assert result.height == 240


async def test_probe_raises_on_missing_file(tmp_path: Path) -> None:
    nope = tmp_path / "nope.mp4"
    with pytest.raises(RuntimeError):
        await probe(nope)


async def test_probe_extracts_creation_time_from_container(
    tmp_path: Path,
) -> None:
    """ffprobe surfaces the container's creation_time tag. Generate a clip
    via ffmpeg with an explicit `creation_time` metadata field and round-trip
    through `probe()`."""
    import subprocess
    from datetime import datetime, timezone

    out = tmp_path / "stamped.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=red:size=160x120:rate=30",
            "-t", "1",
            "-metadata", "creation_time=2024-06-15T12:34:56.000000Z",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(out),
        ],
        check=True,
    )

    result = await probe(out)
    assert result.creation_time is not None
    assert result.creation_time == datetime(
        2024, 6, 15, 12, 34, 56, tzinfo=timezone.utc
    )


async def test_probe_creation_time_none_when_missing(
    sample_clips: list[Path],
) -> None:
    """Our session-fixture clips are generated without a creation_time tag.

    ffmpeg's mov muxer auto-stamps a `creation_time` of "now", which
    ffprobe surfaces. So `creation_time` may be set on these — what we
    care about is that absence parses to None, not a crash. Use a custom
    container.
    """
    # Just exercise the parse path; the previous test covers the populated case.
    from progress_tracker.video.probe import _parse_creation_time

    assert _parse_creation_time(None) is None
    assert _parse_creation_time("") is None
    assert _parse_creation_time("not-a-date") is None
    # Naive datetimes get coerced to UTC so callers can compare safely.
    parsed = _parse_creation_time("2025-01-02T03:04:05")
    assert parsed is not None and parsed.tzinfo is not None
