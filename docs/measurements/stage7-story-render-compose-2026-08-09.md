# Stage 7 real end-to-end render_story + compose_story — passed, with one real bug found and fixed

Every prior Stage 7 artifact (`docs/measurements/stage7-qwen-story-planner-gate-2026-08-08.md`
and the extensive `tests/test_stories.py`/`test_story_render.py` suites) exercised
persistence, invalidation, and portability logic with fake providers. Nothing
had exercised `render_story`/`compose_story` against real LTX/flux-schnell/Kokoro
output end-to-end. This session did.

## Real 3-scene story

Story "The Lighthouse Keeper" — style bible: "Mara: an elderly woman in a worn
navy coat and red scarf... Setting: a white stone lighthouse on a rocky cliff,
stormy grey sea. Consistent painterly, muted watercolor style." Three scenes,
each reusing the character/location description, one with narration.

`render_story` (`through: "narration"`), all scenes approved:

- Scene 1: still (flux-schnell) → clip (LTX) → narration (Kokoro, "Mara.").
- Scenes 2–3: still → clip (no narration — see note below).
- Job succeeded end-to-end in ~4.5 minutes wall time; progress checkpoints
  arrived exactly at each scene/step boundary ("Scene 2: generating key
  image", etc.), matching the resumable-checkpoint design.
- Confirmed each artifact is a real, separately promoted, immutable output
  with correct lineage — not a fake/mocked value.

**Narration duration constraint discovered**: the currently measured LTX
recipe is only 1.125s/scene (9 frames @ 8 FPS — a Stage 1 feasibility
artifact, not yet a production duration). A first attempt with natural
sentence-length narration ("Every evening, Mara climbed the old stairs." ≈
2.69s) was correctly rejected by the same overlong-speech gate validated in
Stage 5, and correctly halted the whole `render_story` job for that scene.
This is compliant behavior, not a bug, but it is a real practical constraint
on Story Mode's narration usefulness at the current measured video profile —
narration only fits a word or two per scene until a longer LTX recipe is
measured. Worth flagging as a product-facing limitation, not just a test
artifact.

## `compose_story` — real bug found and fixed

The first `compose_story` attempt on the fully-rendered story failed
immediately with a generic `"could not compose the story movie"` error.
Direct reproduction (bypassing the generic exception message to capture the
real `ffmpeg` stderr) found the actual cause:

```
[concat @ ...] Impossible to open '/tmp/segment-0.mp4nfile'
```

`worker/story_compose.py`'s concat-list writer had `"'\\n"` where it needed
`"'\n"` — one extra backslash meant the "newline" between entries in the
ffmpeg concat listing was the **literal two characters `\n`**, not an actual
line break, so ffmpeg read `segment-0.mp4` and the next line's `file` keyword
as one bogus path. This broke composition for **any** story with two or more
approved scenes — every multi-scene composition would have failed, and none
of the existing tests catch it because `tests/test_story_render.py` mocks
`subprocess.run` rather than exercising real `ffmpeg`. A second, latent
over-escaping bug in the same line (`'\\\\''` instead of `'\\''` for
single-quote-escaping inside paths) was fixed at the same time; it hadn't
triggered yet since none of our output paths contain a literal `'`, but it
was wrong for the same reason.

Fixed in `worker/story_compose.py` line 65. All 79 unit tests still pass
(none exercised this path). Re-ran composition against the same real
3-scene story:

- Job succeeded, output verified with `ffprobe`: H.264/yuv420p 256×256,
  27 frames total (9+9+9, exactly matching the three source scenes), AAC
  audio present, 3.42s duration (3×1.125s scene video + narration/audio
  overhead). Per-frame `pts_time` inspection confirmed a consistent 8 FPS
  cadence with no dropped/duplicated frames or desync across the two
  hard-cut boundaries (the container's declared `r_frame_rate` shows an
  unrelated `40/1` artifact from the concat demuxer's raw stream copy —
  `avg_frame_rate` and the actual frame timestamps are correct).
- Lineage recorded all three contributing scene output IDs.
- A copy of the composed movie was handed to the user directly.

## Continuity review (repeated character/location)

Direct frame inspection of the composed movie (one frame per scene) shows
recognizable continuity from the shared style bible: the same white stone
lighthouse and rocky, stormy coastline appears in all three scenes; a figure
in a red scarf/coat is visible and recognizable in scenes 1 and 3 (scene 2 is
a wide shot of the lit lamp with no clear figure). This is prompt/style-bible
*guidance*, not identity-preserving generation — pose, exact framing, and
fine character detail vary scene to scene, consistent with PLAN.md's own
framing that this must be labeled as continuity guidance, not identity
retention, absent a separate reference-conditioned model gate.

## Still open for Stage 7

`through: "subtitles"` (segmented TTS timing + `.srt`) was not exercised
this session (blocked by the same narration-duration constraint above — a
segmented multi-cue narration needs more than 1.125s of video to be
meaningful). The full relocated-`.app` story lifecycle (create → render →
compose → export → crash-recovery from the installed bundle specifically)
and the `.synvidstory` real export/import round-trip against this concrete
story remain open; portability logic itself is already well unit-tested
(`tests/test_stories.py`).
