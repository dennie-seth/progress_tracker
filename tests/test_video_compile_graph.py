"""Pure tests for the filter_complex graph builder + atempo helper.

No ffmpeg invocation — these are string assertions on the graph we'd ask
ffmpeg to run. The end-to-end test that actually shells out is in
`test_video_compile_e2e.py`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pathlib import Path

from progress_tracker.video.compile import (
    ClipMeta,
    atempo_chain,
    build_ffmpeg_args,
    build_filter_complex,
)


# ---------- atempo_chain ----------


def test_atempo_chain_speed_one_is_noop() -> None:
    assert atempo_chain(1.0) == []


def test_atempo_chain_within_native_range() -> None:
    chain = atempo_chain(1.5)
    assert len(chain) == 1
    assert chain[0].startswith("atempo=")


def test_atempo_chain_at_2x_single_filter() -> None:
    assert atempo_chain(2.0) == ["atempo=2.000000"]


def test_atempo_chain_at_4x_two_2x_filters() -> None:
    assert atempo_chain(4.0) == ["atempo=2.000000", "atempo=2.000000"]


def test_atempo_chain_at_3x_uses_2x_then_1_5x() -> None:
    chain = atempo_chain(3.0)
    assert chain[0] == "atempo=2.000000"
    assert chain[-1].startswith("atempo=1.5")


def test_atempo_chain_at_8x_three_2x_filters() -> None:
    assert atempo_chain(8.0) == ["atempo=2.000000"] * 3


def test_atempo_chain_rejects_zero_or_negative() -> None:
    with pytest.raises(ValueError):
        atempo_chain(0)
    with pytest.raises(ValueError):
        atempo_chain(-1.0)


# ---------- build_filter_complex ----------


def _meta(duration: float) -> ClipMeta:
    return ClipMeta(duration=Decimal(str(duration)), date_label=None)


def test_filter_complex_includes_concat_for_n_inputs() -> None:
    metas = [_meta(2.0), _meta(2.0), _meta(2.0)]
    fc = build_filter_complex(metas, target_duration=6.0)
    # Video-only concat — audio is intentionally dropped (clips may be silent).
    assert "concat=n=3:v=1:a=0" in fc
    assert "[outv]" in fc
    assert "[outa]" not in fc


def test_filter_complex_keeps_full_speed_when_clip_fits_budget() -> None:
    """Clip duration == target/N → no setpts applied."""
    metas = [_meta(5.0), _meta(5.0)]  # budget = 5s, duration = 5s
    fc = build_filter_complex(metas, target_duration=10.0)
    assert "setpts=" not in fc


def test_filter_complex_speeds_up_long_clips() -> None:
    """Clip 10s, budget 5s → speed factor 2 → setpts=PTS/2."""
    metas = [_meta(10.0), _meta(5.0)]  # budget = 5s
    fc = build_filter_complex(metas, target_duration=10.0)
    # The first clip is sped up by 2x via PTS divisor.
    assert "setpts=PTS/2" in fc


def test_filter_complex_omits_audio_streams_entirely() -> None:
    """No `[i:a]` references anywhere — that's exactly what broke on inputs
    with no audio stream and is the reason audio is dropped."""
    metas = [_meta(2.0), _meta(2.0)]
    fc = build_filter_complex(metas, target_duration=4.0)
    assert ":a]" not in fc
    assert "atempo=" not in fc
    assert "anull" not in fc


def test_filter_complex_drawtext_when_overlay_set() -> None:
    metas = [
        ClipMeta(duration=Decimal("2.0"), date_label="2026-01-15"),
        ClipMeta(duration=Decimal("2.0"), date_label="2026-04-20"),
    ]
    fc = build_filter_complex(metas, target_duration=4.0)
    assert "drawtext=" in fc
    assert "2026-01-15" in fc
    assert "2026-04-20" in fc


def test_filter_complex_no_drawtext_when_overlay_omitted() -> None:
    metas = [_meta(2.0), _meta(2.0)]
    fc = build_filter_complex(metas, target_duration=4.0)
    assert "drawtext=" not in fc


def test_filter_complex_includes_normalize_per_input() -> None:
    """Each input gets scaled+padded to a common size so concat doesn't fail."""
    metas = [_meta(2.0), _meta(2.0)]
    fc = build_filter_complex(metas, target_duration=4.0)
    # We use scale + pad + setsar; check at least one of the canonical filters.
    assert "scale=" in fc
    assert "setsar=1" in fc


def test_filter_complex_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        build_filter_complex([], target_duration=10.0)


def test_filter_complex_rejects_zero_target() -> None:
    with pytest.raises(ValueError):
        build_filter_complex([_meta(2.0)], target_duration=0)


# ---------- build_ffmpeg_args ----------


def test_ffmpeg_args_attach_silent_audio_input() -> None:
    """iOS Photos rejects video-only mp4 — we add a silent AAC track."""
    args = build_ffmpeg_args(
        inputs=[Path("a.mp4"), Path("b.mp4")],
        filter_complex="dummy",
        output=Path("out.mp4"),
    )
    # The lavfi anullsrc input is added after the real video inputs.
    assert "-f" in args
    lavfi_idx = args.index("-f")
    assert args[lavfi_idx + 1] == "lavfi"
    assert any("anullsrc" in a for a in args)
    # Audio is mapped from input index 2 (= len(inputs)).
    assert "-map" in args
    assert "2:a" in args
    # AAC, with -shortest so anullsrc doesn't run the output forever.
    assert "-c:a" in args
    assert "aac" in args
    assert "-shortest" in args


def test_ffmpeg_args_keep_libx264_yuv420p_faststart() -> None:
    """Make sure the iOS-compatible video defaults haven't regressed."""
    args = build_ffmpeg_args(
        inputs=[Path("a.mp4")],
        filter_complex="x",
        output=Path("o.mp4"),
    )
    assert "libx264" in args
    assert "yuv420p" in args
    assert "+faststart" in args
