# Rules: src/progress_tracker/storage/

## Storage Protocol contract

Storage is behind a `Storage` Protocol (`storage/base.py` → `LocalStorage`
now, `S3Storage` stub for later). ffmpeg needs real filesystem paths, so
the Protocol exposes `open(key) -> AsyncContextManager[Path]`.
`LocalStorage` yields the real path; `S3Storage` would download to a
tempdir. Preserve this contract when adding backends.
