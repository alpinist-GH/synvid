# HunyuanVideo 1.5 480p T2V MPS gate — passed

Checkpoint: `hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_t2v` at
`286be7ce72277246578a3e3cc2487e95ddae5bcf` (Tencent Hunyuan Community License,
territory-restricted; personal-research profile only). The SynVid-owned
snapshot (53.4 GiB, 33 files) was checksum-verified against its manifest
before every test run.

## Registry bug found and fixed

The first run failed inside `encode_prompt` with `tokenizer.chat_template is
not set`: `worker/models.py`'s `_HUNYUAN_DIFFUSERS_FILES` allowlist covered
`tokenizer/*.json|*.txt|*.model|merges.txt|vocab.json` but not
`tokenizer/*.jinja`, so the Qwen2.5-VL tokenizer's `chat_template.jinja` —
required by `HunyuanVideo15Pipeline._get_mllm_prompt_embeds` — was silently
never downloaded. This meant the model could never generate, regardless of
measured settings. Fixed by adding `tokenizer/*.jinja` and
`tokenizer_2/*.jinja` to the allowlist, deleting the incomplete snapshot, and
re-downloading/re-verifying the full 53.4 GiB tree (33 files, `chat_template.jinja`
now present). A regression test was added
(`tests/test_registry.py::test_hunyuan15_allowlist_includes_the_mllm_chat_template`).

A second, smaller correctness fix: `HunyuanVideo15Pipeline.__call__` has no
`guidance_scale` parameter on diffusers 0.39.0. CFG strength comes from the
pipeline's `ClassifierFreeGuidance` guider component
(`guider/guider_config.json`, upstream default `guidance_scale=6.0`), not a
per-call argument — matching what `worker/providers/hunyuan.py`'s `run()`
already did (it never passed `guidance_scale` to the pipeline call either).

## Candidates

All candidates used a fixed seed (`42`), prompt `"A yellow flower gently
moving in a spring breeze"`, 848x480, bfloat16, VAE tiling enabled, and
`guidance_scale=6.0` (the pipeline's baked-in guider default). MPS peak
allocation was sampled from a background polling thread (0.5s interval)
since this pipeline does not expose `callback_on_step_end` on diffusers
0.39.0.

| frames | steps | wall time | peak MPS alloc | peak RSS | result |
| --- | ---: | ---: | ---: | ---: | --- |
| 25 | 10 | 680.1s | 34.3 GiB | 12.5 GiB | valid, sharp, watchable |
| 61 | 20 | — | — | — | **thrashed**: killed after 31 min with ~60s of CPU time consumed, system memory nearly exhausted (707 MB free, heavy compressor/swap churn), no forward progress |
| 25 | 20 | 1087.8s | 34.3 GiB | 23.3 GiB | valid, sharp, watchable — **selected as Balanced** |

Peak MPS allocation was identical between the 10-step and 20-step 25-frame
runs (as expected: step count changes wall time, not the resident latent/
activation footprint). The 61-frame attempt raising memory into thrashing
territory shows the diffusion transformer's own attention state — not just
the VAE decode, which tiling already bounds — scales with frame count, and
this Mac's unified memory cannot safely hold a much longer sequence at this
resolution. Frame counts of 61+ are unmeasured and must not be exposed in
the UI without a dedicated follow-up gate (e.g. testing intermediate values
such as 33/41/49 frames, or a reduced resolution at higher frame counts).

Direct frame inspection of both passing candidates (5 frames sampled evenly
across each clip) found sharp petal detail, a plausible depth of field, a
consistent background, and subtle coherent motion between frames — a clearly
higher quality bar than the Wan2.1 gates, which were blurry even at 20 steps.

## Decision

Ship 848x480 / 25 frames / 24 fps (1.04s) / 20 steps / bfloat16 /
`guidance_scale=6.0` as the measured `Balanced` recipe. Written to
`~/Library/Application Support/SynVid/models/hunyuan15-480p-t2v/measured-profile.json`
as `{"recipes": {"Balanced": {...}}, "schema_version": 2}`, matching the
format `HunyuanMeasuredProfile.from_json` and the existing LTX profile both
use. Verified loadable through `HunyuanMeasuredProfile.from_json` directly
and, separately, by invoking the real `HunyuanVideo15Provider.run()` end to
end (model verification, pipeline load, generation, progress callbacks,
export, `unload()`) with a matching-shape small candidate — not just the
standalone measurement script.

This closes the gap called out in the 2026-08-09 README close-out note ("no
measured-profile file on disk at all despite being selectable in the UI").
I2V was not touched by this gate and remains an unmeasured test placeholder.
