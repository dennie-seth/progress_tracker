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
