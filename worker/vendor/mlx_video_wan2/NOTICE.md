# Vendored from Blaizzy/mlx-video

Source: https://github.com/Blaizzy/mlx-video
Pinned commit: `87db56a51758fefb748a359b90a5283bb8ba4837`
License: MIT (see `LICENSE`, copyright Prince Canuma)

Not available on PyPI under a matching name (`mlx-video` on PyPI is an
unrelated package), so the two subtrees SynVid's Wan2.2 TI2V-5B provider
depends on are vendored directly instead of pulled as a live dependency:

- `wan_2/` — the full `mlx_video/models/wan_2/` module.
- `ltx_2_shared/` — only the two files `wan_2` imports from
  `mlx_video/models/ltx_2/`: `config.py` (for `BaseModelConfig`) and
  `video_vae/tiling.py` (for `TilingConfig`). Both are leaves with no
  further `mlx_video` imports reachable from Wan's usage.

## Changes from upstream

All imports were rewritten from absolute `mlx_video.models...` paths to
relative imports within this vendored tree (mechanical, no behavior change).

`wan_2/generate.py`'s `generate_video()` was patched for two reasons:

1. It called `AutoTokenizer.from_pretrained("google/umt5-xxl")` — an
   unpinned network fetch at generation time, incompatible with SynVid's
   fully-offline, pinned-revision model security policy
   (`worker/model_security.py`). Added a `tokenizer_path` parameter; the
   provider passes a local path to the tokenizer files already bundled in
   Wan2.2-TI2V-5B's own snapshot (`google/umt5-xxl/*` in the upstream repo,
   at the same pinned commit as the rest of the checkpoint) instead of
   letting it hit the network.
2. The denoising loop had no progress or cancellation hook (just a bare
   `tqdm` bar) and the function prints extensively via bare `print()`. In
   this worker, `sys.stdout` is the JSON-lines protocol channel Rust
   parses — unmediated prints would corrupt it. Added optional `progress`
   and `cancelled` callback parameters, called once per denoising step,
   matching the pattern every other SynVid provider uses
   (`worker/providers/ltx.py`'s `on_step_end`). Existing informational
   `print()` calls are left as-is; the provider neutralizes them by
   rebinding `print` inside this module's own namespace only (never
   touching `builtins.print` or `sys.stdout`, so concurrent protocol
   replies from other threads are unaffected) rather than editing every
   call site.

LoRA support (`mlx_video.lora`) and the `VideoEncoderModelConfig` codepath
in `ltx_2_shared/config.py` reference modules that were not vendored (LTX-2
generation, LoRA loading) because SynVid's Wan2.2 TI2V-5B provider never
exercises those paths. Both are lazy imports inside functions/methods this
provider never calls; they would raise `ImportError` only if invoked.

## Not vendored / not supported here

- LoRA loading (`--lora*` flags in the original CLI).
- I2V-14B channel-concat conditioning, Wan2.1, Wan2.2 T2V/I2V-14B dual-model
  pipelines, and quantized conversion. Only the single-model Wan2.2 TI2V-5B
  shape SynVid measured and registered is exercised.
