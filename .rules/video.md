# Rules: src/progress_tracker/video/

## ffmpeg subprocess only — no MoviePy

ffmpeg is invoked as a subprocess via `asyncio.create_subprocess_exec`,
not via MoviePy or PyAV. The compiler builds one big `-filter_complex`
graph (normalize → optional `setpts`/`atempo` speedup → optional `drawtext`
overlay → `concat`) so everything runs in a single ffmpeg process. Don't
reintroduce MoviePy.

## Clip trimming rule

If `clip.duration <= target/N`, keep at full speed; otherwise speed up via
`setpts=PTS/speed` + `atempo` (chain `atempo` filters when speed > 2.0).
Never truncate — the user chose "speed up" over "crop".

## Clip selection rule (milestone 6)

Don't include every matching clip; always include the **oldest** and the
**newest**, plus a small number of **middle** clips sampled at random from
the rest. The middle count scales with how many clips are available so a
long history doesn't drown the oldest/newest signal:

- `N <= 4` clips matching: include all
- `5 <= N <= 9`: oldest + 2 middle (random) + newest = 4
- `10 <= N <= 19`: oldest + 3 middle (random) + newest = 5
- `N >= 20`: oldest + 3 middle (random) + newest = 5 (cap at 5)

Middle picks come from `videos[1:-1]` chronologically; a fixed RNG seed is
fine for reproducibility in tests, but production uses a fresh `random`.
This is what the user asked for; revisit only if they request it.
