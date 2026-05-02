# Rules: src/progress_tracker/services/

## Tags come from hashtags in the Telegram caption

Not from an interactive prompt. The ingest path parses `#([\w\-]+)` from
captions; empty caption = reject the upload with a help message. This
keeps the happy path one-message.

## Long compilations run in-process

Run via `asyncio.create_task` with a status-editing message. A queue
worker (arq/Celery) is a later concern — keep service-layer functions
queue-agnostic so it can be added without rewriting call sites.

## Persistence: dump-after-commit, never in-transaction

When a service mutates user state (ingest, delete), it MUST mark the
user dirty via `session.info.setdefault("dirty_users", set()).add(uid)`
inside the transaction, NOT call `dump_user_manifest` directly.
`DependenciesMiddleware` reads that set in the `else:` branch of its
try/except and dumps each user's manifest in a fresh session, so a
rolled-back transaction can never produce an on-disk manifest claiming a
phantom row. Don't change this contract — the symmetry between "DB
state" and "manifest" depends on it.

## Storage filenames encode their tags

`build_storage_key(user_id, tag_names, video_id)` in
`services/persistence.py` is the single source of truth for the
``<user>/<sorted_tag_slugs_dot_joined>.<uuid>.mp4`` shape. Don't inline
the format anywhere else — recovery's `parse_video_filename` is
designed to round-trip with that exact builder. Tag slugs come in the
canonical underscore form from `parse_hashtags`; the dot is reserved as
the field separator and the dash as the UUID separator.
