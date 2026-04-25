"""Hashtag parsing for Telegram captions.

Per the project plan, tags are derived from `#tag` tokens in the caption.
Names are lowercased and deduplicated, preserving order of first appearance.
"""

from __future__ import annotations

import re

# Word characters and hyphens. Permits multilingual tags via `\w` + UNICODE.
_HASHTAG_RE = re.compile(r"#([\w-]+)", re.UNICODE)


def parse_hashtags(caption: str | None) -> list[str]:
    """Extract hashtag names from a caption.

    Returns lowercase names without the leading `#`, deduplicated, in order of
    first appearance. `None` or empty caption returns an empty list.
    """
    if not caption:
        return []
    seen: dict[str, None] = {}
    for match in _HASHTAG_RE.finditer(caption):
        name = match.group(1).lower()
        if name and name not in seen:
            seen[name] = None
    return list(seen.keys())
