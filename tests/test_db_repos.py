"""Integration tests for UserRepo / TagRepo / VideoRepo against real Postgres."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.models import Tag, Video, VideoTag
from progress_tracker.db.repos import TagRepo, UserRepo, VideoRepo


# ---------- UserRepo ----------


async def test_user_upsert_inserts_when_missing(db_session: AsyncSession) -> None:
    repo = UserRepo(db_session)
    user = await repo.upsert(user_id=42, username="alice", first_name="Alice")
    await db_session.commit()
    assert user.id == 42
    assert user.username == "alice"
    assert user.first_name == "Alice"


async def test_user_upsert_updates_existing(db_session: AsyncSession) -> None:
    repo = UserRepo(db_session)
    await repo.upsert(user_id=42, username="alice", first_name="Alice")
    await db_session.commit()
    updated = await repo.upsert(user_id=42, username="alice2", first_name="A.")
    await db_session.commit()
    assert updated.id == 42
    assert updated.username == "alice2"
    assert updated.first_name == "A."


async def test_user_upsert_handles_null_username(db_session: AsyncSession) -> None:
    repo = UserRepo(db_session)
    user = await repo.upsert(user_id=7, username=None, first_name="Bob")
    await db_session.commit()
    assert user.username is None


# ---------- TagRepo ----------


async def test_tag_upsert_many_creates_new(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    repo = TagRepo(db_session)
    tags = await repo.upsert_many(user_id=1, names=["squat", "pr"])
    await db_session.commit()
    assert {t.name for t in tags} == {"squat", "pr"}
    assert all(t.user_id == 1 for t in tags)


async def test_tag_upsert_many_returns_existing_unchanged(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    repo = TagRepo(db_session)
    first = await repo.upsert_many(user_id=1, names=["squat"])
    await db_session.commit()
    second = await repo.upsert_many(user_id=1, names=["squat", "pr"])
    await db_session.commit()
    # `squat` keeps its original id
    squat_old = next(t for t in first if t.name == "squat")
    squat_new = next(t for t in second if t.name == "squat")
    assert squat_old.id == squat_new.id


async def test_tag_upsert_many_scopes_per_user(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u1", first_name="U1")
    await UserRepo(db_session).upsert(user_id=2, username="u2", first_name="U2")
    repo = TagRepo(db_session)
    a = await repo.upsert_many(user_id=1, names=["squat"])
    b = await repo.upsert_many(user_id=2, names=["squat"])
    await db_session.commit()
    # Same name, two different rows because of UNIQUE(user_id, name)
    assert a[0].id != b[0].id


async def test_tag_upsert_many_empty_returns_empty(db_session: AsyncSession) -> None:
    repo = TagRepo(db_session)
    assert await repo.upsert_many(user_id=1, names=[]) == []


# ---------- VideoRepo ----------


async def test_video_create_persists_video_and_tag_links(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    tags = await TagRepo(db_session).upsert_many(user_id=1, names=["squat", "pr"])
    await db_session.commit()

    vid_id = uuid.uuid4()
    video = await VideoRepo(db_session).create(
        id=vid_id,
        user_id=1,
        telegram_file_id="tg-abc",
        storage_key="1/abc.mp4",
        duration_sec=Decimal("12.500"),
        width=1080,
        height=1920,
        caption="#squat #pr session",
        tag_ids=[t.id for t in tags],
    )
    await db_session.commit()

    assert video.id == vid_id
    # Verify VideoTag rows
    rows = (
        await db_session.execute(
            select(VideoTag).where(VideoTag.video_id == vid_id)
        )
    ).scalars().all()
    assert {r.tag_id for r in rows} == {t.id for t in tags}


async def test_video_create_without_tags(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    video = await VideoRepo(db_session).create(
        id=uuid.uuid4(),
        user_id=1,
        telegram_file_id="tg-xyz",
        storage_key="1/xyz.mp4",
        duration_sec=Decimal("3.000"),
    )
    await db_session.commit()
    assert video.id is not None


async def test_count_for_tags_excluding_self(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    [tag] = await TagRepo(db_session).upsert_many(user_id=1, names=["squat"])
    await db_session.commit()

    repo = VideoRepo(db_session)
    ids = []
    for _ in range(3):
        v = await repo.create(
            id=uuid.uuid4(),
            user_id=1,
            telegram_file_id="x",
            storage_key="x",
            duration_sec=Decimal("1.000"),
            tag_ids=[tag.id],
        )
        ids.append(v.id)
    await db_session.commit()

    count = await repo.count_for_tags(user_id=1, tag_ids=[tag.id], exclude_video_id=ids[-1])
    assert count == 2


async def test_count_for_tags_returns_zero_when_no_tags(db_session: AsyncSession) -> None:
    repo = VideoRepo(db_session)
    assert await repo.count_for_tags(user_id=1, tag_ids=[]) == 0


async def test_count_for_tags_dedupes_video_with_multiple_tags(
    db_session: AsyncSession,
) -> None:
    """A single video that matches several queried tags must count exactly once."""
    await UserRepo(db_session).upsert(user_id=1, username="u", first_name="U")
    tags = await TagRepo(db_session).upsert_many(user_id=1, names=["squat", "pr"])
    await db_session.commit()

    repo = VideoRepo(db_session)
    await repo.create(
        id=uuid.uuid4(),
        user_id=1,
        telegram_file_id="x",
        storage_key="x",
        duration_sec=Decimal("1"),
        tag_ids=[t.id for t in tags],
    )
    await db_session.commit()

    count = await repo.count_for_tags(user_id=1, tag_ids=[t.id for t in tags])
    assert count == 1


async def test_count_for_tags_scoped_per_user(db_session: AsyncSession) -> None:
    await UserRepo(db_session).upsert(user_id=1, username="u1", first_name="U1")
    await UserRepo(db_session).upsert(user_id=2, username="u2", first_name="U2")
    [tag1] = await TagRepo(db_session).upsert_many(user_id=1, names=["squat"])
    [tag2] = await TagRepo(db_session).upsert_many(user_id=2, names=["squat"])
    await db_session.commit()
    repo = VideoRepo(db_session)
    await repo.create(
        id=uuid.uuid4(), user_id=1, telegram_file_id="x", storage_key="x",
        duration_sec=Decimal("1"), tag_ids=[tag1.id],
    )
    await repo.create(
        id=uuid.uuid4(), user_id=2, telegram_file_id="y", storage_key="y",
        duration_sec=Decimal("1"), tag_ids=[tag2.id],
    )
    await db_session.commit()
    assert await repo.count_for_tags(user_id=1, tag_ids=[tag1.id]) == 1
    assert await repo.count_for_tags(user_id=2, tag_ids=[tag2.id]) == 1
