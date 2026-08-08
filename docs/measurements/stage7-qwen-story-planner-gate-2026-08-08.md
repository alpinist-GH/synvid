# Stage 7 — Qwen Story Planner MPS gate (2026-08-08)

Candidate: `Qwen/Qwen2.5-1.5B-Instruct` at immutable revision
`989aa7980e4cf806f80c7fef2b1adb7bc71aa306`. The model is Apache-2.0,
does not require gated-access acceptance, and the existing SynVid install was
verified against its allowlist and SHA-256 manifest. Disk use was 2.9 GiB.

The host-MPS smoke test used bf16 and asked for three scenes from a compact
coastal-traveller premise and style bible. It returned exactly three non-empty
`{prompt, narration}` objects in 6.54 seconds (7.27 seconds process wall
time); peak process memory footprint was 3,938,080,600 bytes. The model was
explicitly unloaded afterward.

The representative structured-output batch then included an instruction that
attempted to override the requested JSON-only format. The planner returned
non-JSON output and SynVid rejected it with `planner returned invalid JSON`.
No story was modified. This is correct fail-closed behavior, but it means the
candidate does **not** pass the Stage 7 requirement for strict JSON-schema
output across representative and adversarial prompts.

Result: local **Draft scenes locally** remains unavailable in the product.
Manual Story Mode remains available; no remote LLM/API fallback is used. A
replacement model or constrained decoding strategy needs a fresh license,
download, MPS, adversarial-output, cancellation, and frozen-worker gate before
the control can be re-enabled.
