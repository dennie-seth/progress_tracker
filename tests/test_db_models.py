"""Structural tests for progress_tracker.db.models.

These don't hit a database — they inspect the SQLAlchemy metadata to lock in
the contract described in the plan file (`enumerated-twirling-glacier.md`).
"""

from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint

from progress_tracker.db.models import (
    Base,
    Compilation,
    Tag,
    User,
    Video,
    VideoTag,
)


def test_expected_tables_are_registered() -> None:
    expected = {"users", "videos", "tags", "video_tags", "compilations"}
    assert expected <= set(Base.metadata.tables)


def test_user_pk_is_telegram_bigint_id() -> None:
    pk = list(User.__table__.primary_key.columns)
    assert len(pk) == 1
    assert pk[0].name == "id"
    # Telegram user IDs don't fit in a 32-bit int.
    assert pk[0].type.python_type is int


def test_tags_has_unique_user_name_constraint() -> None:
    uniques = [
        c for c in Tag.__table__.constraints if isinstance(c, UniqueConstraint)
    ]
    assert any(
        set(c.columns.keys()) == {"user_id", "name"} for c in uniques
    ), "tags must have UNIQUE(user_id, name) per the plan"


def test_videos_has_user_created_composite_index() -> None:
    indexes: list[Index] = list(Video.__table__.indexes)
    assert any(
        [c.name for c in idx.columns] == ["user_id", "created_at"] for idx in indexes
    ), "videos must have an index on (user_id, created_at)"


def test_video_tags_is_composite_pk() -> None:
    pk_cols = [c.name for c in VideoTag.__table__.primary_key.columns]
    assert set(pk_cols) == {"video_id", "tag_id"}


def test_video_tags_has_tag_video_index() -> None:
    indexes: list[Index] = list(VideoTag.__table__.indexes)
    assert any(
        [c.name for c in idx.columns] == ["tag_id", "video_id"] for idx in indexes
    ), "video_tags should have an index on (tag_id, video_id) for reverse lookups"


def test_compilations_table_exists() -> None:
    assert Compilation.__tablename__ == "compilations"
    cols = {c.name for c in Compilation.__table__.columns}
    assert {"id", "user_id", "tag_id", "duration_sec", "storage_key", "created_at"} <= cols
