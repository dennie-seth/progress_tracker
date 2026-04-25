"""Async repositories for the domain models.

Repos take an `AsyncSession` and don't manage transactions themselves —
the caller (handler / middleware) decides when to commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from progress_tracker.db.models import Compilation, Tag, User, Video, VideoTag


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(
        self, *, user_id: int, username: str | None, first_name: str | None
    ) -> User:
        """Insert or update a user; return the row in either case."""
        stmt = (
            insert(User)
            .values(id=user_id, username=username, first_name=first_name)
            .on_conflict_do_update(
                index_elements=[User.id],
                set_={"username": username, "first_name": first_name},
            )
            .returning(User)
        )
        result = await self._s.execute(stmt)
        return result.scalar_one()


class TagRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_many(self, user_id: int, names: Sequence[str]) -> list[Tag]:
        """Idempotently ensure tags exist for the given user; return all matching rows."""
        if not names:
            return []
        rows = [{"user_id": user_id, "name": n} for n in names]
        await self._s.execute(
            insert(Tag).values(rows).on_conflict_do_nothing(
                index_elements=[Tag.user_id, Tag.name]
            )
        )
        result = await self._s.execute(
            select(Tag).where(Tag.user_id == user_id, Tag.name.in_(names))
        )
        return list(result.scalars().all())

    async def list_for_user(self, user_id: int) -> list[Tag]:
        """Return every tag owned by the user, ordered alphabetically by name.

        Stable ordering matters for inline keyboards — users see the same
        layout across calls.
        """
        result = await self._s.execute(
            select(Tag).where(Tag.user_id == user_id).order_by(Tag.name)
        )
        return list(result.scalars().all())

    async def get(self, tag_id: int) -> Tag | None:
        """Fetch a tag by primary key. Returns None if missing."""
        return await self._s.get(Tag, tag_id)


class VideoRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        id: uuid.UUID,
        user_id: int,
        telegram_file_id: str,
        storage_key: str,
        duration_sec: Decimal,
        width: int | None = None,
        height: int | None = None,
        fps: Decimal | None = None,
        caption: str | None = None,
        tag_ids: Sequence[int] = (),
    ) -> Video:
        video = Video(
            id=id,
            user_id=user_id,
            telegram_file_id=telegram_file_id,
            storage_key=storage_key,
            duration_sec=duration_sec,
            width=width,
            height=height,
            fps=fps,
            caption=caption,
        )
        self._s.add(video)
        await self._s.flush()
        if tag_ids:
            self._s.add_all(
                [VideoTag(video_id=video.id, tag_id=tid) for tid in tag_ids]
            )
            await self._s.flush()
        return video

    async def list_for_user_tag(
        self,
        user_id: int,
        tag_id: int,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Video]:
        """Return videos owned by `user_id` carrying `tag_id`, oldest first.

        Optional `since` / `until` bound `Video.created_at` (inclusive lower,
        exclusive upper) — used by the compile flow's date-range selector.
        """
        stmt = (
            select(Video)
            .join(VideoTag, VideoTag.video_id == Video.id)
            .where(Video.user_id == user_id, VideoTag.tag_id == tag_id)
            .order_by(Video.created_at)
        )
        if since is not None:
            stmt = stmt.where(Video.created_at >= since)
        if until is not None:
            stmt = stmt.where(Video.created_at < until)
        result = await self._s.execute(stmt)
        return list(result.scalars().all())

    async def count_for_tags(
        self,
        user_id: int,
        tag_ids: Sequence[int],
        *,
        exclude_video_id: uuid.UUID | None = None,
    ) -> int:
        """Count this user's videos that have ANY of the given tags."""
        if not tag_ids:
            return 0
        stmt = (
            select(func.count(func.distinct(Video.id)))
            .join(VideoTag, VideoTag.video_id == Video.id)
            .where(Video.user_id == user_id, VideoTag.tag_id.in_(tag_ids))
        )
        if exclude_video_id is not None:
            stmt = stmt.where(Video.id != exclude_video_id)
        result = await self._s.execute(stmt)
        return int(result.scalar_one() or 0)


class CompilationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        id: uuid.UUID,
        user_id: int,
        tag_id: int | None,
        from_date: datetime | None,
        to_date: datetime | None,
        duration_sec: Decimal,
        storage_key: str,
        telegram_file_id: str | None = None,
    ) -> Compilation:
        comp = Compilation(
            id=id,
            user_id=user_id,
            tag_id=tag_id,
            from_date=from_date,
            to_date=to_date,
            duration_sec=duration_sec,
            storage_key=storage_key,
            telegram_file_id=telegram_file_id,
        )
        self._s.add(comp)
        await self._s.flush()
        return comp
