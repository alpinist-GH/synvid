# Wan2.1-14B MPS feasibility gate — failed

Checkpoint: `Wan-AI/Wan2.1-T2V-14B-Diffusers` at
`38ec498cb3208fb688890f8cc7e94ede2cbd7f68` (Apache-2.0). The SynVid-owned
snapshot was independently checksum-verified against its 29-file manifest
before testing: 80,406,918,983 snapshot bytes.

The one declared baseline used fixed seed `42`, prompt "A yellow flower gently
moving in a spring breeze", 480x480 output, 17 frames at 8 FPS, bfloat16, and
the stated sliced strategy: attention slicing plus VAE slicing and tiling. It
produced a structurally valid 2.125-second H.264/yuv420p MP4, but direct
inspection of early and middle frames found an indistinct yellow-green blur,
not a recognizable flower or watchable motion.

| dtype | strategy | steps | wall time | peak MPS allocation | peak RSS | result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| bfloat16 | attention + VAE slicing/tiling | 4 | 287.640s | 40.28 GB | 39.48 GB | invalid visual quality |

Decision: do not create a measured Wan profile and do not expose Wan2.1-14B in
the worker or UI. The downloaded snapshot remains available only for an
explicitly authorized future investigation. This baseline does not establish
that every higher-cost recipe is impossible; it does establish that this exact
Stage 3 strategy/recipe is not shippable, so the model remains unavailable.
