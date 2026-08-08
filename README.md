# SynVid

SynVid is a local Apple-Silicon macOS image and video generator. It uses a
Tauri desktop shell and a contained Python worker over versioned JSON-lines
IPC; it does not run a local HTTP server and never bundles model weights in the
app installer.

The project has passed the narrow Stage 1 LTX feasibility recipe recorded
below and is implementing the broader Stage 2 app gate in [PLAN.md](PLAN.md).
The app exposes no unmeasured generation settings.

SynVid keeps its mutable data below `~/Library/Application Support/SynVid/`:
`models/`, `outputs/`, `temporary/`, `logs/`, and `index.sqlite3`. Replacing or
removing an app bundle never removes this data. Output and model deletion will
be explicit in-app actions; they are not implemented in Stage 0.

## Development

The current environment check is:

```sh
./venv/bin/python -m pip check
```

The committed worker protocol, fake-provider, and Stage 1 orchestration tests
use only the Python standard library:

```sh
./venv/bin/python -m unittest discover -s tests -v
```

Do not use the moved development environment's console scripts. Recreate it
from `requirements.lock` before any model or worker packaging work.

## Profiles and model trust boundary

The shareable profile exposes only permissively licensed candidates. The
personal/research profile can disclose opt-in FLUX dev/Kontext features, which
remain non-commercial and access-gated. Model files are never downloaded by
startup or model selection. A future explicit download confirmation must show
the pinned commit, cache size, license and access requirement. It must use the
reviewed repository and exact commit, force `trust_remote_code=False`, allow
only reviewed Diffusers data/Safetensors files, and verify a complete SHA-256
manifest sourced from Hugging Face LFS metadata at that commit.

## Stage 0 evidence

The versioned JSON-lines protocol, fake-provider lifecycle seam, immutable
output promotion, resource reservations (including failure/crash release),
SQLite backup/rebuild/corrupt-index preservation, pinned model registry and
negative model-security controls, and restrictive Tauri capability manifest are
host-tested. The MPS device probe passed on the development Mac (fp16 and bf16
both work). Build the frozen one-folder worker with:

```sh
./scripts/build-worker.sh
```

The bundle is deliberately generated into the Tauri resource tree and ignored
by Git. The Stage 1 source path accepts only a pinned local LTX snapshot that
passed the reviewed checksum manifest, then records one measured profile before
any generation setting can be accepted. The remaining gate requires explicit
model-download/license authorization and a real MPS output inspection.

## Stage 1 evidence (development Mac, 2026-08-07)

The explicitly authorized `Lightricks/LTX-Video` snapshot at
`8984fa25007f376c1a299016d0957a37a2f797bb` was checksum-verified and installed
only under SynVid's Application Support model root. It is not bundled in this
repository or an app installer.

The selected first measured recipe is float16 MPS, 256×256, 9 frames, native 8
FPS (1.125 seconds), 8 denoising steps, and guidance scale 3.0. It completed in
16.104 seconds with 30,375,952,384 bytes peak process RSS. `ffprobe` confirmed
H.264/yuv420p, 256×256, 9 frames, and 8 FPS; a rendered frame was directly
inspected and shows the requested yellow flower. The 4-step float16 candidate
used less memory (27,656,749,056 bytes) but was visibly too abstract, so it is
not selected. A 4-step bfloat16 candidate was valid but had higher measured
RSS (28,953,264,128 bytes).

This is one small square feasibility recipe, not a complete user-facing
resolution/aspect-ratio matrix. Stage 2 must expose no controls outside
measured profiles; additional valid combinations require the same output,
memory, timing, and inspection gate.

Recorded packaging evidence on the development Mac (2026-08-07): frozen worker
bundle 18,345,984 bytes; first post-rebuild worker response 1,426 ms (valid
handshake rerun 38 ms); mounted unsigned app 44,163,072 bytes; unsigned DMG
13,842,760 bytes. The mounted app launched and its fixed resource worker
returned the version-1 handshake. These are feasibility measurements only;
there is no model-weight or real-inference claim, and the DMG is not signed or
notarized.

## Stage 2 implementation status

The three-pane app has bounded `worker_status`, `generate`, `cancel`,
`export_video`, and native source-image selection surfaces. The webview never
supplies generation dimensions or media bytes: it selects a worker-reported
Draft/Balanced/High recipe, while the native picker copies a verified regular
PNG/JPEG/WebP image into SynVid-owned temporary storage and passes only its
opaque ID. The UI reattaches by polling worker state after reload and receives
queued progress/terminal protocol messages during that reconciliation.

Stage 2 now includes resumable first-run disclosure (which never downloads or
renders), local draft autosave with a bounded undo/redo history, preset reset,
metadata-only library selection and variant promotion, plus a Recovery Center
that previews and removes only incomplete output directories. Draft/Balanced/
High are distinct measured recipes (4/8/12 steps respectively); custom
parameter overrides remain unavailable. The measured renders were valid
256×256 H.264/yuv420p, 9-frame, 8-FPS clips. Draft was visibly abstract,
while Balanced and High retained the requested yellow flower. Canonical output
has separate High/Balanced/Small File H.264 exports that preserve its 256×256,
9-frame, 8-FPS facts without rerunning generation.

The local app build uses `scripts/build-local-app.sh`: Tauri's normal resource
copy omits nested PyInstaller framework/symlink files, so the script restores
the one-folder worker with `ditto`. A copied, relocated `.app` then completed a
real frozen-worker text-to-video render with Homebrew removed from `PATH`.
Real LTX image-to-video was also inspected from a locally selected flower
frame. The UI's visible accessibility review and forced worker-crash/restart
exercise are still manual acceptance gates; Stage 2 remains open until those
installed-app checks are recorded. DMG construction is a Stage 8 signing/release
task.
