# Rules: src/progress_tracker/services/

## Tags come from hashtags in the Telegram caption

Not from an interactive prompt. The ingest path parses `#([\w\-]+)` from
captions; empty caption = reject the upload with a help message. This
keeps the happy path one-message.

## Long compilations run in-process

Run via `asyncio.create_task` with a status-editing message. A queue
worker (arq/Celery) is a later concern — keep service-layer functions
queue-agnostic so it can be added without rewriting call sites.
