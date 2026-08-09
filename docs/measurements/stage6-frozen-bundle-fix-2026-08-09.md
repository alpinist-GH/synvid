# Stage 6 frozen-bundle blocker — root-caused and fixed

Follows on from `stage6-qwen-frozen-bundle-2026-08-09.md`, which left the
frozen-bundle `edit_image` acceptance item open with an unconfirmed root
cause. Both real bugs found in that investigation are now fixed.

## Bug 1: PyInstaller silently drops dynamically-imported `transformers` submodules

`transformers`' `Auto*` dispatchers (`video_processing_auto.py`,
`image_processing_auto.py`, `tokenization_auto.py`, `configuration_auto.py`,
`processing_auto.py`, `feature_extraction_auto.py`) all resolve a model's
per-type submodule the same way:

```python
module = importlib.import_module(f".{module_name}", "transformers.models")
```

`module_name` is built from data (a model's `config.json` / registry
mapping), not a literal string PyInstaller's static import-graph analysis can
see. So PyInstaller only bundles a given model submodule if something *else*
in the traced graph happens to import it directly. Confirmed by diffing the
frozen bundle against the venv source: 22 of 27 `transformers/models/*/video_processing_*.py`
files made it into `_internal/transformers/models/` by accident; 5 did not,
including `transformers/models/qwen2_vl/video_processing_qwen2_vl.py` — the
exact file `AutoVideoProcessor` needs to resolve `Qwen2VLVideoProcessor` for
`QwenImageEditPipeline`. This is why the error was
`Unrecognized video processor ... Qwen2VLVideoProcessor` even though the
model's own `video_preprocessor_config.json` correctly declared that type:
the class genuinely wasn't on disk in the frozen bundle.

`--collect-data transformers` (tried in the prior session) only collects
non-`.py` resource files, so it could never have caught this — the missing
files are Python source, not data.

**Fix**: added `--collect-submodules transformers.models` to
`scripts/build-worker.sh`. This forces every per-model `.py` file into the
bundle regardless of static reachability. Verified: all 27
`video_processing_*.py` files (previously 22) are now present in the rebuilt
`_internal/transformers/models/` tree, including `qwen2_vl`'s.

## Bug 2: no cross-provider unload before running a job (MPS OOM)

Fixing bug 1 revealed a second, independent real bug. Each provider
(`FluxSchnellProvider`, `QwenImageEditProvider`, etc.) caches its loaded
pipeline in `self._pipeline` until `.unload()` is called explicitly.
`GenerationService._submit` — the shared entry point behind `generate`,
`edit_video`, and `edit_image` — never called `.unload()` on any *other*
provider before running a job on the selected one. Nothing in the Rust/UI
layer ever sends an `unload_model` request either (confirmed by grep). So a
`flux-schnell` generation immediately followed by a `qwen-image-edit` edit —
exactly the realistic workflow Stage 6 acceptance requires — tried to hold
both pipelines resident at once: flux-schnell's ~31 GiB measured peak plus
qwen-image-edit's ~58 GiB measured peak exceeds the machine's ~64 GiB MPS
ceiling. First frozen-bundle rerun after the bug 1 fix failed with:

```
MPS backend out of memory (MPS allocated: 63.56 GiB, other allocations: 1.69 MiB, max allowed: 63.65 GiB).
```

The narration path (`_submit_narration`) already had the correct pattern —
it explicitly unloads every provider before synthesizing speech, with the
comment "Diffusion models are substantially larger than the narrator. Do not
co-reside without a measured combined-memory budget." That same guard was
simply missing from `_submit`.

**Fix**: `worker/service.py`'s `_submit` now unloads every provider other
than the one about to run (and the narrator, if present) before starting the
job. All 80 existing unit tests still pass unchanged.

## Verification: real frozen-bundle end-to-end run

After both fixes and a full worker rebuild, ran the frozen `synvid-worker`
binary (not from source) via its real stdin/stdout IPC protocol, with
`SYNVID_APP_SUPPORT` pointed at a real installed Application Support
directory (existing `flux-schnell` and `qwen-image-edit` installs, no
relocation needed to reproduce this specific defect class):

1. `generate` with `flux-schnell` — succeeded, produced a source image.
2. `edit_image` with `qwen-image-edit` against that source, prompt "change
   the square to plain solid orange" — succeeded. Terminal event:
   `state: succeeded`, 4/4 steps, no error.

Metadata for the resulting output confirms correct lineage
(`"relation":"edited_from"` pointing at the source output) and the real
request parameters (prompt, seed, steps). Direct visual inspection of the
output image confirms the instructed color change was applied to the
source's square region while the surrounding area was left alone — the
instruction visibly changed the output, satisfying this stage's own
acceptance bullet.

Total wall time for the combined generate+edit run was approximately 12.5
minutes, consistent with the previously measured ~585s Qwen Image Edit load
already accounting for most of that.

Stage 6 is complete.
