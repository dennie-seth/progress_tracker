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
    out = tmp_path / "out.mov"
    await compile_videos(inputs, metas, target_duration=4.0, output=out)

    assert out.exists()
    result = await probe(out)
    # Output should be ~3s (sum of inputs at 1.0x). Allow a bit of slack
    # for container overhead.
    assert Decimal("2.7") < result.duration < Decimal("3.5")


async def test_compile_output_has_audio_stream(
    sample_clips: list[Path], tmp_path: Path
) -> None:
    """iOS Photos rejects video-only mp4 — confirm we attach a silent AAC track."""
    import json
    import subprocess

    inputs = [sample_clips[0]]  # 1s clip
    metas = [ClipMeta(duration=Decimal("1.0"), date_label=None)]
    out = tmp_path / "out.mov"
    await compile_videos(inputs, metas, target_duration=2.0, output=out)

    raw = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(out),
        ],
        check=True, capture_output=True,
    )
    streams = json.loads(raw.stdout)["streams"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    assert audio_streams, "compile output must include an audio stream"
    assert audio_streams[0]["codec_name"] == "aac"


async def test_compile_speeds_up_long_clip(
    sample_clips: list[Path], tmp_path: Path
) -> None:
    """A 3s clip in a 1s budget gets sped up 3x; final reel ~2s for two clips."""
    inputs = [sample_clips[0], sample_clips[2]]  # 1s + 3s
    metas = [
        ClipMeta(duration=Decimal("1.0"), date_label=None),
        ClipMeta(duration=Decimal("3.0"), date_label=None),
    ]
    out = tmp_path / "out.mov"
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
    out = tmp_path / "out.mov"
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
    out = tmp_path / "out.mov"
    with pytest.raises(RuntimeError):
        await compile_videos(
            inputs=[tmp_path / "does-not-exist.mp4"],
            metas=[ClipMeta(duration=Decimal("1.0"), date_label=None)],
            target_duration=2.0,
            output=out,
        )


async def test_compile_output_is_quicktime_with_h264_main(
    sample_clips: list[Path], tmp_path: Path
) -> None:
    """Container + codec match what iOS Photos accepts for import."""
    import json
    import subprocess

    out = tmp_path / "out.mov"
    await compile_videos(
        inputs=[sample_clips[0]],
        metas=[ClipMeta(duration=Decimal("1.0"), date_label=None)],
        target_duration=2.0,
        output=out,
    )

    raw = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", "-show_format", str(out),
        ],
        check=True, capture_output=True,
    )
    data = json.loads(raw.stdout)
    fmt_name = data["format"]["format_name"]
    assert "mov" in fmt_name or "quicktime" in fmt_name.lower()

    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert video["codec_name"] == "h264"
    # ffprobe reports profile as a human string ("Main" / "High" / ...).
    assert video.get("profile", "").lower() == "main"
