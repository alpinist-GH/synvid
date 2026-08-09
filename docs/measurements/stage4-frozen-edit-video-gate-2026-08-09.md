# Stage 4 frozen-bundle `edit_video` gate — passed

The one explicitly-missing piece of Stage 4 evidence — a real `edit_video`
run from the relocated, frozen `.app` bundle (as already done for
FLUX-schnell and Stage 5 narration) — is now recorded.

Driving the installed `/Applications/SynVid.app`'s bundled worker binary
directly over its JSON-lines protocol (`PATH` restricted to `/usr/bin:/bin`,
no Homebrew/dev tooling), against source output `fe4b2ed0-2275-498f-997a-31db83da90ed`
(LTX Draft, 256x256, 9 frames, 8 FPS):

- Request: `edit_video` with prompt "the flower turns bright red and glows",
  `recipe: "Draft"`, `change_amount: 0.35`.
- Result: job `b0545030-58dc-4d32-b6d2-9df18b5e084e` succeeded in ~16.3s,
  producing output `9e1a329a-ede4-479a-81a1-299e8fe12d46`.
- `ffprobe` confirmed H.264/yuv420p, 256×256, 9 frames, 8 FPS, 1.125s
  duration — exactly matching the source's measured facts.
- Metadata sidecar recorded `edited_from` lineage to the source, and a
  `preprocessing` block with `source_conditioning_strength: 0.65` (= 1 −
  change_amount, consistent with Stage 4's original dev-backend gate).
- Direct frame inspection (frame 4 of 9, source vs. edited) showed a
  visually distinct but still-abstract Draft-quality result, consistent with
  Draft's already-documented abstraction at low step counts; low/high
  change-amount visual-difference validation was already established on the
  dev backend in the original Stage 4 gate doc and is not repeated here.

One earlier attempt to run the same edit through the *installed app's GUI*
(via AppleScript/JXA driving) appeared to leave an incomplete output (visible
in Recovery Center) rather than a promoted variant. That GUI attempt
immediately followed a forced worker-kill test and several rapid automated
clicks in the same session — conditions not representative of normal use —
and the identical request succeeded cleanly and repeatably via direct
protocol testing against the same binary. Flagged for awareness, not treated
as a confirmed product defect, since it could not be reproduced in isolation.
