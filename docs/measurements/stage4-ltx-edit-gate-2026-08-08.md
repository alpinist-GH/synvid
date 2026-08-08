# Stage 4 LTX video-editing gate — passed

Model: `Lightricks/LTX-Video` at
`8984fa25007f376c1a299016d0957a37a2f797bb`, using SynVid's existing
checksum-verified snapshot. No model was downloaded. Wan remains unavailable
because its Stage 3 MPS quality gate failed, so no Wan source/output exists for
comparison.

LTX's measured High recipe (256x256, 9 frames, 8 FPS, 12 steps) generated
source `cb5d8351-dee2-4304-952f-626ecc4e0934`. The source remained a separate
immutable output while these descendants were atomically promoted:

| Change amount | Descendant output ID | Source-conditioning strength | Prompt summary |
| ---: | --- | ---: | --- |
| 0.05 | `4ee1dd68-c567-4364-a392-1c63fa7ddbad` | 0.95 | preserve flower while making a moonlit ice-flower scene |
| 0.75 | `a3f4836e-6372-45c0-9c4b-f6e0cd5595f2` | 0.25 | cool moonlit garden, preserve the flower silhouette |

`ffprobe` verified the edited output as H.264/yuv420p, 256x256, 9 frames at
8 FPS, with 1.125-second duration. Each metadata sidecar records
`edited_from` lineage, the decoded 8-FPS/256x256/9-frame source, matching
target facts, and the `identity` preprocessing policy. The completed outputs
retain the canonical-video export controls.

Direct frame inspection showed the low-change sample preserving the flower and
the 0.75 sample visibly altering the scene while retaining a recognizable
flower form. Their sampled-frame mean absolute RGB difference was 34.2/255.
The 0.95 exploratory result was visibly degraded, so the accepted validation
range stops at 0.75; the UI may keep 0.95 available as an explicit maximum,
but it is not represented as a quality preset.

Diffusers 0.39.0's `LTXConditionPipeline` did not supply `mu` to its
dynamic-shift scheduler. SynVid therefore disables dynamic shifting only for
that condition pipeline and retains its pinned static shift. The initial edit
mapping also left source conditioning at the pipeline's hard-preserve default;
it now maps change amount to both denoising and inverse source conditioning.
Text-to-video behavior is unchanged. Re-evaluate this workaround when the
pinned Diffusers version changes.
