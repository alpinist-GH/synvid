# Stage 6 frozen-bundle gate — MPS re-validated; real frozen-only defect found, not yet fixed

## Two real bugs found and fixed first

**`worker/model_download.py` (blocking, affects every model, not just Qwen):**
the in-app `download_model` IPC path builds its file manifest from
`huggingface_hub`'s `list_repo_tree`, reading each file's LFS checksum via
`lfs.get("oid")` / `getattr(lfs, "oid", None)`. The installed
`huggingface_hub` (1.27.0) exposes that hash as `.sha256`, not `.oid` — a
renamed field in a version this project was never pinned against. Every
file's digest silently resolved to `None`, so the download always failed
with `"Pinned model manifest has no LFS checksum metadata."` before
transferring a single byte. This is not Qwen-specific: **the in-app download
flow was broken for any model that isn't already installed**, and had zero
test coverage (`tests/test_model_install.py` constructs `RemoteFile` objects
directly and never exercises the HF-API-to-`RemoteFile` translation). Fixed
by reading `.sha256` instead of `.oid`; verified the pinned Qwen manifest now
resolves 15 of 34 allow-listed files with real SHA-256 digests (the rest are
small non-LFS JSON/text files with no LFS hash, which is expected and
already handled correctly downstream). All 79 unit tests still pass.

With the fix, the pinned `Qwen/Qwen-Image-Edit` snapshot (57.7 GB, 34
allow-listed files) downloaded and verified successfully through the real
in-app `download_model` protocol path.

## MPS gate re-confirmed

Re-ran `scripts/qwen_image_edit_smoke.py` against the freshly (re-)installed
snapshot: bf16, 512×512, 4 steps, guidance/true-CFG 1.0, wall time 584.6s,
peak MPS allocation 57.74 GB — matching the original 2026-08-08 gate
(621.32s, same peak allocation) within normal run-to-run variance. Direct
inspection of the output (a plain blue square instructed to become plain
orange) shows a clean, correct solid-orange result. `measured-profile.json`
was rewritten from this real run, so `edit_image` is measured and available
in the running app again.

## Frozen-bundle end-to-end run: reproducible defect, not fixed

The explicitly-flagged remaining Stage 6 item — a real `edit_image` run from
the relocated frozen `.app` — was attempted and **fails**, in a way that does
not reproduce from source:

1. Generated a real source image via the frozen worker (`flux-schnell`,
   succeeded normally, ~42s).
2. `edit_image` against that source (frozen worker, `qwen-image-edit`)
   fails immediately after model load:

   ```
   Unrecognized video processor in .../qwen-image-edit/snapshot/processor.
   Should have a `video_processor_type` key in its video_preprocessor_config.json
   ... or one of the following `model_type` keys ...
   ```

   The referenced file **does** contain the expected key
   (`"video_processor_type": "Qwen2VLVideoProcessor"`) — inspected directly,
   it's well-formed and matches what `config.json`/`model_index.json`
   declare (`processor: ["transformers", "Qwen2VLProcessor"]`).

3. Ruled out as causes: PATH restriction (fails identically with full PATH
   inherited vs. `/usr/bin:/bin` only); a stale/cached HuggingFace "dynamic
   modules" lookup (no `~/.cache/huggingface/modules` directory exists, and
   the worker never sets `HF_HOME`/`TRANSFORMERS_CACHE`, so frozen and
   source runs would consult the same, unmodified default cache location
   regardless).
4. **Not yet ruled out / most likely cause**: `scripts/build-worker.sh` uses
   `--copy-metadata transformers` (package metadata only), not a full data
   collection for `transformers`. `AutoProcessor`/video-processor
   type-resolution in this installed `transformers` version (5.14.1 —
   unpinned, whatever the environment currently resolves) may rely on
   registration data PyInstaller's default analysis doesn't collect for a
   library this large and dynamically structured. The identical code
   (`worker/providers/qwen_image_edit.py`'s `_load` is effectively the same
   `QwenImageEditPipeline.from_pretrained(...)` call as
   `scripts/qwen_image_edit_smoke.py`, which succeeds from source every
   time) makes an environment/packaging difference the most likely
   explanation, not a code bug in the provider itself.

**Update**: tried `--collect-data transformers` in `scripts/build-worker.sh`
(added, full worker+app rebuild, reinstalled, re-tested). Identical failure,
byte-for-byte the same error message. This rules out a missing-data-file
explanation as the fix, at least via this specific flag; the change is left
in place since it's harmless and may still be needed alongside whatever the
real fix turns out to be. `--collect-all transformers` (which also pulls
binaries/hidden submodules, not just data) has not been tried — that's the
next thing to attempt, though the ruled-out data-collection theory makes it
a lower-confidence next guess than when this doc was first written. Given
the identical error persists with real data files confirmed collected, the
more likely remaining explanation is a `transformers` **code-path**
difference under PyInstaller's frozen import machinery (e.g. an
entry-point/plugin-style auto-registration that runs at a different time or
not at all when frozen) rather than a missing file. Stage 6's frozen-bundle
acceptance item remains open — the MPS/model correctness gate is solid, but
the packaged `.app` cannot currently complete a real image edit.
