# Rules: src/progress_tracker/db/

## Tags are per-user

There is no shared library; `tags` has `UNIQUE(user_id, name)`. Do not add
cross-user tag sharing without explicit request.
