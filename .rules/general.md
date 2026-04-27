# General project rules

Cross-cutting rules that apply project-wide. See sibling files in `.rules/`
for area-specific rules.

## TDD is the rule here

For any new function/class/handler/service, write a failing unit test first,
confirm it fails for the right reason, then implement. Declarative
SQLAlchemy models are the one allowed exception.

## Secrets

`.env` holds the real `BOT_TOKEN` and is gitignored. `.env.example` is the
committed template and must stay blank. Never move secrets into
`.env.example`.

## Graceful shutdown

`dp.start_polling(handle_signals=True)` installs POSIX SIGINT/SIGTERM
handlers, so `docker compose stop` unwinds cleanly through the `finally`
block in `__main__._run` (closes the bot's HTTP session, disposes the DB
engine, logs `shutting down` / `bot stopped`). Don't replace this with a
manual signal handler.
