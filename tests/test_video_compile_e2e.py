"""End-to-end compile tests — actually shells out to ffmpeg.

These run the full pipeline against the fixture clips and probe the output
so we know speedup math, normalization, concat, and the optional drawtext
overlay all hang together. Slower than the pure tests; ~2-5 seconds each.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from progress_tracker.video.compile import ClipMeta, compile_videos
from progress_tracker.video.probe import probe


async def test_compile_two_clips_fits_target_duration(
    sample_clips: list[Path], tmp_path: Path
) -> None:
    """Two 1s + 2s clips into a 4s reel — no speedup, no overlay."""
    inputs = [sample_clips[0], sample_clips[1]]  # 1s + 2s
    metas = [
        ClipMeta(duration=Decimal("1.0"), date_label=None),
        ClipMeta(duration=Decimal("2.0"), date_label=None),
    ]
    out = tmp_path / "out.mp4"
    await compile_videos(inputs, metas, target_duration=4.0, output=out)

    assert out.exists()
    result = await probe(out)
    # Output should be ~3s (sum of inputs at 1.0x). Allow a bit of slack
    # for container overhead.
    assert Decimal("2.7") < result.duration < Decimal("3.5")


async def test_compile_speeds_up_long_clip(
    sample_clips: list[Path], tmp_path: Path
) -> None:
    """A 3s clip in a 1s budget gets sped up 3x; final reel ~2s for two clips."""
    inputs = [sample_clips[0], sample_clips[2]]  # 1s + 3s
    metas = [
        ClipMeta(duration=Decimal("1.0"), date_label=None),
        ClipMeta(duration=Decimal("3.0"), date_label=None),
    ]
    out = tmp_path / "out.mp4"
    await compile_videos(inputs, metas, target_duration=2.0, output=out)

    assert out.exists()
    result = await probe(out)
    # 1s + 1s (3s sped 3x) ≈ 2s. Slack of ~30%.
    assert Decimal("1.5") < result.duration < Decimal("2.7")


async def test_compile_with_drawtext_overlay_succeeds(
    sample_clips: list[Path], tmp_path: Path
) -> None:
    """Drawtext on each clip — verifies the filter graph is well-formed."""
    inputs = [sample_clips[0], sample_clips[1]]
    metas = [
        ClipMeta(duration=Decimal("1.0"), date_label="2026-01-15"),
        ClipMeta(duration=Decimal("2.0"), date_label="2026-04-26"),
    ]
    out = tmp_path / "out.mp4"
    await compile_videos(inputs, metas, target_duration=4.0, output=out)
    assert out.exists()
    # Just confirm we got a real video back; visual-correctness of overlay
    # isn't unit-testable.
    result = await probe(out)
    assert result.duration > Decimal("0")


async def test_compile_raises_on_mismatched_inputs_and_metas(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await compile_videos(
            inputs=[tmp_path / "a.mp4"],
            metas=[],
            target_duration=10.0,
            output=tmp_path / "out.mp4",
        )


async def test_compile_raises_when_ffmpeg_fails(tmp_path: Path) -> None:
    """A non-existent input causes ffmpeg to bail; we surface that as RuntimeError."""
    out = tmp_path / "out.mp4"
    with pytest.raises(RuntimeError):
        await compile_videos(
            inputs=[tmp_path / "does-not-exist.mp4"],
            metas=[ClipMeta(duration=Decimal("1.0"), date_label=None)],
            target_duration=2.0,
            output=out,
        )
