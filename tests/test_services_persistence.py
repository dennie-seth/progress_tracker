"""Tests for services.persistence — manifest dump/load + recovery.

The pure helpers (`build_storage_key`, `parse_video_filename`, manifest
ser/de, atomic write) are unit-tested at the top. The DB-touching paths
(`dump_user_manifest`, `recover_from_storage`) use the real Postgres
fixture + LocalStorage from the project conftest.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from progress_tracker.db.models import Tag, User, Video
from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo
from progress_tracker.db.session import create_session_factory
from progress_tracker.services.persistence import (
    MANIFEST_SCHEMA_VERSION,
    Manifest,
    ManifestUser,
    ManifestVideo,
    SchemaVersionMismatch,
    atomic_write_text,
    build_storage_key,
    dump_user_manifest,
    parse_video_filename,
    recover_from_storage,
)
from progress_tracker.storage.local import LocalStorage

# ---------- build_storage_key ----------


def test_build_storage_key_single_tag() -> None:
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    assert build_storage_key(7, ["squat"], vid) == f"7/squat.{vid}.mp4"


def test_build_storage_key_sorts_tag_names() -> None:
    """Same tag set always produces the same filename, regardless of input order."""
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    assert (
        build_storage_key(7, ["pushup", "abs"], vid)
        == build_storage_key(7, ["abs", "pushup"], vid)
        == f"7/abs.pushup.{vid}.mp4"
    )


def test_build_storage_key_dedupes_tags() -> None:
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    assert build_storage_key(7, ["squat", "squat"], vid) == f"7/squat.{vid}.mp4"


def test_build_storage_key_rejects_empty_tag_list() -> None:
    """Ingest already enforces ≥1 tag, but encode the invariant in this helper."""
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    with pytest.raises(ValueError):
        build_storage_key(7, [], vid)


def test_build_storage_key_underscore_tags_pass_through() -> None:
    """Tags arrive in canonical form from parse_hashtags (no `-`); passed through."""
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    assert (
        build_storage_key(7, ["bachata_basic", "lift"], vid)
        == f"7/bachata_basic.lift.{vid}.mp4"
    )


# ---------- parse_video_filename ----------


def test_parse_video_filename_single_tag() -> None:
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    parsed = parse_video_filename(f"squat.{vid}.mp4")
    assert parsed is not None
    tags, parsed_uuid = parsed
    assert tags == ["squat"]
    assert parsed_uuid == vid


def test_parse_video_filename_multiple_tags() -> None:
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    parsed = parse_video_filename(f"abs.pushup.{vid}.mp4")
    assert parsed is not None
    tags, parsed_uuid = parsed
    assert tags == ["abs", "pushup"]
    assert parsed_uuid == vid


def test_parse_video_filename_underscore_tag() -> None:
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    parsed = parse_video_filename(f"bachata_basic.{vid}.mp4")
    assert parsed is not None
    tags, parsed_uuid = parsed
    assert tags == ["bachata_basic"]
    assert parsed_uuid == vid


def test_parse_video_filename_rejects_manifest_json() -> None:
    assert parse_video_filename("manifest.json") is None


def test_parse_video_filename_rejects_garbage() -> None:
    assert parse_video_filename("random.txt") is None
    assert parse_video_filename("squat.mp4") is None  # no UUID
    assert parse_video_filename("squat.not-a-uuid.mp4") is None


def test_parse_video_filename_rejects_uuid_only() -> None:
    """Old-format filenames had `<uuid>.mp4` with no tag prefix. Recovery
    should ignore them — no way to know which tags they belonged to."""
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    assert parse_video_filename(f"{vid}.mp4") is None


def test_parse_video_filename_uuid_case_insensitive() -> None:
    vid = uuid.UUID("12345678-1234-1234-1234-123456789ABC")
    parsed = parse_video_filename("squat.12345678-1234-1234-1234-123456789ABC.mp4")
    assert parsed is not None
    _, parsed_uuid = parsed
    assert parsed_uuid == vid


# ---------- Manifest dataclass JSON round-trip ----------


def _full_manifest() -> Manifest:
    base = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    return Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        user=ManifestUser(
            id=42,
            username="alice",
            first_name="Alice",
            locale=None,
            created_at=base,
        ),
        tags=[{"name": "squat", "created_at": base.isoformat()}],
        videos=[
            ManifestVideo(
                id="12345678-1234-1234-1234-123456789abc",
                telegram_file_id="BAACAg-EXAMPLE",
                storage_key="42/squat.12345678-1234-1234-1234-123456789abc.mp4",
                duration_sec="12.345",
                width=1080,
                height=1920,
                fps=None,
                caption="felt strong",
                created_at=base + timedelta(days=1),
                tag_names=["squat"],
            )
        ],
    )


def test_manifest_round_trip_full() -> None:
    m = _full_manifest()
    serialized = m.to_json()
    parsed = json.loads(serialized)
    assert parsed["schema_version"] == MANIFEST_SCHEMA_VERSION
    restored = Manifest.from_dict(parsed)
    assert restored == m


def test_manifest_round_trip_minimal_video() -> None:
    base = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    m = Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        user=ManifestUser(id=99, username=None, first_name=None, locale=None, created_at=base),
        tags=[{"name": "x", "created_at": base.isoformat()}],
        videos=[
            ManifestVideo(
                id="12345678-1234-1234-1234-123456789abc",
                telegram_file_id="",
                storage_key="99/x.12345678-1234-1234-1234-123456789abc.mp4",
                duration_sec="1.0",
                width=None,
                height=None,
                fps=None,
                caption=None,
                created_at=base,
                tag_names=["x"],
            )
        ],
    )
    parsed = json.loads(m.to_json())
    assert Manifest.from_dict(parsed) == m


def test_manifest_from_dict_rejects_schema_mismatch() -> None:
    base = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    bad = {
        "schema_version": MANIFEST_SCHEMA_VERSION + 1,
        "user": {
            "id": 1,
            "username": None,
            "first_name": None,
            "locale": None,
            "created_at": base.isoformat(),
        },
        "tags": [],
        "videos": [],
    }
    with pytest.raises(SchemaVersionMismatch):
        Manifest.from_dict(bad)


# ---------- atomic_write_text ----------


def test_atomic_write_text_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    atomic_write_text(target, '{"hello":"world"}')
    assert target.read_text() == '{"hello":"world"}'


def test_atomic_write_text_replaces_atomically(tmp_path: Path) -> None:
    """Existing file is replaced; mid-write SIGKILL would leave the old file."""
    target = tmp_path / "x.json"
    target.write_text('{"v":1}')
    atomic_write_text(target, '{"v":2}')
    assert target.read_text() == '{"v":2}'


def test_atomic_write_text_does_not_leak_tmp_on_success(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    atomic_write_text(target, "ok")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_text_keeps_old_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `os.replace` raises mid-write, the previous valid file must survive."""
    target = tmp_path / "x.json"
    target.write_text('{"v":1}')

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(target, '{"v":2}')
    assert target.read_text() == '{"v":1}'


# ---------- dump_user_manifest ----------


async def test_dump_user_manifest_writes_expected_shape(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    storage = LocalStorage(root=tmp_path)
    await UserRepo(db_session).upsert(user_id=42, username="alice", first_name="A")
    [tag] = await TagRepo(db_session).upsert_many(user_id=42, names=["squat"])
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    storage_key = build_storage_key(42, ["squat"], vid)
    target = await storage.write_path(storage_key)
    target.write_bytes(b"x")
    await storage.commit(storage_key)
    await VideoRepo(db_session).create(
        id=vid,
        user_id=42,
        telegram_file_id="tg-1",
        storage_key=storage_key,
        duration_sec=Decimal("3"),
        width=1080,
        height=1920,
        caption="hi",
        tag_ids=[tag.id],
    )
    await db_session.commit()

    await dump_user_manifest(db_session, storage, user_id=42)

    manifest_path = tmp_path / "42" / "manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert data["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert data["user"]["id"] == 42
    assert data["user"]["username"] == "alice"
    assert {t["name"] for t in data["tags"]} == {"squat"}
    assert len(data["videos"]) == 1
    v = data["videos"][0]
    assert v["telegram_file_id"] == "tg-1"
    assert v["storage_key"] == storage_key
    # Numeric(10, 3) column → Decimal stringifies with 3 places.
    assert Decimal(v["duration_sec"]) == Decimal("3")
    assert v["caption"] == "hi"
    assert v["tag_names"] == ["squat"]


async def test_dump_user_manifest_overwrites_previous(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    storage = LocalStorage(root=tmp_path)
    await UserRepo(db_session).upsert(user_id=42, username="alice", first_name="A")
    await db_session.commit()

    # Pre-existing stale manifest
    user_dir = tmp_path / "42"
    user_dir.mkdir()
    (user_dir / "manifest.json").write_text('{"stale": true}')

    await dump_user_manifest(db_session, storage, user_id=42)

    data = json.loads((user_dir / "manifest.json").read_text())
    assert "stale" not in data
    assert data["user"]["id"] == 42


# ---------- recover_from_storage ----------


async def test_recover_skips_when_videos_already_present(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Recovery is a one-shot bootstrap; if the DB has videos, do nothing."""
    factory = create_session_factory(db_engine)
    async with factory() as s:
        await UserRepo(s).upsert(user_id=42, username="a", first_name="A")
        [tag] = await TagRepo(s).upsert_many(user_id=42, names=["squat"])
        await VideoRepo(s).create(
            id=uuid.uuid4(),
            user_id=42,
            telegram_file_id="t",
            storage_key="42/squat.x.mp4",
            duration_sec=Decimal("1"),
            tag_ids=[tag.id],
        )
        await s.commit()

    # Drop a stale-looking manifest to verify it isn't read
    user_dir = tmp_path / "42"
    user_dir.mkdir()
    (user_dir / "manifest.json").write_text('{"phantom": true}')

    report = await recover_from_storage(factory, tmp_path)
    assert report.skipped is True


async def test_recover_from_manifest_only(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Manifest in `<user>/manifest.json`; no orphan video files."""
    factory = create_session_factory(db_engine)

    # Wipe DB clean
    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await s.commit()

    base = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    user_dir = tmp_path / "42"
    user_dir.mkdir()
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "user": {
            "id": 42,
            "username": "alice",
            "first_name": "Alice",
            "locale": None,
            "created_at": base.isoformat(),
        },
        "tags": [{"name": "squat", "created_at": base.isoformat()}],
        "videos": [
            {
                "id": str(vid),
                "telegram_file_id": "tg-original",
                "storage_key": f"42/squat.{vid}.mp4",
                "duration_sec": "12.345",
                "width": 1080,
                "height": 1920,
                "fps": None,
                "caption": "hi",
                "created_at": (base + timedelta(days=1)).isoformat(),
                "tag_names": ["squat"],
            }
        ],
    }
    (user_dir / "manifest.json").write_text(json.dumps(manifest))
    # The video file itself must exist for the manifest path to import it
    (user_dir / f"squat.{vid}.mp4").write_bytes(b"x")

    report = await recover_from_storage(factory, tmp_path)
    assert report.skipped is False
    assert report.users_restored == 1
    assert report.videos_via_manifest == 1
    assert report.videos_via_filename == 0

    async with factory() as s:
        users = (await s.execute(select(User))).scalars().all()
        assert len(users) == 1 and users[0].id == 42
        videos = (await s.execute(select(Video))).scalars().all()
        assert len(videos) == 1
        v = videos[0]
        assert v.id == vid
        assert v.telegram_file_id == "tg-original"
        assert v.caption == "hi"
        assert v.duration_sec == Decimal("12.345")
        # Manifest's created_at preserved exactly
        assert v.created_at == base + timedelta(days=1)


async def test_recover_filename_only_path(
    db_engine: AsyncEngine, sample_clips: list[Path], tmp_path: Path
) -> None:
    """No manifest; filename encodes tags + uuid. Filename-only recovery
    fills duration via ffprobe, mtime as created_at, empty telegram_file_id."""
    factory = create_session_factory(db_engine)

    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await s.commit()

    user_dir = tmp_path / "99"
    user_dir.mkdir()
    vid = uuid.UUID("12345678-1234-1234-1234-123456789abc")
    target = user_dir / f"squat.{vid}.mp4"
    target.write_bytes(sample_clips[0].read_bytes())  # real 1s mp4 for ffprobe

    report = await recover_from_storage(factory, tmp_path)
    assert report.skipped is False
    assert report.videos_via_manifest == 0
    assert report.videos_via_filename == 1

    async with factory() as s:
        users = (await s.execute(select(User))).scalars().all()
        assert [u.id for u in users] == [99]
        tags = (await s.execute(select(Tag))).scalars().all()
        assert [t.name for t in tags] == ["squat"]
        videos = (await s.execute(select(Video))).scalars().all()
        assert len(videos) == 1
        v = videos[0]
        assert v.id == vid
        assert v.telegram_file_id == ""  # placeholder
        assert v.duration_sec > 0  # ffprobe-derived


async def test_recover_mixed_manifest_and_orphan_files(
    db_engine: AsyncEngine, sample_clips: list[Path], tmp_path: Path
) -> None:
    """Manifest covers some videos; orphan files alongside; both restored."""
    factory = create_session_factory(db_engine)

    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await s.commit()

    base = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    vid_known = uuid.UUID("12345678-1234-1234-1234-123456789aaa")
    vid_orphan = uuid.UUID("12345678-1234-1234-1234-123456789bbb")
    user_dir = tmp_path / "42"
    user_dir.mkdir()

    # Known via manifest
    (user_dir / f"squat.{vid_known}.mp4").write_bytes(sample_clips[0].read_bytes())
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "user": {"id": 42, "username": None, "first_name": None, "locale": None,
                 "created_at": base.isoformat()},
        "tags": [{"name": "squat", "created_at": base.isoformat()}],
        "videos": [{
            "id": str(vid_known),
            "telegram_file_id": "tg-known",
            "storage_key": f"42/squat.{vid_known}.mp4",
            "duration_sec": "1.0",
            "width": 320, "height": 240, "fps": None, "caption": None,
            "created_at": base.isoformat(),
            "tag_names": ["squat"],
        }],
    }
    (user_dir / "manifest.json").write_text(json.dumps(manifest))

    # Orphan video — only a file, not in the manifest
    (user_dir / f"pr.{vid_orphan}.mp4").write_bytes(sample_clips[1].read_bytes())

    report = await recover_from_storage(factory, tmp_path)
    assert report.users_restored == 1
    assert report.videos_via_manifest == 1
    assert report.videos_via_filename == 1

    async with factory() as s:
        videos = (await s.execute(select(Video).order_by(Video.id))).scalars().all()
        assert {v.id for v in videos} == {vid_known, vid_orphan}
        # Known one keeps its rich metadata
        known = next(v for v in videos if v.id == vid_known)
        assert known.telegram_file_id == "tg-known"
        # Orphan gets a placeholder
        orphan = next(v for v in videos if v.id == vid_orphan)
        assert orphan.telegram_file_id == ""


async def test_recover_skips_compilations_subdir(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Files under `<user>/compilations/` must not be rebuilt as videos."""
    factory = create_session_factory(db_engine)

    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await s.commit()

    user_dir = tmp_path / "42"
    (user_dir / "compilations").mkdir(parents=True)
    # Compilation has the .mov extension, but assert the recovery is also
    # robust against hypothetical .mp4 leakage into that subdir.
    (user_dir / "compilations" / f"squat.{uuid.uuid4()}.mp4").write_bytes(b"x")

    report = await recover_from_storage(factory, tmp_path)
    assert report.skipped is False
    assert report.videos_via_filename == 0
    assert report.videos_via_manifest == 0


async def test_recover_skips_non_numeric_dirs(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """A `tmp/` or unrelated dir at MEDIA_DIR root mustn't blow recovery up."""
    factory = create_session_factory(db_engine)
    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await s.commit()
    (tmp_path / "weirdname").mkdir()
    report = await recover_from_storage(factory, tmp_path)
    assert report.skipped is False
    assert report.users_restored == 0


async def test_recover_rolls_back_on_failure(
    db_engine: AsyncEngine, tmp_path: Path
) -> None:
    """If anything in the import fails, the transaction rolls back so the next
    startup retries from scratch (DB stays empty)."""
    factory = create_session_factory(db_engine)
    async with factory() as s:
        from sqlalchemy import text
        await s.execute(
            text("TRUNCATE users, tags, videos, video_tags, compilations RESTART IDENTITY CASCADE")
        )
        await s.commit()

    user_dir = tmp_path / "42"
    user_dir.mkdir()
    # Manifest schema-version mismatch → SchemaVersionMismatch raised mid-flight
    bad_manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION + 99,
        "user": {"id": 42, "username": None, "first_name": None, "locale": None,
                 "created_at": "2026-01-01T00:00:00+00:00"},
        "tags": [],
        "videos": [],
    }
    (user_dir / "manifest.json").write_text(json.dumps(bad_manifest))

    with pytest.raises(SchemaVersionMismatch):
        await recover_from_storage(factory, tmp_path)

    async with factory() as s:
        users = (await s.execute(select(User))).scalars().all()
        assert users == []
