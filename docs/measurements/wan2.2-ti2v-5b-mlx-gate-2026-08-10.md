# Wan2.2 TI2V-5B via MLX (`wan2.2-ti2v-5b-mlx`) — runtime and memory pass; quality provisional

A distinct entry from the retired `wan2.2-ti2v-5b` (see
`wan2.2-ti2v-5b-gate-pending.md`): same upstream weights
(`Wan-AI/Wan2.2-TI2V-5B`, pinned revision `921dbaf3f1674a56f47e83fb80a34bac8a8f203e`),
different runtime. That entry ran the checkpoint through Diffusers'
`WanPipeline` on MPS and found it unwatchable (color wash/blur, overexposure)
at every tested config. This entry runs the same weights through an
Apple-native MLX port instead — a vendored, patched subset of
`Blaizzy/mlx-video` (MIT, commit `87db56a51758fefb748a359b90a5283bb8ba4837`;
see `worker/vendor/mlx_video_wan2/NOTICE.md` for exactly what was vendored
and why) — and produced a genuinely watchable result on the first real
attempt.

## Why a separate model ID, not a fix to the retired entry

The retired entry's failure was in the Diffusers pipeline specifically, not
necessarily the weights. Rather than overwrite a documented failed gate with
an unrelated runtime's success, `wan2.2-ti2v-5b-mlx` is registered as its own
`ModelSpec` in `worker/models.py`, with its own provider
(`worker/providers/wan_mlx.py`). The Diffusers entry stays retired and
removal-only exactly as before.

## Install path: local MLX conversion, not a direct file copy

Unlike every other registered model, this one's app-owned snapshot is not a
copy of the upstream repository's own files. The upstream checkpoint ships
`Wan2.2_VAE.pth` and `models_t5_umt5-xxl-enc-bf16.pth` — pickle-based
PyTorch serialization, which `worker/model_security.py` forbids anywhere in
SynVid's model root (`_FORBIDDEN_SUFFIXES` includes `.pth`; pickle
deserialization is a real code-execution risk).

`worker/model_install_wan_mlx.py` instead: downloads the raw upstream files
to an ephemeral `tempfile.TemporaryDirectory()` outside any app-owned path;
verifies them against Hugging Face's own LFS SHA-256 metadata; runs the
vendored MLX conversion (`bfloat16`, single-model TI2V-5B path) producing
safetensors-only output; copies the four tokenizer files
(`google/umt5-xxl/*` in the upstream repo, same pinned revision) into a
`tokenizer/` subdirectory; discards the raw temporary directory entirely;
computes local SHA-256 checksums of the converted tree; verifies it against
`ModelSpec.allowed_files` and the forbidden-suffix check
(`model_security.verify_tree`); and atomically promotes it to
`snapshot/`, exactly like every other model's install.

Real run against this Mac's actual `~/Library/Application Support/SynVid`
paths, through this exact code path (`submit_model_download`'s dispatch to
`install_wan_mlx_snapshot` when `model_id == "wan2.2-ti2v-5b-mlx"`):

| step | result |
| --- | --- |
| raw download | 11 files, ~32 GiB, ~8 min (unauthenticated HF request rate) |
| upstream LFS verification | passed |
| MLX conversion | succeeded, bfloat16 |
| `verify_tree` on converted snapshot | passed — 8 files, no forbidden suffixes |
| installed size | 24,201,740,102 bytes (~22.5 GiB) |

## Generation gate: one real recipe, real numbers

`worker/providers/wan_mlx.py` exposes exactly one quality-approved recipe,
`"Balanced"`, matching the shape used for the qualitative check below.
Ran twice through the real production provider against the real installed
snapshot: once via `calibrate()` (writing `measured-profile.json`), once via
`run()` with a different prompt/seed (validating the exact-shape-match gate
generation requests go through).

| | value |
| --- | --- |
| shape | 1280×704, 41 frames, 24 fps, 40 steps, guidance 5.0, unipc scheduler |
| wall time | 597.7 s (~10 min) — T5 encode + model load are a small fraction; denoising dominates at ~13 s/step |
| peak process RSS (`resource.getrusage().ru_maxrss`) | 12,878,331,904 bytes (~12.0 GiB) |
| peak MLX allocator (`mx.get_peak_memory()`) | 43,117,119,592 bytes (~40.16 GiB) |
| this Mac's total memory | 48 GiB |

**The RSS/MLX-peak gap is the important finding here.** Every other provider
in this codebase (all PyTorch/MPS-backed) uses `peak_rss_bytes()` as the
meaningful memory number, and LTX's own `MIN_SYSTEM_MEMORY_BYTES` is derived
from it. For this MLX-backed provider, process RSS (12.0 GiB) drastically
understates real memory pressure — MLX's unified-memory buffers are not
fully reflected in classic RSS accounting. `calibrate()` therefore also
records `mx.get_peak_memory()`, and `MIN_SYSTEM_MEMORY_BYTES` (46 GiB) is
derived from *that* number, not RSS. A gate built the LTX way — trusting
RSS alone — would have been wrong by roughly 3.4×, incorrectly admitting a
~16 GiB Mac.

At ~40.16 GiB peak on a 48 GiB Mac, this recipe leaves real but not
generous headroom. It has only been measured on this one machine.

## Quality: real, but not the same bar as a passed gate

Two real generations were produced and directly inspected (frames pulled
with `ffmpeg`, viewed, not just probed for container validity):

1. "A lighthouse on a rocky coastline at sunset, waves crashing, cinematic
   lighting" (an earlier, pre-integration scratch run with the same shape)
   — coherent, well-exposed, matched the prompt, visible wave-position
   motion between frames.
2. The `calibrate()` and `run()` invocations documented above (same
   lighthouse prompt reused as `_CALIBRATION_PROMPT`; a second, different
   prompt/seed through the real `run()` path).

This is **not** the same evidentiary bar as the multi-prompt quality gate
that failed the Diffusers Wan2.2 TI2V-5B attempt. `worker/models.py`'s
`reason` field for this entry says so explicitly ("provisionally, not fully,
approved"), and the model is registered under the `personal-research`
profile, not `shareable`. A real quality gate — multiple prompts, multiple
seeds, explicit motion/artifact review — is real follow-up work, not done
here.

## Known gaps, stated plainly

- **PyInstaller packaging is unverified.** `build/pyinstaller/synvid-worker.spec`
  was not updated or tested against a frozen `.app` build. MLX ships Metal
  binaries that may need `collect_all('mlx')`-equivalent treatment the way
  `imageio`/`kokoro_onnx` already get in that spec; this has not been
  checked. Stage 8 (release candidate) work, not done in this pass.
- **Wan's measured controls are intentionally narrower than LTX's.** The
  Create screen now derives available quality, aspect, and duration controls
  from each model's calibration references. Wan exposes only its measured
  Balanced Landscape recipe (1280×704, 41 frames at 24 FPS); Draft/High,
  Square/Portrait, and alternate durations remain disabled rather than being
  submitted as unmeasured settings. A broader Wan control set requires new
  real output, memory, cancellation, and quality measurements for each shape.
- **Only T2V, only this one recipe.** I2V, LoRA, Wan2.1, and the 14B
  dual-model pipelines are not vendored or wired in (see
  `worker/vendor/mlx_video_wan2/NOTICE.md`).
- **Dependency footprint changed.** `mlx==0.31.1` is now a real
  `requirements.txt` dependency. `worker/vendor/mlx_video_wan2` is vendored,
  patched, MIT-licensed third-party source (not a live pip dependency,
  since `Blaizzy/mlx-video` has no matching PyPI release) — a new SBOM/license
  entry, not yet run through the license-scan tooling mentioned in the
  Stage 8 gate.

## Update — 2026-08-11: packaging fixed and verified

The "PyInstaller packaging is unverified" gap above is closed. The frozen
`.app` build was failing to import the vendored MLX runtime because
`scripts/build-worker.sh`'s PyInstaller invocation had no collection
directives for `mlx` at all; fixed by adding `--collect-data mlx
--hidden-import mlx._reprlib_fix`. Along the way, `worker/providers/wan_mlx.py`
was changed to chain the underlying import/generation exception into
`WanMlxProviderError`'s message instead of a bare "Wan MLX generation
failed", and the Rust worker supervisor now keeps a bounded 32-entry log of
recent terminal/error protocol events for the diagnostic bundle
(`WorkerSupervisor::recent_event_lines`, `app/src-tauri/src/worker.rs`) —
both added specifically because a packaged-only MLX import failure had
been opaque to diagnose without rebuilding from source.

User-verified end-to-end against the real packaged, notarizable `.app`: a
Wan 2.2 TI2V-5B (MLX) generation completed successfully. This confirms the
runtime packages and runs correctly outside a source checkout; it does
**not** change anything else in this document — the recipe is still one
shape only (Balanced Landscape), and quality is still provisional, not the
full multi-prompt gate other ✅ models passed.
