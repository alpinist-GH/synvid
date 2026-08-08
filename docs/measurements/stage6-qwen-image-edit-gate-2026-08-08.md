# Stage 6 — Qwen Image Edit MPS gate (2026-08-08)

Shareable-profile choice: `Qwen/Qwen-Image-Edit` at immutable revision
`ac7f9318f633fc4b5778c59367c8128225f1e3de`, Apache-2.0. The verified
SynVid installation contains 34 allowlisted files totaling 57,718,778,989
bytes. It was installed atomically only after upstream LFS and local SHA-256
verification.

The local MPS smoke gate used bf16, 512x512, four inference steps, a fixed CPU
seed of 42, and a synthetic blue square input. The instruction asked for a
bright orange square. It produced a 512x512 PNG at
`temporary/qwen-image-edit-smoke.png`; direct visual inspection confirmed the
source changed to orange. The source is synthetic and contains no personal or
licensed media.

Measured first-run wall time was 621.32 seconds. MPS current allocation after
generation was 57,736,179,968 bytes; process peak RSS was 662,601,728 bytes.
macOS reports `ru_maxrss` in bytes, and the smoke script records that platform
unit correctly. The output profile is stored only after PNG validity and
source-difference checks pass.

This establishes the local model/MPS feasibility gate. The Stage 6 UI and
service still require an end-to-end app run from a relocated frozen bundle
before the stage can be accepted as complete.
