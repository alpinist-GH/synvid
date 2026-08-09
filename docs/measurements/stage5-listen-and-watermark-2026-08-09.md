# Stage 5 narration — "listen" verification and watermark disclosure review

Both gate docs from 2026-08-08 ended with "remaining manual acceptance:
listen to a narration created from the installed-app Add Voice flow." That
listening step cannot be performed by an agent; as a substitute, this session
generated a real narration and verified the result is genuinely non-silent
speech (not merely a present-but-empty audio track), which the user can
independently confirm by listening to the attached sample.

## Narration correctness (source-mode worker, real Kokoro model)

Against edited output `9e1a329a-ede4-479a-81a1-299e8fe12d46` (1.125s video):

- **Overlong rejection**: "The flower blooms as morning light spreads across
  the water." (3.43s of speech) was correctly rejected: `"Narration is
  3.43s but the video is 1.12s; shorten the script."` — job failed cleanly,
  no partial output.
- **Short/padded happy path**: "Hi." (0.597s of speech) succeeded, producing
  output `7457e838-5c82-4192-b1ca-26a6439fbbb7`. `ffprobe` confirmed an AAC
  audio stream (24kHz mono) with duration exactly matching the video
  (1.125s). `ffmpeg silencedetect` confirmed non-silent audio from 0s to
  ~0.41s followed by silence padding to the end — i.e. real speech followed
  by correct padding, not a silent or corrupt track. Lineage recorded
  `narrated_from` back to the source video.
- A copy of this narrated clip was handed to the user directly so they can
  perform the actual listening step this session cannot do.

## Watermark disclosure

PLAN.md's Stage 5 bullet says to "disclose the TTS watermark." Kokoro-82M
(via `kokoro-onnx` 0.5.0) does not embed a technical audio watermark —
unlike some commercial TTS APIs, there is no inaudible fingerprint to
disclose here. The existing UI copy ("Kokoro uses the installed stock voice"
in `index.html`, and README's "Kokoro uses a stock voice; it does not clone
a person") already covers the substantive disclosure this bullet is
protecting: that the voice is synthetic/stock, not a captured or cloned
identity. No watermark-specific UI text was added, since adding language
about a watermark that does not exist would be inaccurate rather than more
disclosive.

## Still open

Genuine VoiceOver/keyboard-only operation of the Add Voice panel, and
repeating this flow from a clean macOS account, remain open per the
installed-app acceptance checklist.
