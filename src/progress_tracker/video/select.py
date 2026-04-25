"""Pick which matching clips end up in the compile.

Algorithm (per CLAUDE.md): always include the oldest and newest matching
clips, plus a small number of random middle clips. The middle count scales
with N so a long history doesn't drown the endpoints.

    N <= 4    : include all
    5  <= N <= 9 : oldest + 2 random middle + newest    (4 clips)
    10 <= N <= 19: oldest + 3 random middle + newest    (5 clips)
    N >= 20   : oldest + 3 random middle + newest       (capped at 5)

Caller passes clips ordered oldest-first. The return preserves that order.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Protocol, TypeVar


class _HasCreatedAt(Protocol):
    @property
    def created_at(self) -> object: ...


T = TypeVar("T", bound=_HasCreatedAt)


def _middle_count(n: int) -> int:
    if n <= 4:
        return max(0, n - 2)  # everything between the endpoints
    if n <= 9:
        return 2
    return 3  # 10+ all use 3 middle picks → cap at 5 total


def select_clips(
    clips: Sequence[T], *, rng: random.Random | None = None
) -> list[T]:
    """Pick a subset of clips per the algorithm above.

    With <= 4 clips, all are returned. Otherwise: clips[0], some random
    middle clips from clips[1:-1] (sorted by their original chronological
    position), and clips[-1].
    """
    n = len(clips)
    if n == 0:
        return []
    if n <= 4:
        return list(clips)

    middle_count = _middle_count(n)
    rng = rng or random.Random()
    middle_pool = list(clips[1:-1])
    middles = rng.sample(middle_pool, k=middle_count)
    # Preserve chronological order in the final list.
    middles.sort(key=lambda c: middle_pool.index(c))
    return [clips[0], *middles, clips[-1]]
