# Wan2.2 TI2V-5B Apple Silicon gate — runtime passed, quality failed

The model is wired into the reviewed SynVid catalog at the immutable
Diffusers revision `bfbd0086538bbf9b0f7c1f1939879d65e1f872ce`.

The authorized snapshot was downloaded and verified: 20 files and
34,201,418,400 installed bytes. Direct Diffusers `WanPipeline` execution on
MPS completed successfully with valid H.264/yuv420p MP4 output.

| dtype | size | frames | steps | wall time | peak MPS allocation | visual result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| bfloat16 | 256×256 | 9 | 4 | 25.25 s | 22.8 GB | unusable color wash/blur |
| bfloat16 | 256×256 | 9 | 20 | 23.77 s | 22.8 GB | unusable blur |
| bfloat16 | 480×480 | 17 | 4 | 58.03 s | 22.8 GB | overexposed and blurry |
| bfloat16 | 480×480 | 17 | 20 | 90.02 s | 22.8 GB | overexposed and blurry |
| float16 | 480×480 | 17 | 20 | 102.50 s | 24.8 GB | same failure |

All runs produced the requested dimensions, frame counts, FPS, and playable
MP4 containers. Direct frame inspection found no watchable quality, so the
profile is exposed only as an explicitly experimental text-to-video test
profile; it is not a quality-approved shipping profile. Image-to-video remains
unavailable until its own provider and gate pass.

This tested checkpoint is the full BF16 Diffusers model, not an Apple-native
quantized MLX derivative. A separate MLX/q8 TI2V-5B experiment may still be
useful, but it requires its own runtime integration and validation.
