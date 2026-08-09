# Stage 7 relocated-`.app` story lifecycle — mostly passed; two real findings

Drove the installed `/Applications/SynVid.app`'s bundled worker binary
directly (each "app launch" below is a fresh worker process, matching how
Rust would spawn one per app start) through create → save → close → reopen →
partially regenerate → crash-recover → resume → compose → export → import.

## Passed

- **Create/save**: `story_create`, `story_add_scene` ×2, `story_update_scene`
  (approve) ×2 — every call persists atomically; no explicit "save" step
  exists or is needed.
- **Close/reopen**: closing the worker process and starting a brand-new one
  against the same `SYNVID_APP_SUPPORT` correctly reloaded the story at the
  same revision with both scenes intact.
- **Partial regeneration + real crash recovery**: rendered scene 1 fully
  (flux-schnell still → LTX clip) via `render_story`. While starting scene
  2's render, the worker process was killed (both deliberately via `kill -9`
  in one run, and via an unplanned Python exception in another — both are
  authentic "app crashed mid-work" scenarios). In every case, reopening with
  a fresh worker process confirmed scene 1's checkpoint survived intact
  (`'clip' in scene["artifacts"]`), `recovery_preview` correctly reported the
  interrupted scene 2 partial as recoverable, and resuming `render_story`
  completed only the missing scene 2 work — scene 1 was not recomputed.
- **Export/import round-trip**: `story_export_project` (project-only)
  produced a real `.synvidstory` archive on disk; staging it into
  `temporary/imports/` and calling `story_import_project` adopted it as a
  new story ID with both scenes intact. (Self-contained export/import and
  the full corruption/tampering/path-traversal matrix are already
  extensively unit-tested in `tests/test_stories.py` and were not repeated
  against real media here.)

## Finding 1: worker responsiveness after a real generation job

Twice, the request immediately following a real (non-trivial) `render_story`
job — a second `render_story` call, and separately a `compose_story` call —
took over 90 seconds without even an `accepted` reply, in the same worker
process. Isolating this: `compose_story`'s own accept latency is 0.0s when
called on a freshly started worker with no prior generation in that process,
and identical request sequences succeed instantly when the preceding
`render_story` call was a cheap no-op (all artifacts already present, no
real model load). This points at slow model-unload/MPS-cache cleanup
blocking the worker's single request-processing loop after a real job, not
at `compose_story` or `render_story` themselves. Not fully root-caused;
worth profiling `provider.unload()` / `torch.mps.empty_cache()` timing
directly.

## Finding 2: `compose_story` intermittently fails when frozen, not from source

`compose_story` failed twice against the **frozen** worker binary with the
generic `"could not compose the story movie"` error, both times as the first
substantial request after a fresh app-support-clean worker start. Direct
reproduction of the identical `compose_hard_cuts` call — via a standalone
script, via the exact `submit_story_compose` runner logic replicated
in-process, and via three repeated fresh **source**-mode (`python -m
worker`) protocol calls — succeeded every time (3/3). This localizes the
failure to something specific to the **frozen PyInstaller bundle**, in the
same general class as the Qwen video-processor finding
(`docs/measurements/stage6-qwen-frozen-bundle-2026-08-09.md`): code that
behaves correctly from source but not once packaged. Temporary stderr
instrumentation was added and then reverted without a repeat frozen-bundle
run to capture it (to avoid another lengthy rebuild cycle in an already long
session) — the real `ffmpeg` stderr for the frozen-only failure was not
captured. Recommend re-adding a debug print of `error.stderr` in
`worker/story_compose.py`'s except block and re-running specifically against
the frozen `.app` to close this out.

## Missing feature, not a bug: no story-project delete

PLAN.md's Stage 7 acceptance line explicitly says: "create, save, close,
reopen, partially regenerate, compose, export, **and delete** a story
project." No `delete_story`/`story_delete` capability exists anywhere —
not in `worker/stories.py`, not in the worker protocol
(`worker/__main__.py`), not as a Rust command (`app/src-tauri/src/lib.rs`),
and not in the frontend. Scene deletion (`delete_scene`) and output deletion
(`delete_output`) both exist; whole-project deletion does not. This is a
real, confirmed gap against the stage's own written acceptance criteria —
implementing it (worker method + protocol case + Rust command + UI action,
plus a decision on whether to cascade-delete story-referenced outputs or
leave them as orphaned-but-recoverable Library entries) is a small feature
addition, out of scope for a testing pass.
