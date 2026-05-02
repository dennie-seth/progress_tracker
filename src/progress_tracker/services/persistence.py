"""Persist + recover bot state across DB loss.

Postgres lives in a per-host docker named volume (`db_data`). When the
operator migrates between VDS hosts, the DB goes with the old host while
video files in `<MEDIA_DIR>/<user_id>/...` survive (they're on a bind
mount). This module makes that recoverable.

Two complementary mechanisms:

1. **Per-user JSON manifest** (`<user_id>/manifest.json`) dumped after
   every successful ingest, every successful delete, and on graceful
   shutdown. Captures `User`, `Tag`s, `Video`s, video↔tag links — the
   fields filenames can't carry (`telegram_file_id`, `caption`,
   `created_at`).
2. **Tag-encoded video filenames** (`<user_id>/<sorted_tag_slugs>.<uuid>.mp4`)
   so a manifest-less recovery still rebuilds tags + video↔tag links from
   the files themselves, with `telegram_file_id=""`, `created_at` from
   filesystem mtime, and `duration_sec` from a fresh ffprobe.

Recovery runs once at startup when `videos` is empty. Past compilations
are out of scope — their rendered .mov files survive on disk; users
re-run `/compile` to rebuild rows.

Tag IDs are NOT preserved (Postgres reassigns on insert). User IDs ARE
preserved (`User.id` is the Telegram user_id, stable). Video IDs ARE
preserved (UUIDs we generated). Compilations from before the wipe lose
their tag link via `tag_id ON DELETE SET NULL` — out of scope here.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from progress_tracker.db.models import Tag, User, Video, VideoTag
from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.storage.base import Storage
from progress_tracker.video.probe import probe

_log = structlog.get_logger("progress_tracker.persistence")


MANIFEST_SCHEMA_VERSION = 1
MANIFEST_BASENAME = "manifest.json"
_COMPILATIONS_DIRNAME = "compilations"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class SchemaVersionMismatch(Exception):
    """Raised when an on-disk manifest's schema_version doesn't match this build.

    Fail-fast at startup rather than risk a silent partial restore against a
    schema this code doesn't understand.
    """


# ---------- pure helpers ----------


def build_storage_key(
    user_id: int, tag_names: Sequence[str], video_id: uuid.UUID
) -> str:
    """Construct the storage key for a freshly-ingested video.

    Format: ``<user_id>/<sorted_unique_tag_slugs_dot_joined>.<uuid>.mp4``.
    The tag slugs come straight from `parse_hashtags` in canonical
    underscore form. Sorting + de-duping ensures the same tag set always
    yields the same filename — handy for stable diffs and human-readable
    listings.
    """
    if not tag_names:
        raise ValueError("build_storage_key requires at least one tag")
    chain = ".".join(sorted(set(tag_names)))
    return f"{user_id}/{chain}.{video_id}.mp4"


def parse_video_filename(basename: str) -> tuple[list[str], uuid.UUID] | None:
    """Reverse of `build_storage_key`'s basename portion.

    Returns ``(tag_names, video_uuid)`` for filenames matching
    ``<tag1>.<tag2>....<uuid>.mp4`` (≥ 1 tag, strict UUID at the end).
    Returns ``None`` for anything else (manifest.json, garbage,
    legacy uuid-only filenames, non-mp4 files).
    """
    if not basename.endswith(".mp4"):
        return None
    stem = basename[:-4]
    parts = stem.split(".")
    if len(parts) < 2:
        return None
    uuid_str = parts[-1]
    if not _UUID_RE.match(uuid_str):
        return None
    try:
        vid_uuid = uuid.UUID(uuid_str)
    except ValueError:
        return None
    tag_names = parts[:-1]
    if not tag_names or any(not t for t in tag_names):
        return None
    return tag_names, vid_uuid


def atomic_write_text(target: Path, contents: str) -> None:
    """Write text to ``target`` atomically.

    Writes to a sibling ``.tmp`` then ``os.replace``-es into place. A
    SIGKILL between write and replace leaves the previous valid file
    intact rather than a truncated half-written one.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contents)
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


# ---------- manifest dataclasses ----------


@dataclass(frozen=True)
class ManifestUser:
    id: int
    username: str | None
    first_name: str | None
    locale: str | None
    created_at: datetime


@dataclass(frozen=True)
class ManifestVideo:
    id: str  # UUID as canonical string
    telegram_file_id: str
    storage_key: str
    duration_sec: str  # `Decimal` as string for JSON cleanliness
    width: int | None
    height: int | None
    fps: str | None  # `Decimal` as string, or None
    caption: str | None
    created_at: datetime
    tag_names: list[str]


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    user: ManifestUser
    # Tags carry their original `created_at` so the restored row matches
    # what it would have been pre-wipe. We don't preserve tag IDs (Postgres
    # reassigns), so this is just a list of `{name, created_at}` dicts.
    tags: list[dict[str, str]]
    videos: list[ManifestVideo]

    def to_json(self) -> str:
        def _default(o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            raise TypeError(f"unserialisable type: {type(o).__name__}")

        return json.dumps(asdict(self), default=_default, ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Manifest:
        version = raw.get("schema_version")
        if version != MANIFEST_SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"manifest schema_version {version!r} != "
                f"this build's {MANIFEST_SCHEMA_VERSION}"
            )
        user = ManifestUser(
            id=int(raw["user"]["id"]),
            username=raw["user"].get("username"),
            first_name=raw["user"].get("first_name"),
            locale=raw["user"].get("locale"),
            created_at=_parse_iso(raw["user"]["created_at"]),
        )
        videos = [
            ManifestVideo(
                id=v["id"],
                telegram_file_id=v["telegram_file_id"],
                storage_key=v["storage_key"],
                duration_sec=v["duration_sec"],
                width=v.get("width"),
                height=v.get("height"),
                fps=v.get("fps"),
                caption=v.get("caption"),
                created_at=_parse_iso(v["created_at"]),
                tag_names=list(v["tag_names"]),
            )
            for v in raw.get("videos", [])
        ]
        return cls(
            schema_version=version,
            user=user,
            tags=[dict(t) for t in raw.get("tags", [])],
            videos=videos,
        )


def _parse_iso(s: str) -> datetime:
    """Parse ISO-8601; default to UTC for naive strings."""
    candidate = s.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# ---------- dump ----------


async def dump_user_manifest(
    session: AsyncSession, storage: Storage, *, user_id: int
) -> None:
    """Serialize the user's full state and atomically write the manifest.

    Idempotent — overwrites any previous manifest. Reads the current state
    of the user from the session (must reflect committed data).
    """
    user = await session.get(User, user_id)
    if user is None:
        # No-op when the user has been removed; a stale manifest will be
        # picked up by a future cleanup, not here.
        _log.debug("dump_user_manifest: no such user", user_id=user_id)
        return

    tags = (
        (
            await session.execute(
                select(Tag).where(Tag.user_id == user_id).order_by(Tag.name)
            )
        )
        .scalars()
        .all()
    )

    video_rows = (
        (
            await session.execute(
                select(Video).where(Video.user_id == user_id).order_by(Video.created_at)
            )
        )
        .scalars()
        .all()
    )

    # Tag names per video — separate query so we don't depend on ORM lazy
    # loading inside async sessions.
    video_tag_rows = (
        await session.execute(
            select(VideoTag.video_id, Tag.name)
            .join(Tag, Tag.id == VideoTag.tag_id)
            .where(Tag.user_id == user_id)
        )
    ).all()
    tags_by_video: dict[uuid.UUID, list[str]] = {}
    for vid, name in video_tag_rows:
        tags_by_video.setdefault(vid, []).append(name)

    manifest = Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        user=ManifestUser(
            id=user.id,
            username=user.username,
            first_name=user.first_name,
            locale=user.locale,
            created_at=user.created_at,
        ),
        tags=[{"name": t.name, "created_at": t.created_at.isoformat()} for t in tags],
        videos=[
            ManifestVideo(
                id=str(v.id),
                telegram_file_id=v.telegram_file_id,
                storage_key=v.storage_key,
                duration_sec=str(v.duration_sec),
                width=v.width,
                height=v.height,
                fps=str(v.fps) if v.fps is not None else None,
                caption=v.caption,
                created_at=v.created_at,
                tag_names=sorted(tags_by_video.get(v.id, [])),
            )
            for v in video_rows
        ],
    )

    target = await storage.write_path(f"{user_id}/{MANIFEST_BASENAME}")
    atomic_write_text(target, manifest.to_json())
    _log.debug(
        "manifest dumped",
        user_id=user_id,
        videos=len(manifest.videos),
        tags=len(manifest.tags),
    )


# ---------- recovery ----------


@dataclass
class RecoveryReport:
    skipped: bool = False
    users_restored: int = 0
    videos_via_manifest: int = 0
    videos_via_filename: int = 0


async def recover_from_storage(
    session_factory: async_sessionmaker[AsyncSession], media_root: Path
) -> RecoveryReport:
    """Restore users / tags / videos from disk when the DB is empty.

    Single transaction. If anything fails, rollback leaves the DB clean
    so the next startup retries from scratch. No-op when the `videos`
    table already has rows — a one-shot bootstrap, not a merge.
    """
    # Startup-only filesystem walk; blocking is fine because polling hasn't
    # started and the loop has nothing to serve. ASYNC240 is suppressed
    # accordingly throughout this function.
    if not media_root.exists():  # noqa: ASYNC240
        _log.info("recover: media root absent, nothing to restore", root=str(media_root))
        return RecoveryReport(skipped=True)

    async with session_factory() as session:
        already = (
            await session.execute(select(exists().select_from(Video)))
        ).scalar()
        if already:
            _log.info("recover: videos table non-empty, skipping")
            return RecoveryReport(skipped=True)

        report = RecoveryReport(skipped=False)
        for user_dir in sorted(media_root.iterdir()):  # noqa: ASYNC240
            if not user_dir.is_dir():
                continue
            try:
                user_id = int(user_dir.name)
            except ValueError:
                _log.debug("recover: skipping non-numeric dir", dir=str(user_dir))
                continue
            if user_id <= 0:
                continue
            covered, restored_videos = await _restore_user(
                session, user_id, user_dir
            )
            if restored_videos > 0 or covered > 0:
                report.users_restored += 1
            report.videos_via_manifest += covered
            report.videos_via_filename += restored_videos - covered

        await session.commit()
        _log.info(
            "recover: complete",
            users=report.users_restored,
            videos_manifest=report.videos_via_manifest,
            videos_filename=report.videos_via_filename,
        )
        _write_recovery_log(media_root, report)
        return report


async def _restore_user(
    session: AsyncSession, user_id: int, user_dir: Path
) -> tuple[int, int]:
    """Restore one user's state. Returns (videos_via_manifest, total_videos)."""
    manifest_path = user_dir / MANIFEST_BASENAME
    covered_uuids: set[uuid.UUID] = set()
    videos_from_manifest = 0
    if manifest_path.exists():
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = Manifest.from_dict(raw)  # raises SchemaVersionMismatch
        await _restore_from_manifest(session, manifest)
        covered_uuids = {uuid.UUID(v.id) for v in manifest.videos}
        videos_from_manifest = len(covered_uuids)

    videos_from_filename = 0
    for video_file in sorted(user_dir.glob("*.mp4")):  # noqa: ASYNC240
        # Belt: glob is non-recursive, but defend if someone moves files
        # around at runtime.
        if video_file.parent.name == _COMPILATIONS_DIRNAME:
            continue
        parsed = parse_video_filename(video_file.name)
        if parsed is None:
            continue
        tag_names, vid_uuid = parsed
        if vid_uuid in covered_uuids:
            continue
        await _restore_orphan_video(
            session,
            user_id=user_id,
            video_id=vid_uuid,
            tag_names=tag_names,
            video_file=video_file,
        )
        videos_from_filename += 1

    return videos_from_manifest, videos_from_manifest + videos_from_filename


async def _restore_from_manifest(session: AsyncSession, manifest: Manifest) -> None:
    """Insert User + Tags + Videos + video↔tag links from a manifest, preserving
    timestamps. Tag IDs are reassigned by Postgres — the link rows resolve by
    name."""
    user_repo = UserRepo(session)
    tag_repo = TagRepo(session)
    video_repo = VideoRepo(session)

    user_row = await user_repo.upsert(
        user_id=manifest.user.id,
        username=manifest.user.username,
        first_name=manifest.user.first_name,
        created_at=manifest.user.created_at,
    )
    # Add tags listed in the manifest top-level (covers tags with no current
    # videos, e.g. all videos for that tag were deleted before the wipe).
    tag_names = sorted({t["name"] for t in manifest.tags})
    tags = await tag_repo.upsert_many(
        user_row.id,
        tag_names,
        created_at_by_name={
            t["name"]: _parse_iso(t["created_at"]) for t in manifest.tags
        },
    )
    tag_by_name = {t.name: t for t in tags}

    for v in manifest.videos:
        # Ensure each video's tag rows exist (manifest top-level tags +
        # any per-video tags should be the same set, but be tolerant).
        video_tag_objs = []
        for name in v.tag_names:
            t = tag_by_name.get(name)
            if t is None:
                # Tag missing from top-level — upsert it now.
                [t] = await tag_repo.upsert_many(user_row.id, [name])
                tag_by_name[name] = t
            video_tag_objs.append(t)
        await video_repo.create(
            id=uuid.UUID(v.id),
            user_id=user_row.id,
            telegram_file_id=v.telegram_file_id,
            storage_key=v.storage_key,
            duration_sec=Decimal(v.duration_sec),
            width=v.width,
            height=v.height,
            fps=Decimal(v.fps) if v.fps else None,
            caption=v.caption,
            tag_ids=[t.id for t in video_tag_objs],
            created_at=v.created_at,
        )


async def _restore_orphan_video(
    session: AsyncSession,
    *,
    user_id: int,
    video_id: uuid.UUID,
    tag_names: list[str],
    video_file: Path,
) -> None:
    """Filename-only recovery: minimal viable Video row + Tag links + ffprobe.

    `telegram_file_id` is empty (the cached Telegram file_id is gone with the
    wipe — `delete_flow` falls back to FSInputFile when this is falsy).
    `created_at` from filesystem mtime. `duration_sec` from a fresh ffprobe
    so the compile path's per-clip budget math still works.
    """
    user_repo = UserRepo(session)
    tag_repo = TagRepo(session)
    video_repo = VideoRepo(session)

    user_row = await user_repo.upsert(
        user_id=user_id, username=None, first_name=None
    )
    tags = await tag_repo.upsert_many(user_row.id, tag_names)
    tag_ids = [t.id for t in tags]

    probed = await probe(video_file)
    mtime = datetime.fromtimestamp(video_file.stat().st_mtime, tz=UTC)  # noqa: ASYNC240
    relative_key = str(video_file.relative_to(video_file.parents[1])).replace("\\", "/")

    await video_repo.create(
        id=video_id,
        user_id=user_row.id,
        telegram_file_id="",
        storage_key=relative_key,
        duration_sec=probed.duration,
        width=probed.width,
        height=probed.height,
        fps=probed.fps,
        caption=None,
        tag_ids=tag_ids,
        created_at=mtime,
    )


def _write_recovery_log(media_root: Path, report: RecoveryReport) -> None:
    """Append a one-line summary to `<MEDIA_DIR>/.recovery.log` for forensics."""
    if report.skipped:
        return
    log_path = media_root / ".recovery.log"
    line = (
        f"{datetime.now(UTC).isoformat()} "
        f"users={report.users_restored} "
        f"videos_manifest={report.videos_via_manifest} "
        f"videos_filename={report.videos_via_filename}\n"
    )
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        # Don't let a logging failure derail recovery — just warn.
        _log.warning("could not write .recovery.log", path=str(log_path), exc_info=True)
