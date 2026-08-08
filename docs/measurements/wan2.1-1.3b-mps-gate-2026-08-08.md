# Wan2.1-1.3B MPS feasibility gate — failed

Checkpoint: `Wan-AI/Wan2.1-T2V-1.3B-Diffusers` at
`0fad780a534b6463e45facd96134c9f345acfa5b` (Apache-2.0). The SynVid-owned
snapshot was checksum-verified against its 19-file manifest before testing.

All candidates used a fixed seed (`42`), 480x480 output, 17 frames at 8 FPS,
and an H.264/yuv420p media validation. Every candidate produced a structurally
valid 2.125-second MP4, but direct frame inspection found the output too blurry
to be watchable.

| dtype | steps | wall time | peak RSS | result |
| --- | ---: | ---: | ---: | --- |
| bfloat16 | 4 | 45.858s | 34.7 GB | invalid visual quality |
| bfloat16 | 20 | 98.426s | 35.3 GB | invalid visual quality |
| float16 | 20 | 104.913s | 34.9 GB | invalid visual quality |

Decision: do not create a measured Wan profile and do not expose Wan in the
worker or UI. Its downloaded snapshot remains available only for an explicitly
authorized future investigation; it is not a supported SynVid generation
option. This preserves the Stage 3 rule that a model which cannot produce a
stable watchable output remains unavailable.
