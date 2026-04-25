"""ffprobe wrapper — extracts duration, dimensions, fps from a video file."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class ProbeResult:
    duration: Decimal
    width: int
    height: int
    fps: Decimal | None


async def probe(path: Path) -> ProbeResult:
    """Run `ffprobe -print_format json -show_format -show_streams` and parse."""
    args = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path}: {stderr.decode(errors='replace')}"
        )

    data = json.loads(stdout)
    fmt = data.get("format", {})
    duration = Decimal(fmt.get("duration", "0"))

    streams = data.get("streams", [])
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise RuntimeError(f"{path} has no video stream")

    width = int(video_stream["width"])
    height = int(video_stream["height"])
    fps_raw = video_stream.get("r_frame_rate", "0/1")
    if "/" in fps_raw:
        num, den = fps_raw.split("/", 1)
        fps = (
            Decimal(num) / Decimal(den)
            if Decimal(den) > 0
            else None
        )
    else:
        fps = Decimal(fps_raw)

    return ProbeResult(duration=duration, width=width, height=height, fps=fps)
