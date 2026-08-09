# Stage 2 forced-kill/restart gate — passed, with two packaging fixes

## Stale-worker regression found and fixed

Before this session's testing could proceed, the installed `/Applications/SynVid.app`
was found to be running a frozen worker binary built at 08:32, one commit
behind `worker/service.py`'s 09:31 change (`25c82b6`) that added `installed`
and `modes` fields to each entry in `get_status`'s `available_models` payload.
The frontend (`app/src/main.js`) reads `model.installed` to decide whether
generation is available; against the stale binary this was always
`undefined`, so every model showed as not installed and generation was
silently unavailable in the shipped app. Rebuilding the worker
(`scripts/build-worker.sh`) and the local app bundle
(`scripts/build-local-app.sh`) resolved it — confirmed by querying
`get_status` directly against the rebuilt frozen binary and seeing
`"installed": true` for `ltx-video`.

This is a process gap, not a code bug: nothing currently rebuilds the
installed `.app` when `worker/service.py` changes. Worth a pre-flight check
(or CI step) before relying on the installed app for manual testing.

## Orphan `multiprocessing.resource_tracker` — root-caused and fixed

`docs/measurements/flux-schnell-stage3-gate-2026-08-08.md` flagged that a
bundled-worker run left an orphaned `resource_tracker` process holding the
worker's stdout pipe open after the main process exited, so Rust (or a test
harness) never saw EOF. Root cause: PyTorch/multiprocessing lazily starts a
`resource_tracker` helper the first time a semaphore-backed primitive is
touched (typically during model loading); that helper is spawned via
`os.posix_spawn`/fork+exec and inherits whatever fd 1/2 pointed at when it
was launched. Since the worker's own fd 1 *is* the JSON-lines protocol pipe
Rust reads, the helper kept that pipe's write end open indefinitely.

Fix (`worker/__main__.py`, first lines executed, before any provider/torch
import): duplicate the real fd 1/2 to private descriptors used for all
protocol replies, repoint fd 1/2 at `/dev/null`, and mark the duplicated
descriptors close-on-exec (`fcntl.FD_CLOEXEC`) so neither a `posix_spawn`-based
child (which explicitly inherits only fd 0/1/2 as configured) nor a
fork-based multiprocessing child (which copies the whole fd table but honors
CLOEXEC across its own `exec()`) can carry the real pipe past this process.

Verified against the rebuilt frozen binary via `lsof`: after a real LTX
generation, both the main worker process and its `resource_tracker` child
report fd 1 and fd 2 as `/dev/null`, never the pipe. A harness that sends
`generate`, waits for the `terminal` event, and calls `.terminate()` +
`.wait()` now sees the worker's stdout close immediately — no leftover
`resource_tracker` process remains (`pgrep -f resource_tracker` empty).

Residual, lower-severity finding: in the **frozen** bundle specifically (not
reproduced running from source), a harness that goes on to call
`.stderr.read()` after the worker exits can still block waiting for stderr
EOF, even though `lsof` confirms fd 2 was already `/dev/null` at the time
checked. This does not block Rust's actual restart logic (`worker.rs` detects
interruption/restart from the stdout side, not by waiting for stderr
drainage to complete), but Rust's background stderr-reader thread
(`app/src-tauri/src/worker.rs:86`) could stay parked past a crash in the
packaged app, leaking one thread per crash rather than a resource that grows
unboundedly. Follow-up recommended before treating "drains stderr" as fully
closed for the frozen bundle specifically.

## Live forced-kill/restart evidence (installed `.app`, both idle and mid-job)

Using System Events/JXA to drive the actual installed app (no GUI automation
tool exists for native macOS apps in this environment; AppleScript/JXA
against the accessibility tree was used as a substitute, and doubled as a
structural accessibility check — see below):

- **Idle kill**: `kill -9` on the worker mid-idle. Rust spawned a fresh worker
  process automatically (new PID) with no user-visible error; UI showed
  `Ready · worker protocol v1` on next poll.
- **Mid-job kill**: started a real Draft LTX generation (worker at ~99% CPU,
  UI showing `queued · 0%`), then `kill -9`. Rust again spawned a new worker
  process automatically. Recovery Center correctly reported "1 incomplete
  output and 0 reserved bytes can be safely recovered" — confirming the
  resource reservation was released despite the hard kill, and the partial
  output directory was preserved (by design, for Recovery Center) rather than
  silently lost. Clicking "Recover incomplete work" removed it; a repeat
  check showed 0 incomplete outputs.
- No orphan processes (`ps aux` / `pgrep -f resource_tracker`) remained after
  either kill.

## Accessibility structural review (autonomous proxy)

No native macOS GUI-automation tool was available, so the accessibility tree
was queried and driven directly via JXA (`System Events`), which is the same
tree VoiceOver itself reads:

- Every interactive control in the Story dialog (a representative
  information-dense screen) has a distinct, meaningful accessible name and
  role — no unnamed `AXButton`s. Includes explicit non-drag reorder controls
  ("Move earlier" / "Move later"), satisfying the WCAG non-drag-alternative
  requirement for that flow.
- Keyboard `Tab` navigation moves focus in logical, top-to-bottom visual
  order (`Saved stories` → `Title` → `Premise` → `Style bible` → `Aspect
  ratio`), with a clear, high-contrast focus ring rendered at each stop
  (screenshot-verified), matching the `:focus-visible` CSS rule already
  present in `style.css`.
- `prefers-reduced-motion` and semantic ARIA roles (`tablist`, `radiogroup`,
  `aria-live`, `aria-checked`) are present in the markup (confirmed by prior
  code review).

This is a structural proxy, not a substitute for a human running VoiceOver
end-to-end or completing every flow keyboard-only. Genuine VoiceOver speech
output and a full human keyboard-only pass across onboarding, download,
variant, export, and destructive flows remain open acceptance items.
