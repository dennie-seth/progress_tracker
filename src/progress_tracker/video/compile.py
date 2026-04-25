"""ffmpeg `-filter_complex` graph builder + subprocess runner.

The compile step is a single ffmpeg invocation. Per CLAUDE.md, we don't
bring in MoviePy — the entire pipeline (normalize → optional speedup →
optional date overlay → concat) lives in one filter graph string and one
subprocess.

This module is intentionally split between **pure** helpers (filter graph,
atempo chain, command builder) and the **impure** runner (`compile_videos`,
which shells out). The pure parts have fast unit tests; the runner has an
end-to-end test on small fixture MP4s.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import structlog

_log = structlog.get_logger("progress_tracker.video.compile")


# Output canonical size — vertical 9:16, mobile-first; matches the plan.
_OUTPUT_WIDTH = 1080
_OUTPUT_HEIGHT = 1920
_OUTPUT_FPS = 30


@dataclass(frozen=True)
class ClipMeta:
    """Per-clip data the filter graph needs.

    `duration` is what ffprobe returned (seconds, sub-second precision).
    `date_label` is the optional drawtext text — None means no overlay.
    """

    duration: Decimal
    date_label: str | None


# ---------- pure helpers ----------


def atempo_chain(speed: float) -> list[str]:
    """Return atempo filter strings whose product equals `speed`.

    ffmpeg's `atempo` is bounded to [0.5, 2.0] per filter; chain when
    outside. We only ever speed up in this project, so the slow-down
    branch is defensive.

    speed == 1.0 returns an empty list (no-op — caller can omit the
    audio leg's filter chain entirely).
    """
    if speed <= 0:
        raise ValueError(f"atempo speed must be positive, got {speed}")
    if abs(speed - 1.0) < 1e-6:
        return []
    if 0.5 <= speed <= 2.0:
        return [f"atempo={speed:.6f}"]
    if speed > 2.0:
        chain: list[str] = []
        remaining = speed
        while remaining > 2.0:
            chain.append("atempo=2.000000")
            remaining /= 2.0
        if abs(remaining - 1.0) > 1e-6:
            chain.append(f"atempo={remaining:.6f}")
        return chain
    # speed < 0.5 — chain 0.5 filters
    chain = []
    remaining = speed
    while remaining < 0.5:
        chain.append("atempo=0.500000")
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-6:
        chain.append(f"atempo={remaining:.6f}")
    return chain


def _escape_drawtext(text: str) -> str:
    """Escape characters ffmpeg's drawtext filter treats as special."""
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def build_filter_complex(
    clips: Sequence[ClipMeta],
    target_duration: float,
) -> str:
    """Build the `-filter_complex` argument for the compile.

    Each clip is normalized (scale → pad → setsar → fps), optionally sped
    up if its duration exceeds the per-clip budget, optionally overlaid
    with the drawtext date label, then all video streams are concatenated
    to `[outv]`. Audio is intentionally dropped — many training clips are
    silent (no audio stream at all), and uniform `concat=n:v=1:a=1` fails
    when even one input has no audio. A silent reel is what users want
    anyway. `atempo` is kept in the public API for a future audio path.
    """
    n = len(clips)
    if n == 0:
        raise ValueError("build_filter_complex requires at least one clip")
    if target_duration <= 0:
        raise ValueError(f"target_duration must be positive, got {target_duration}")

    budget_per_clip = target_duration / n
    parts: list[str] = []
    concat_inputs: list[str] = []

    for i, clip in enumerate(clips):
        # 1) Normalize geometry/fps so concat can stitch differently-shaped inputs.
        v_label = f"v{i}n"
        parts.append(
            f"[{i}:v]"
            f"scale={_OUTPUT_WIDTH}:{_OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={_OUTPUT_WIDTH}:{_OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,"
            f"fps={_OUTPUT_FPS}"
            f"[{v_label}]"
        )

        # 2) Speedup if needed (video PTS only — audio is dropped).
        speed = float(clip.duration) / budget_per_clip
        if speed > 1.0 + 1e-6:
            v_speed = f"v{i}s"
            parts.append(f"[{v_label}]setpts=PTS/{speed:.6f}[{v_speed}]")
            v_label = v_speed

        # 3) Drawtext overlay (top-left, white-on-translucent-black box).
        if clip.date_label is not None:
            v_text = f"v{i}t"
            text = _escape_drawtext(clip.date_label)
            parts.append(
                f"[{v_label}]drawtext=text='{text}'"
                f":x=40:y=40:fontsize=48:fontcolor=white"
                f":box=1:boxcolor=black@0.5"
                f"[{v_text}]"
            )
            v_label = v_text

        concat_inputs.append(f"[{v_label}]")

    parts.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[outv]")
    return ";".join(parts)


# ---------- impure runner ----------


def build_ffmpeg_args(
    inputs: Sequence[Path],
    filter_complex: str,
    output: Path,
) -> list[str]:
    """Assemble the argv for the ffmpeg subprocess. Pulled out for testability.

    A silent stereo `anullsrc` is added as the last input and mapped to the
    output as a real AAC track. iOS Photos and several other consumers refuse
    to import video-only `.mp4` files; attaching silence costs ~1 KB and
    makes the output behave like every other phone-shot clip.
    """
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        args.extend(["-i", str(path)])

    # Append the silent-audio input *after* all video inputs so its index in
    # the ffmpeg argument list is `len(inputs)` — that's what `-map` references
    # below. anullsrc is infinite; `-shortest` will truncate to the video.
    silent_input_index = len(inputs)
    args.extend(
        [
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
    )

    args.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            f"{silent_input_index}:a",
            "-shortest",  # output ends when video ends; anullsrc would run forever
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return args


async def compile_videos(
    inputs: Sequence[Path],
    metas: Sequence[ClipMeta],
    target_duration: float,
    output: Path,
) -> None:
    """Run ffmpeg with the assembled filter graph; raise on failure.

    `inputs` and `metas` must be parallel sequences of the same length.
    """
    if len(inputs) != len(metas):
        raise ValueError(
            f"inputs ({len(inputs)}) and metas ({len(metas)}) length mismatch"
        )
    fc = build_filter_complex(metas, target_duration)
    args = build_ffmpeg_args(inputs, fc, output)
    _log.info("running ffmpeg", clips=len(inputs), target=target_duration, output=str(output))

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited {proc.returncode}: {stderr.decode(errors='replace')}"
        )
