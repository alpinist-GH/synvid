# SynVid

SynVid is a local, Apple Silicon-only macOS app for generating and editing
images and video with on-device AI models. It is a Tauri desktop shell (Rust)
driving a bundled Python worker over a versioned JSON-lines protocol on
stdin/stdout — there is no local HTTP server, no telemetry, and the app never
bundles model weights in the installer. Everything it produces stays under
your own `~/Library/Application Support/SynVid/` unless you explicitly export
or delete it.

This document describes the app as it actually behaves today, including its
real limitations. Every claim below is either measured on the development
Mac or traceable to a specific source file — see `PLAN.md` for the full
staged build history and `docs/measurements/*.md` for the underlying
evidence.

## Status

All eight build stages in `PLAN.md` have a first real pass; two still have
open items recorded in their stage notes: a full human VoiceOver pass, and
GPL license disposition for narration's phonemizer chain (see
[SBOM and third-party notices](#sbom-and-third-party-notices)). The app is
now signed and notarized (see [Getting the app](#getting-the-app) and
[Signing and notarization](#signing-and-notarization)), but the GPL
disposition is a real, unresolved open item that applies to this release —
notarization verifies code signing, not license compliance.

## Requirements

- Apple Silicon (`arm64`) Mac. There is no Intel build.
- macOS 14.0 or later (`app/src-tauri/tauri.conf.json`'s
  `minimumSystemVersion`). This is a distribution floor, not a promise that
  every model runs well on every supported Apple GPU — model support is
  gated per-model by a real measured MPS run (see [Models](#models) below).
- Tested configuration: MacBook Pro, Apple M5 Pro, 48 GB RAM, macOS 26.5.2,
  with PyTorch 2.13.0, diffusers 0.39.0, and transformers 5.14.1
  (`requirements.lock`). Nothing here has been tested on Intel, on a
  different Apple Silicon generation, with less memory, or against other
  PyTorch/diffusers versions — expect some larger models (HunyuanVideo 1.5 at
  ~53-54 GB) to be impractical or to
  fail outright on machines with less unified memory.
- Free disk: models range from ~3 GB (the disabled story-planner LLM) to
  ~57.7 GB (Qwen Image Edit). SynVid reserves disk space (with a 2 GB safety
  margin) before any download or generation job and refuses admission if
  the reservation would exceed free space, rather than starting and
  running out mid-job.

## Getting the app

Download the signed, notarized DMG from the
[GitHub Releases page](https://github.com/alpinist-GH/synvid/releases),
mount it, and drag `SynVid.app` to `/Applications` — see
[Signing and notarization](#signing-and-notarization) for what that
verifies (and what it doesn't). You can also build it yourself from source
instead.

## Building from source

Toolchain versions pinned for this project (`TOOLCHAINS.md`):

| Tool | Version |
|---|---|
| Python | 3.11.15 (exact patch pin) |
| PyInstaller | 6.18.0 |
| Node.js / npm | 26.5.0 / 11.17.0 |
| Rust / Cargo | 1.97.0 |
| Tauri | 2.x (`app/package-lock.json`, `app/src-tauri/Cargo.lock`) |

Recreate the Python environment from the lock file (there is no committed
`venv`, and an old one must not be reused across machines):

```sh
python3.11 -m venv venv
./venv/bin/python -m pip install -r requirements.lock
./venv/bin/python -m pip check
```

Run the test suites:

```sh
./venv/bin/python -m pytest tests/ -q          # 103 worker/protocol tests, stdlib + pytest only
cd app/src-tauri && cargo test --release        # Rust unit tests (worker supervisor, diagnostics redaction)
```

Install the frontend's build-time-only npm tooling (`@tauri-apps/api`,
`@tauri-apps/cli` — the shipped webview does not import them; it uses the
injected `window.__TAURI__` global, so no npm package code ships at
runtime):

```sh
npm --prefix app install
```

### Building the app bundle

Three scripts, in increasing order of what they produce:

- **`scripts/build-worker.sh`** — freezes the Python worker with PyInstaller
  (`--onedir`) into `app/src-tauri/resources/worker/`. Every other build
  step depends on this. No signing, no arguments required.
- **`scripts/build-local-app.sh`** — a development build: runs the worker
  freeze, builds the Tauri frontend, and produces an **unsigned** DMG at
  `dist/SynVid-0.1.0-unsigned.dmg`. This is what you want for local testing;
  macOS Gatekeeper will still let you open it locally (right-click → Open on
  first launch) even though it isn't signed.
- **`scripts/build-release-app.sh`** — the release path: does everything
  `build-local-app.sh` does, then code-signs every nested executable/dylib/
  framework with hardened runtime (deepest first), signs the outer `.app`,
  builds the DMG, and signs the DMG too, producing
  `dist/release/SynVid-0.1.0-signed-unnotarized.dmg`. Requires a Developer
  ID Application certificate in your keychain and
  `SYNVID_SIGNING_IDENTITY` set to its name or SHA-1:

  ```sh
  SYNVID_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
    ./scripts/build-release-app.sh
  ```

  This build is **not notarized** — see below.
- **`scripts/notarize-release-dmg.sh`** — the explicitly authorized step that
  takes the signed DMG from `build-release-app.sh`, submits it to Apple's
  notary service, staples the ticket, and validates the result with
  `stapler` and `spctl`, producing
  `dist/release/SynVid-0.1.0-notarized.dmg`. Requires a notarytool keychain
  profile; defaults to a profile named `synvid-notary`, overridable with
  `SYNVID_NOTARY_PROFILE`:

  ```sh
  xcrun notarytool store-credentials synvid-notary \
    --apple-id you@example.com --team-id TEAMID --password app-specific-password
  ./scripts/notarize-release-dmg.sh
  ```

  This step uploads the DMG to Apple and is never run automatically by any
  other script — see below.

## Installing, updating, and uninstalling

Drag the built `.app` to `/Applications` (or run it from wherever you put
it — SynVid does not require a fixed install location). All of SynVid's
mutable state lives outside the `.app` bundle, under
`~/Library/Application Support/SynVid/`:

```
~/Library/Application Support/SynVid/
├── index.sqlite3      # metadata index (SQLite, versioned schema, auto-migrated)
├── models/             # downloaded model weights, one directory per model
├── outputs/             # generated/edited media + sidecar metadata JSON
├── stories/             # Story Mode project documents
├── temporary/            # imported-copy and interrupted-work scratch space
└── logs/
```

**Replacing or removing the `.app` bundle never touches this directory.**
This was directly tested: a signed release build was `ditto`'d over an
existing dev-built install with a real ~186 GB library already present
(models, dozens of outputs, and stories from prior testing), and every
byte of it was intact and working after the replacement
(`docs/measurements/stage8-release-candidate-2026-08-09.md`). There is no
uninstaller; deleting the `.app` (e.g. dragging it to the Trash) does not
remove your models or generated media — do that yourself if you want your
disk space back. Deleting `~/Library/Application Support/SynVid/` by hand
removes everything SynVid has ever made or downloaded.

### Deletion inside the app

Every deletion is an explicit action; nothing is removed automatically
except reservation bookkeeping (below).

- **Remove model** — deletes one model's on-disk directory. Not reversible;
  re-enabling it means downloading again.
- **Clean temporary files** — empties `temporary/` (imported-asset copies,
  interrupted-job scratch data). Does not touch outputs, models, or
  stories.
- **Delete output** — deletes one completed generation and its metadata.
  Refuses if other outputs were generated *from* it (edits, narrated
  variants) unless you explicitly cascade-delete the whole descendant
  tree, and always refuses if a Story still references it — Story-owned
  media can never be deleted through this path (`worker/service.py`).
- **Delete story** — always deletes the story project document itself.
  Its scene artifacts (stills, clips, narration, compositions) are
  **retained by default**; an explicit cascade option additionally deletes
  each one via the same retain-unless-still-referenced rule as "Delete
  output."
- **Recovery Center** — after a crash or force-quit mid-job, shows any
  partial (never-promoted) output directories and the disk space still
  reserved for them, and lets you clear both in one action. This is
  distinct from the deletions above: it only ever touches incomplete work
  that was never a real output.

## Privacy and local diagnostics

SynVid makes no network calls except an explicit, user-initiated model
download (via `huggingface_hub`) — there is no telemetry, analytics, or
crash-reporting SDK anywhere in the app or worker. Model downloads never use
a Hugging Face account or API token: `worker/model_download.py` hardcodes
`token = None` and downloads anonymously; the UI states this explicitly
before every download.

Settings has an opt-in **Diagnostics** panel: "Preview diagnostics" builds a
small bounded text bundle (app/OS version, worker connection state, the
worker's last 128 log lines) with your home-folder path and any
token-shaped string automatically redacted, shown to you in full before you
choose whether to save it anywhere. Nothing is collected or sent unless you
explicitly export that file yourself; there is no automatic upload path at
all.

## Models

SynVid never downloads or loads a model without an explicit action. Choosing
a model to download shows its license, expected download size, and pinned
revision before anything transfers; downloads are checksum-verified against
a manifest sourced from Hugging Face LFS metadata, restricted to
Safetensors/reviewed Diffusers config files, and run with
`trust_remote_code=False`.

Every model below is registered in `worker/models.py` with a `shareable` or
`personal-research` classification recording its licensing situation. As of
this pass **that classification is disclosed to you before download, but is
not yet enforced as a separate build toggle** — a single SynVid build
exposes the same generation-model list regardless of a model's recorded
profile. Treat the license column as what governs your own use of that
model's output, not as something the app restricts on your behalf yet.

Models with a real measured profile on this Mac generate immediately once
installed. Models without one show "experimental test profile, not measured
on this Mac" and, where noted, a known quality problem — SynVid never hides
that distinction behind a plausible-looking control.

| Model | Capability | License | Size | Status on this Mac |
|---|---|---|---|---|
| **LTX Video** | text/image → video, video editing | LTX-Video Open Weights | 24 GB | ✅ Measured: Draft/Balanced/High at 256×256/8 fps, each across a duration ladder from 9 to 121 frames (1.1s–15.1s); BalancedLandscape 512×288/49 frames fixed |
| **FLUX.1-schnell** | text → image | Apache-2.0 | 54 GB | ✅ Measured: 512×512, 4 steps, bf16 — 48.5s, ~33.7 GB peak MPS |
| **Qwen Image Edit** | image editing | Apache-2.0 | 57.7 GB | ✅ Measured: 512×512, 4 steps, bf16 — 621s first run, ~57.7 GB peak MPS |
| **HunyuanVideo 1.5 480p** (T2V and I2V) | text/image → video | Tencent Hunyuan Community License — **excludes the EU, UK, and South Korea** | 53.4 / 54.2 GB | ⚠️ **Never measured on this Mac** — an unvalidated placeholder test profile only (848×480, 121 frames, 24 fps, 50 steps); registered but hidden from the app entirely (below) |
| **Wan 2.2 TI2V-5B (MLX)** | text → video | Apache-2.0 weights (`Wan-AI/Wan2.2-TI2V-5B`); MLX runtime code is a vendored, patched MIT-licensed subset of `Blaizzy/mlx-video` | 23 GB | 🟡 Provisionally measured through a distinct Apple-native MLX runtime, not the retired Diffusers/MPS pipeline (see below) — one recipe only, Balanced Landscape (1280×704, 41 frames/24 fps, ~10 min, ~40 GB peak MLX memory on this 48 GB Mac); verified generating from the packaged, notarizable `.app` build, but not yet through the same multi-prompt quality gate the ✅ models passed |
| FLUX.1-dev | text → image | FLUX.1-dev non-commercial | 54 GB | Registered, personal/research only, hidden from the app entirely (below) |
| FLUX.1-Kontext-dev | image editing | FLUX.1-dev non-commercial | 54 GB | Registered, personal/research only, hidden from the app entirely (below); not wired to any command |
| Qwen Story Planner (Qwen2.5-1.5B-Instruct) | Story Mode scene drafting | Apache-2.0 | 2.9 GB | ❌ Failed its adversarial structured-output gate; the "Draft scenes locally" button is disabled in the UI. Manual Story Mode authoring is unaffected. |

Only LTX Video, FLUX.1-schnell, and Wan 2.2 TI2V-5B (MLX) are selectable in
the **Create** (generation) model dropdown (`app/src-tauri/src/lib.rs`'s
`ENABLED_GENERATION_MODELS`, 3 entries). Qwen Image Edit is reachable only
through the separate **Edit Image** flow, not the Create dropdown.
FLUX.1-dev, FLUX.1-Kontext-dev, HunyuanVideo 1.5 480p (T2V and I2V), and the
story planner remain registered in `worker/models.py` but are hidden from
both the Create dropdown and the **Preparation** tab's model list
(`app/src/main.js`'s `SETTINGS_HIDDEN_MODEL_IDS`) — the story planner is
the one exception, shown only for its disabled "Draft scenes locally"
explanation, not for download. Wan 2.1 and the original Wan 2.2 TI2V-5B
(Diffusers/MPS, `WanPipeline`) are retired after failed local quality
gates; they are neither selectable nor downloadable, and an existing
retired snapshot is shown in Preparation only as removal-only cleanup.
`wan2.2-ti2v-5b-mlx` is a distinct, newer entry that runs the same upstream
weights through an Apple-native MLX runtime instead
(`worker/providers/wan_mlx.py`, `worker/vendor/mlx_video_wan2`) and is a
real, working, selectable, downloadable model — see
`docs/measurements/wan2.2-ti2v-5b-mlx-gate-2026-08-10.md` for the full
runtime/memory/quality evidence and its still-open follow-up items.

## Generation, quality, and export

**Recipes.** Draft/Balanced/High are the only generation-quality presets, and
each one is a real profile measured on this Mac (resolution, fps, step
count, memory) — there are no free-form resolution/step controls. A model
with no measured profile cannot generate at all; it shows "experimental
test profile" rather than silently falling back to guessed settings.

**Duration.** For LTX Video at Square aspect, duration is a separate control
from quality: a slider maps to a ladder of measured frame counts (9, 17, 25,
… 121 frames at 8 fps — 1.1s to 15.1s) for each of Draft/Balanced/High, so
changing quality preserves the selected duration where a matching measured
point exists (see `docs/measurements/ltx-duration-ladder-gate-2026-08-10.md`).
Landscape and Portrait remain single fixed-duration profiles; the UI
disables the duration slider and explains why when either is selected.

**Native vs. interpolated FPS.** Every clip plays at its model's native fps
— LTX runs at 8 fps native, for example. A frame-interpolation feasibility
gate (`docs/measurements/frame-interpolation-gate-2026-08-08.md`) tried
FFmpeg's `minterpolate` and rejected it: it produced the correct frame
count and fps but silently shrank the clip's duration (1.125s → 0.937s for
the same frame count). **"Native" is the only FPS SynVid offers anywhere**;
no interpolation code ships in the app.

**Canonical output vs. export.** Every generation's canonical file is H.264/
yuv420p MP4, written once. "Export" (High/Balanced/Small File) re-encodes
that same canonical file with `libx264`/`aac` at different CRF values
(18/23/30) into a separate file — it **never re-runs generation and never
touches the canonical output**. Re-exporting after already exporting simply
re-encodes from the same source again.

## Editing

- **Video editing** (LTX Video only, via `LTXConditionPipeline`): you pick a
  change amount from 0.05 to 0.95 (mapped internally to the pipeline's
  strength/denoise-strength), plus a prompt. Measured and accepted range is
  0.05–0.75; 0.75+ was visibly degraded in testing and is kept only as a UI
  maximum, not a recommended value. Wan has no video-editing path — the
  retired Diffusers/MPS pipeline never passed its own generation quality
  gate, and the newer `wan2.2-ti2v-5b-mlx` entry (provisionally measured
  for generation only) has not been extended to editing either.
- **Image editing** (Qwen Image Edit only, via `QwenImageEditPipeline`): a
  prompt plus the measured profile above; no user-set resolution/steps.
- Both paths read the source file read-only and write a brand-new output
  with `lineage: [{"output_id": <source>, "relation": "edited_from"}]` in
  its metadata — the original is never mutated, and every edit is
  traceable back to what it came from.

## Narration

"Add Voice" replaces a video's audio track with English speech synthesized
locally by Kokoro (via `kokoro-onnx`), never altering the video itself.
Duration is enforced strictly: speech shorter than the video is padded with
silence to match exactly; speech that would run longer than the video is
**rejected outright** with a clear error ("shorten the script") rather than
being time-stretched, sped up, or silently truncated. Kokoro is a
synthesized stock voice, not a clone of any real person, and the UI states
this before you use it. Kokoro embeds no technical audio watermark
(directly checked, `docs/measurements/stage5-listen-and-watermark-2026-08-09.md`)
— the "stock voice, not a clone" disclosure is what actually applies here,
not a watermark claim.

## Story Mode

Story Mode is a versioned, multi-scene project format (`.synvidstory`),
distinct from single-shot generation:

- Each scene tracks a prompt, optional narration, and an approval flag; up
  to 64 scenes per story. Editing a scene's prompt or narration
  automatically invalidates only the artifacts that depend on it (its
  still/clip/segment, or its narration/subtitles/segment) — unrelated
  scenes are never recomputed.
- **Rendering** runs each scene through still → clip → narration →
  subtitles, checkpointing every step as its own immutable output, so an
  interrupted render (crash, force-quit) only loses the step in progress —
  already-finished scenes are never redone.
- **Composing** requires every scene to be approved first, normalizes each
  scene's media to a common codec/resolution/fps/audio format, and
  concatenates them with hard cuts only (no cross-fades, no other
  transitions).
- Every write to a story takes an `expected_revision`; a stale write (e.g.
  from a second window) is rejected with a clear conflict error rather than
  silently overwriting concurrent changes.
- **Continuity across scenes is prompt- and style-bible-guided, not
  identity retention.** SynVid does not claim or attempt to keep a
  character's or location's exact appearance consistent across scenes the
  way a reference-conditioned model would — this is a deliberate, tested
  claim boundary (`PLAN.md`'s Stage 7 notes), not a missing feature to be
  assumed away.
- **Optional local script drafting** (an on-device Qwen2.5-1.5B-Instruct
  model) is built but currently **disabled**: it failed an adversarial
  structured-output test during its acceptance gate, so "Draft scenes
  locally" is grayed out. Manual scene authoring works fully without it —
  this was always designed as an optional accelerant, never a dependency.
- **Project export/import**: "project-only" exports just the story
  document plus a SHA-256 manifest; "self-contained" additionally bundles
  every referenced media file. Import verifies the manifest against actual
  file contents, rejects symlinked entries, path-traversal (`..`) entries,
  and any archive over 512 entries or 2 GiB uncompressed — checksum
  tampering, decompression bombs, and zip-slip are all rejected before
  anything is written to disk.
- **Deletion**: deleting a story always removes the project document;
  its generated scene artifacts are retained by default and only removed
  with an explicit cascade, under the same "never delete something another
  Story still needs" rule as single-output deletion.

## Accessibility

A structural accessibility pass — driving the real macOS accessibility
tree the same way VoiceOver itself reads it — found correct roles and
names, a non-drag alternative for scene reordering, and a correct visible
keyboard-tab order in the Story dialog specifically
(`docs/measurements/stage2-crash-restart-and-orphan-fix-2026-08-09.md`).

**This is not a substitute for a genuine human accessibility pass, and one
has not been done.** Real VoiceOver speech output and a full keyboard-only
walkthrough of every flow (not just Story) remain open — see `PLAN.md`'s
Stage 2/5/7 notes. Treat SynVid's current accessibility state as
"structurally sound, not yet human-verified," not as WCAG 2.2 AA-complete.

## Security architecture

- The webview holds exactly one Tauri capability, `core:default`, on its
  one window — no filesystem, shell, dialog, or HTTP plugin is granted to
  it (`app/src-tauri/capabilities/default.json`). File pickers run in Rust
  via `rfd`, validate the chosen file (magic bytes, symlink rejection, size
  cap), and hand the webview only an opaque ID — never a raw path.
- 39 narrowly-typed Rust commands are the entire surface the webview can
  call; there is no generic/dynamic command dispatch.
- Content-Security-Policy (`app/src/index.html`):
  `default-src 'self'; connect-src 'self' ipc: http://ipc.localhost; img-src 'self' asset:; media-src 'self' asset:; style-src 'self'; script-src 'self'`
  — no inline scripts, no `unsafe-eval`, no third-party origins.

## Failure handling

The worker auto-restarts within a few seconds if it's killed mid-session
(the app recovers to "Ready" without user action), and force-quitting the
outer app leaves no orphan worker process — both directly verified against
the signed release build, not just the dev path. A damaged or missing
worker binary, and corrupt or newer-than-supported local metadata, both
degrade to a clear on-screen error rather than crashing the app or worker
process — these were real bugs found and fixed during the Stage 8 release
pass (see `docs/measurements/stage8-release-candidate-2026-08-09.md`).
Insufficient disk space and a failed model download are both caught and
reported as a normal job failure by design (verified by code review, not a
live destructive test this pass).

## Signing and notarization

Release builds are signed with a Developer ID Application certificate and
hardened runtime, with every nested executable, dylib, and framework signed
before the outer app and DMG (§ [Building the app bundle](#building-the-app-bundle)).
Every signature uses a secure (Apple TSA) timestamp — `codesign
--timestamp=none` produces signatures the notary service unconditionally
rejects, so the build script never uses it.

The GitHub Releases DMG is **notarized**: submitted via
`scripts/notarize-release-dmg.sh` to Apple's notary service, stapled, and
validated. Apple accepted the submission, the stapled ticket validated with
`stapler validate`, and `spctl -a -t exec` against the mounted `.app`
returned `accepted` / `source=Notarized Developer ID` — the check that
reflects what actually happens when a user opens the app. (The script's own
DMG-level `spctl -a -t open --context context:primary-signing-identifier`
check is unreliable when invoked from a terminal — it needs a real
Finder/Safari-set quarantine event to evaluate correctly, not a manually
added `com.apple.quarantine` xattr — so a rejection there alongside a clean
`spctl -a -t exec` result is a known CLI limitation, not a real Gatekeeper
problem.) Notarization remains an explicit, manually authorized step
(`SYNVID_NOTARY_PROFILE`); it is never run automatically by any other
script or as part of `build-release-app.sh`.

**Notarization is not a legal clearance.** It verifies code signing and
malware scanning only. The [GPL disposition](#sbom-and-third-party-notices)
below is a separate, still-unresolved item that applies to this release
regardless of notarization status.

## SBOM and third-party notices

A release pass generates machine-readable SBOMs (CycloneDX, one each for
Rust, npm, Python, and the actual frozen worker bundle) plus vulnerability
scans (`cargo audit`, `pip-audit`, `npm audit`, `grype`) into
`dist/release/sbom/` and a `dist/release/THIRD_PARTY_NOTICES.md` license
inventory — regenerate both with `scripts/build-release-app.sh` plus the
scan commands documented in
`docs/measurements/stage8-release-candidate-2026-08-09.md`.

As of the last pass, every ecosystem scan is clean (zero known
vulnerabilities). Three GPL-family components are genuinely present in the
shipped worker bundle — `phonemizer-fork` (GPLv3+) and the native
`libespeak-ng.dylib` it loads (both part of Kokoro's narration path), and a
GPL-configured build of `ffmpeg` with `libx264` in active use for video
encoding. These require an explicit product/legal decision (offer FFmpeg's
corresponding source, or swap components for non-copyleft alternatives).
**This decision has not been made yet, and the current GitHub Release ships
anyway with this item open** — a known gap called out in the release notes,
not a resolved compliance posture. `THIRD_PARTY_NOTICES.md` currently
exists only in the `dist/release/` build-output tree, not yet embedded
inside the shipped `.app`.

## Contributing / internal docs

`PLAN.md` is the authoritative, staged build record — every feature above
traces back to a dated stage entry there with its measured evidence. Read
it before changing behavior described in this document; update both
together.

## License

SynVid's own source code is licensed under the [Apache License 2.0](LICENSE).
This covers the app and worker code in this repository only — it does not
extend to third-party models (several are gated, non-commercial, or
territory-restricted; see the model table above) or to the GPL-family
components noted under "SBOM and third-party notices."
