"""Tests for the clip-selection algorithm.

Per CLAUDE.md: don't include every matching clip; always pick the oldest and
newest, plus a small number of random middle clips. The middle count scales
with N so a long history doesn't drown the oldest/newest signal.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from progress_tracker.video.select import select_clips


@dataclass(frozen=True)
class Stub:
    """Minimum surface select_clips needs from a Video — keeps the test
    independent of SQLAlchemy."""

    id: int
    created_at: datetime


def _series(n: int) -> list[Stub]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Stub(id=i, created_at=base + timedelta(days=i)) for i in range(n)]


def test_returns_empty_for_no_clips() -> None:
    assert select_clips([]) == []


def test_returns_all_when_at_or_below_4() -> None:
    for n in range(1, 5):
        clips = _series(n)
        assert select_clips(clips) == clips


def test_5_clips_picks_oldest_two_middle_newest() -> None:
    clips = _series(5)
    rng = random.Random(0)  # deterministic
    picked = select_clips(clips, rng=rng)
    assert len(picked) == 4
    assert picked[0] is clips[0]
    assert picked[-1] is clips[-1]
    middle_ids = {p.id for p in picked[1:-1]}
    # Middles must come from clips[1:-1]
    assert middle_ids.issubset({c.id for c in clips[1:-1]})


def test_9_clips_picks_oldest_two_middle_newest() -> None:
    clips = _series(9)
    picked = select_clips(clips, rng=random.Random(0))
    assert len(picked) == 4
    assert picked[0] is clips[0] and picked[-1] is clips[-1]


def test_10_clips_picks_oldest_three_middle_newest() -> None:
    clips = _series(10)
    picked = select_clips(clips, rng=random.Random(0))
    assert len(picked) == 5
    assert picked[0] is clips[0] and picked[-1] is clips[-1]


def test_19_clips_picks_oldest_three_middle_newest() -> None:
    clips = _series(19)
    picked = select_clips(clips, rng=random.Random(0))
    assert len(picked) == 5


def test_20_clips_capped_at_5() -> None:
    clips = _series(20)
    picked = select_clips(clips, rng=random.Random(0))
    assert len(picked) == 5
    assert picked[0] is clips[0] and picked[-1] is clips[-1]


def test_100_clips_still_capped_at_5() -> None:
    clips = _series(100)
    picked = select_clips(clips, rng=random.Random(0))
    assert len(picked) == 5


def test_picked_clips_remain_in_chronological_order() -> None:
    clips = _series(15)
    picked = select_clips(clips, rng=random.Random(7))
    timestamps = [c.created_at for c in picked]
    assert timestamps == sorted(timestamps)


def test_seeded_rng_is_deterministic() -> None:
    clips = _series(15)
    a = select_clips(clips, rng=random.Random(42))
    b = select_clips(clips, rng=random.Random(42))
    assert [c.id for c in a] == [c.id for c in b]


def test_default_rng_returns_at_least_oldest_and_newest() -> None:
    """Without a seed: still includes endpoints, even if middle is random."""
    clips = _series(15)
    picked = select_clips(clips)  # uses fresh random.Random()
    assert picked[0] is clips[0]
    assert picked[-1] is clips[-1]
    assert len(picked) == 5
