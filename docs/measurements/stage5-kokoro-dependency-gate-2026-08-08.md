# Stage 5 Kokoro dependency and asset gate — 2026-08-08

## Selected runtime

Stage 5 uses `kokoro-onnx==0.5.0`: its runtime is MIT-licensed and the
Kokoro model is Apache-2.0. It leaves the existing LTX/Diffusers lock intact.
`pip check` is required after the dependency update.

## Asset pairing

The supported files are the official `model-files-v1.0` release assets:

- `kokoro-v1.0.fp16.onnx` (177,464,787 bytes, SHA-256
  `c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204`)
- `voices-v1.0.bin` (28,214,398 bytes, SHA-256
  `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d`)

The latter is a NumPy key/value voice bundle required by `kokoro-onnx`; it is
not interchangeable with a single raw Hugging Face `.bin` embedding. SynVid
uses the release's `af_bella` stock voice. On 2026-08-08, SynVid downloaded
both files after explicit authorization into Application Support staging,
verified the sizes and SHA-256 values above, confirmed the voice bundle opens
with `numpy.load(..., allow_pickle=False)`, then atomically promoted the
snapshot. The raw Hugging Face asset from the first attempt is retained outside
the active snapshot as an incompatible backup, not used at runtime.

## Real synthesis and mux gate

On 2026-08-08, using the active Application Support snapshot and the bundled
`imageio-ffmpeg` binary, `af_bella` synthesized `Hi.` to a 0.597333-second
WAV. SynVid padded it to a 1.125-second, 256x256, 8-FPS source clip, then
replaced the video audio. `ffprobe` reported H.264 video and AAC audio, both
exactly 1.125 seconds. An overlong control (`Hello from SynVid.`) synthesized
to 1.19 seconds and was correctly rejected for that same 1.125-second video.

The worker unloads every diffusion provider before starting Kokoro and unloads
Kokoro after each job. This is the selected no-co-residency policy; it avoids
claiming that TTS and a diffusion model fit together without an additional
concurrent-memory experiment.

## Remaining manual acceptance

Listen to a narration created from the installed-app Add Voice flow. Recheck
export profiles and any future interpolated-FPS output with `ffprobe` and
playback to confirm A/V sync before marking Stage 5 complete.
