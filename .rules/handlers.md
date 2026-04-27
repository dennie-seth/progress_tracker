# Rules: src/progress_tracker/handlers/

## Router registration

All new routers must be registered in
`handlers/__init__.py::build_root_router` — do not register routers
directly on the Dispatcher in `bot.py`.

## FSM uses aiogram MemoryStorage

Fine for a single-instance bot. Switch to Redis only when adding
multi-replica deploys.
