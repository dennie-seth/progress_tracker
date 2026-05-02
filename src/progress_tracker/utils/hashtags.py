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

    Returns canonical-form names without the leading `#`, deduplicated, in
    order of first appearance. Canonical form = lowercased + every `-`
    replaced by `_` so the only `\\w` character that survives is the
    underscore — `-` stays reserved for UUIDs in storage filenames, where
    `<user>/<tag1>.<tag2>.<uuid>.mp4` would otherwise be ambiguous to read
    if tags also carried dashes.

    `None` or empty caption returns an empty list.
    """
    if not caption:
        return []
    seen: dict[str, None] = {}
    for match in _HASHTAG_RE.finditer(caption):
        name = match.group(1).lower().replace("-", "_")
        if name and name not in seen:
            seen[name] = None
    return list(seen.keys())
