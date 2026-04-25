"""ffprobe wrapper — extracts duration, dimensions, fps from a video file."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class ProbeResult:
    duration: Decimal
    width: int
    height: int
    fps: Decimal | None
    # When the file was originally recorded — read from the container's
    # `creation_time` tag (iPhones, Androids, and most cameras write this).
    # `None` when the tag is absent or unparseable; callers should fall back
    # to the upload time.
    creation_time: datetime | None = None


def _parse_creation_time(raw: str | None) -> datetime | None:
    """Parse `creation_time` tag values like '2025-04-15T10:30:00.000000Z'.

    ffprobe normalizes most container timestamps into ISO-8601 with a `Z`
    suffix, but the format isn't guaranteed — return None on anything we
    can't parse rather than blowing up the whole probe.
    """
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Most container metadata is UTC; default to that rather than naive.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


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

    # `creation_time` may live on the format or on the video stream depending
    # on the container. Try both, format first.
    creation_raw = (
        fmt.get("tags", {}).get("creation_time")
        or video_stream.get("tags", {}).get("creation_time")
    )
    creation_time = _parse_creation_time(creation_raw)

    return ProbeResult(
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        creation_time=creation_time,
    )
