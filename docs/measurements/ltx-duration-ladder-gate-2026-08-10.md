# LTX Video Square duration ladder — passed

Checkpoint: `Lightricks/LTX-Video` at `8984fa25007f376c1a299016d0957a37a2f797bb`
(LTX-Video Open Weights License). The SynVid-owned snapshot was
checksum-verified against its manifest before every run.

## Motivation

The user asked for duration control. Until this gate, LTX's Square recipes
(`Draft`/`Balanced`/`High`) each hard-coded a single frame count (9 or 49),
so duration was a side effect of the quality choice, not an independent
control. This gate measures a shared ladder of frame counts across all three
quality tiers so the UI can expose duration as its own slider, per the
"Duration in seconds" control described in `PLAN.md`.

## Candidates

All candidates used `LtxProvider.calibrate()` — the same real on-device
measurement path used for every other LTX recipe — with the fixed seed
`42`, prompt `"A yellow flower gently moving in a spring breeze"`, 256×256,
float16, and each quality tier's existing steps/guidance (Draft: 4 steps;
Balanced: 8 steps; High: 12 steps; `guidance_scale=3.0` throughout). Frame
counts follow LTX's 8k+1 latent alignment: 9, 17, 25, 33, 41, 49, 57, 65,
73, 81, 89, 97, 105, 113, 121 (1.125s–15.125s at 8 fps).

| frames | duration | wall (Balanced, 8 steps) | wall (Draft, 4 steps) | wall (High, 12 steps) | peak RSS |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 9 | 1.1s | 16.8s (cold load) | 16.8s (cold load) | 1.5s | ~25.8–31.2 GiB |
| 25 | 3.1s | 2.4s | 1.8s | 2.9s | ~25.8–31.2 GiB |
| 49 | 6.1s | 4.1s | 3.1s | 5.0s | ~25.8–31.2 GiB |
| 73 | 9.1s | 5.9s | 4.4s | 7.3s | ~25.8–31.2 GiB |
| 97 | 12.1s | 7.7s | 5.8s | 9.6s | ~25.8–31.2 GiB |
| 121 | 15.1s | 9.6s | 7.2s | 12.0s | ~25.8–31.2 GiB |

Full 45-point run (15 frame counts × 3 quality tiers): 256.0s total, all in
one resident worker process. Peak RSS stayed essentially flat across the
entire frame range within a given process — unlike HunyuanVideo 1.5 (see
`hunyuan15-480p-t2v-mps-gate-2026-08-09.md`, which thrashed memory at 61
frames), this 2B distilled LTX checkpoint at 256×256 shows no meaningful
memory scaling with frame count up to 121 frames. All values stay well
under `LtxProvider.MIN_SYSTEM_MEMORY_BYTES` (36 GiB) on this Mac's 48 GiB
unified memory. Wall time scales roughly linearly with frame count, as
expected (more latent positions per denoising step), not with peak memory.

121 frames (15.1s) was tested first as a standalone stress probe before
committing to the full ladder, specifically to rule out the kind of memory
cliff Hunyuan hit — it passed cleanly (25.3s wall, 30.3 GiB peak) before the
full 45-point run proceeded.

## Decision

Ship a 15-point Square duration ladder for each of Draft/Balanced/High.
Recipe naming: the bare quality name (`Draft`/`Balanced`/`High`) stays
pinned to each tier's original single-duration shape (9/49/9 frames) so
existing measured-profile entries and external state remain valid; every
other frame count is named `{Quality}D{frames}` (e.g. `BalancedD73`). This
is generated programmatically in `worker/providers/ltx.py`
(`DURATION_LADDER_FRAMES`, `_square_duration_recipes`) — service.py, the
Rust `valid_recipe_name` gate, and the calibration/measured-profile
machinery required no changes, since they were already generic over
whatever `CALIBRATION_RECIPES` declares.

All 45 points were measured and written to
`~/Library/Application Support/SynVid/models/ltx-video/measured-profile.json`
(and the pre-existing lowercase `synvid` directory kept in sync) via direct
`LtxProvider.calibrate()` calls, then verified end-to-end through the real
app: a rebuilt PyInstaller worker bundle, a live Tauri window, a duration
slider drag via Accessibility automation, quality-tier switches that
correctly preserve the selected duration when a matching frame count
exists, and one real `generate` job (High quality, 1.1s / 9 frames) that
produced a playable canonical output.

Landscape and Portrait aspect ratios are **not** part of this gate and keep
their existing single fixed-duration recipes; the UI disables the duration
slider and explains why whenever either is selected.

## Frozen-worker gotcha found during verification

The running dev app loads its Python worker from the PyInstaller-bundled
`app/src-tauri/resources/worker/synvid-worker/synvid-worker`, not the `venv`
source tree — `tauri dev` has no dev-mode fallback to live source
(`app/src-tauri/src/lib.rs`'s `setup()` always resolves the bundled
executable). Editing `worker/providers/ltx.py` alone does not affect a
running `npm run dev` session; `scripts/build-worker.sh` must be re-run and
the app relaunched before UI changes depending on new Python behavior can be
verified. This cost a full debugging detour (duration briefly appeared not
to persist across quality switches) before the stale binary was identified
as the cause; the underlying frontend logic was correct throughout.
