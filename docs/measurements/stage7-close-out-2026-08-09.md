# Stage 7 close-out — delete_story, frozen-bundle stall/compose root cause, longer Story Mode duration

This session closed the four concrete gaps left open by
`docs/measurements/stage7-story-render-compose-2026-08-09.md` and
`docs/measurements/stage7-relocated-app-lifecycle-2026-08-09.md`.

## 1. `delete_story`

Added end-to-end: `StoryStore.delete` (optimistic-revision-checked document
removal), `GenerationService.delete_story` (mirrors `delete_output`'s
retain-by-default / explicit-cascade contract — cascade deletes each scene's
generated artifacts only when no other story still references them), the
`story_delete` protocol/worker dispatch, a Rust `story_delete` command, and
two UI actions in the Story dialog ("Delete story" retains media, "Delete
story and media" cascades). 16 new/changed unit tests across
`tests/test_stories.py` and `tests/test_generation_service.py`; full suite
(85 tests) passes; `cargo build` clean.

## 2 & 3. Frozen-bundle `compose_story` failure and the post-render request stall — same root cause

Both remaining Stage 7 findings turned out to be one bug.

**Reproduction.** A harness driving the frozen `synvid-worker` binary
directly (real flux-schnell + LTX, 2 scenes, `render_story` through `clip`,
then `get_status` → `story_get` → `compose_story`) reliably reproduced: after
a real render, `get_status` replies instantly, but the very next `story_get`
request never receives a reply — not even after a 300s wait. An initial
from-source reproduction of the same sequence (`python -m worker`, same real
models) showed **no** stall at all, localizing the defect to the frozen
PyInstaller bundle specifically, the same class as
`docs/measurements/stage6-qwen-frozen-bundle-2026-08-09.md`.

**Ruled out: my own instrumentation.** An early attempt at reproducing this
from source used `gc.set_debug(gc.DEBUG_STATS)` to look for a slow garbage
collection after model unload. That produced enough stderr volume to fill
the OS pipe buffer while the harness wasn't concurrently draining `stderr` —
a classic subprocess pipe deadlock in the *test harness*, not the product.
Confirmed via `sample <pid>`: the worker's own thread was stuck inside
`gc_collect_main` → `write()` to a full pipe. Rewriting the harness to drain
`stderr` on a background thread (matching how `worker.rs` already does it)
eliminated this and let the real investigation proceed.

**Real root cause, confirmed with `lsof`.** During a genuine post-render
hang, `lsof -p <worker-pid>` showed the `multiprocessing.resource_tracker`
helper process (spawned the first time torch/diffusers touches a
semaphore-backed primitive, e.g. during model loading) holding **fd 0
(stdin)** — the exact same pipe object as the main worker's fd 0:

```
=== lsof for worker pid (fd 0/1/2 focus) ===
synvid-wo 39789 alpinist    0     PIPE 0x5c9b58d0c67bb526 ... ->0x315b9e2ff4f64ceb
synvid-wo 39789 alpinist    1w     CHR                3,2 ... /dev/null
synvid-wo 39789 alpinist    2w     CHR                3,2 ... /dev/null
=== lsof for CHILD pid=39818 (resource_tracker) ===
synvid-wo 39818 alpinist    0   PIPE 0x5c9b58d0c67bb526 ... ->0x315b9e2ff4f64ceb
synvid-wo 39818 alpinist    1w   CHR                3,2 ... /dev/null
synvid-wo 39818 alpinist    2w   CHR                3,2 ... /dev/null
```

`worker/__main__.py` already had a documented, verified fix for exactly this
class of problem — but only for fd 1/2 (Stage 2's orphan-pipe fix, so Rust
reliably sees EOF): both are dup'd to private, `CLOEXEC`-marked descriptors
before being repointed at `/dev/null`, so a `resource_tracker` child spawned
later never inherits the real pipes. **fd 0 (the request pipe Rust/the
harness writes into) was never given the same treatment.** With two
processes holding the pipe's read end open, the kernel can deliver an
incoming request line to whichever one calls `read()` first; if it lands in
`resource_tracker` (which never consumes it — its own loop only reads the
tracker fd it was launched with), the request silently vanishes and the real
worker's next `sys.stdin` read blocks forever.

**Fix.** Mark fd 0 close-on-exec in place (no dup2 needed — nothing else
needs to keep using the literal fd-0 number the way stdout/stderr's
redirect-to-devnull trick requires):

```python
_stdin_flags = fcntl.fcntl(0, fcntl.F_GETFD)
fcntl.fcntl(0, fcntl.F_SETFD, _stdin_flags | fcntl.FD_CLOEXEC)
```

**Verification.** Rebuilt the frozen worker and relocated `.app`, reran the
identical 2-scene real render → `get_status` → `story_get` → `compose_story`
sequence: `story_get` replied in 0.0005s (previously: no reply after 300s+),
`compose_story` was accepted in 0.0005s and completed successfully
("Validating story scenes" → "Saving immutable story movie" → "Story
revision 10 composed", state `succeeded`). Both Stage 7 findings closed by
one fix. Also added a diagnostic in `worker/story_compose.py`'s exception
handler (prints the real `ffmpeg`/subprocess failure detail to stderr
instead of only the generic user-facing message) as a permanent aid for any
future composition failure — not itself required once the fd 0 fix landed,
since composition never actually failed once requests were reliably
delivered.

**Related, separate finding kept as a fix.** While investigating, real
memory measurements showed `flux-schnell` (~34 GiB peak MPS-allocated) plus
`ltx-video` (~30 GiB peak RSS) sum to ~64 GiB — well past this Mac's 48 GiB
unified memory. `render_story` was holding both the image and video
providers resident for the whole render (unlike the single-shot
`generate`/`edit_image` path, which already unloads competing providers
before running). A frozen-bundle sample during a real render showed physical
footprint at 51.2 GiB — already past the machine's physical RAM, forcing
compression/swap. Fixed in `worker/service.py`: `make_still`/`make_clip` now
unload the sibling provider before running, so Story Mode never holds both
resident at once, at the cost of a reload between steps. (This was not the
fd 0 bug's cause — the fd 0 hang reproduced even after this fix, and the fd
0 fix alone resolved it — but it's a real, separately measured problem
worth fixing regardless: a 111s two-scene render dropped to no memory
overcommit, and a relocated-app run after both fixes together completed the
same render in 130s with no stall.) Covered by
`test_story_render_unloads_the_sibling_provider_between_still_and_clip`.

## 4. Longer LTX duration + subtitles/multi-cue narration

Re-measured `ltx-video`'s "Balanced" recipe on real MPS hardware at 49
frames / 8 FPS (6.125s, up from the Stage 1 feasibility artifact's 9 frames
/ 1.125s), width/height/steps/guidance/dtype unchanged: `smoke_test.py`
succeeded, `ffprobe` confirmed exactly 49 frames / 6.125s / correct
H.264/yuv420p 256×256, and peak RSS was unchanged (~30.3 GiB — LTX's memory
cost here is dominated by loaded weights, not frame count at this
resolution). Wall time was 19.7s, barely more than the 9-frame baseline.
`worker/models.py`'s registry needs no change (duration lives entirely in
the per-installation `measured-profile.json`); only "Balanced" was
re-measured since Story Mode hardcodes that recipe and "Draft"/"High" are
meant to represent the same content at different quality, not different
durations.

With real duration headroom, ran a real `render_story` through
`"subtitles"` for the first time ever (still → clip → narration →
subtitles, real flux-schnell + LTX + Kokoro, narration: "Mara climbed the
old stone stairs. The lighthouse beam swept slowly across the bay." — two
sentences, 14 words). The job succeeded and produced all four artifacts.

**Found and fixed a real bug this exposed**: `worker/narration.py`'s
`write_srt` built each cue with `f"...\\n..."` — an escaped double
backslash followed by `n`, which is the **literal two-character sequence**
`\n`, not a real line break. Direct byte inspection of the produced
`subtitles.srt` confirmed it: `b'1\\n00:00:00,000 --> ...'` — no real SRT
player could parse this file. The existing test
(`test_segmented_narration_uses_measured_sentence_boundaries`) only asserted
a timestamp substring was present via `.read_text()`, which can't
distinguish an escaped backslash-n from an actual newline, so this shipped
undetected since the feature's introduction — never caught because
`through: "subtitles"` had never been exercised against a real clip long
enough to hold narration until this session. Same general lesson as the
`story_compose.py` concat-list bug from the prior session: an
escaping/formatting defect in a rarely-exercised code path, invisible to a
substring-only test. Fixed to real `\n`; added
`test_write_srt_uses_real_newlines_between_multiple_cues`, which asserts
exact byte-for-byte SRT output including real line breaks. Full suite (now
86 tests) passes.

Re-ran the same real `render_story` (fresh story, same narration) with the
fix in place and inspected the result directly: `subtitles.srt` now
contains two correctly-timed cues with real newlines (`00:00:00,000 -->
00:00:01,941` / `00:00:01,941 --> 00:00:04,672`) matching Kokoro's actual
per-sentence synthesis boundaries. `ffprobe` on the narrated clip confirmed
video and audio both at exactly 6.125s (in sync), and `ffmpeg
silencedetect` confirmed genuine non-silent speech from 0–4.56s followed by
correctly padded silence to the end — consistent with the duration policy
already validated in `docs/measurements/stage5-listen-and-watermark-2026-08-09.md`.

All test stories created during this investigation (in the real
Application Support library, needed to exercise real models) were removed
afterward via the new `delete_story` (cascade) capability from item 1 —
itself a useful dogfooding check that the new capability works correctly
against real data.
