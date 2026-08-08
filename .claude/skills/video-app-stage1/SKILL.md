---
name: video-app-stage1
description: Validate the LTX backend feasibility gate for SynVid.
---

Read `/Users/alpinist/Github/synvid/PLAN.md` and confirm Stage 0's device,
lock, and frozen-worker gates have evidence before changing production model
code. SynVid uses Tauri and contained JSON-lines sidecar IPC: never add
FastAPI, Uvicorn, an HTTP listener, generic shell access, or media bytes in
IPC.

Implement only the Stage 1 LTX gate: one pinned/reviewed LTX revision, a
capability provider adapter, the shared single-active-job contract, atomic
output sidecars, and real MPS measurements. Inspect a generated clip directly;
protocol success, imports, or compilation are not evidence. Stop if a stable,
watchable MPS output is not achieved within the recorded budget.
