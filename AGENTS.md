# Repository Guidelines

## Project Structure & Module Organization

This repository is in the planning stage. `PLAN.md` is the architecture and acceptance authority. The intended layout is:

- `app/src/`: Tauri web UI (HTML, CSS, TypeScript).
- `app/src-tauri/`: Rust commands, worker supervision, and permissions.
- `worker/`: Python inference, model registry, jobs, outputs, and Story Mode.
- `tests/`: protocol, lifecycle, persistence, and media tests.
- `packaging/` and `scripts/`: worker freezing, app builds, and release tooling.
- `venv/`: local development environment only; never ship or commit its contents.

`.claude/skills/` describes an older FastAPI design and stale `ai-video` paths. Do not follow it where it conflicts with `PLAN.md`.

## Build, Test, and Development Commands

The application scaffold and lockfiles do not exist yet. The currently valid environment check is:

```sh
./venv/bin/python -m pip check
```

After scaffolding, prefer repository scripts and locked tools. Expected checks include `./venv/bin/python -m pytest tests`, `npm --prefix app test`, `npm --prefix app run build`, and `cargo test --manifest-path app/src-tauri/Cargo.toml`. Run only commands backed by committed configuration.

## Coding Style & Naming Conventions

Use four spaces in Python and two in TypeScript, HTML, and CSS. Format Rust with `cargo fmt`; use configured frontend tooling once added. Use `snake_case` for Python, `camelCase` for TypeScript values, and `PascalCase` for types/components. Keep IPC messages versioned, typed, bounded, and free of media bytes.

## Testing Guidelines

Use `test_*.py` for Python and `*.test.ts` for frontend tests; run Rust tests through Cargo. No numeric coverage threshold is defined. Cover failure and cancellation paths. Model features require real MPS output inspection and metadata validation; compilation or a protocol response is insufficient.

## Commit & Pull Request Guidelines

There is no Git history yet. Use short imperative commits such as `feat(worker): add version handshake` or `docs(plan): clarify story checkpoints`. Keep commits stage-scoped and exclude models, generated media, caches, tokens, and unrelated files.

Pull requests should identify the `PLAN.md` stage, summarize behavior and risks, list exact verification, and include UI screenshots. Call out model revisions, licenses, download sizes, and unverified runtime gates. Signing, notarization, upload, and publication require separate authorization.

## Security & Local-Only Contract

Preserve narrow Tauri commands and contained sidecar IPC. Do not add a local HTTP server, generic shell access, silent downloads, absolute-path trust, or secrets in logs and metadata. Outputs are immutable; use atomic promotion and explicit lineage for edits and Story Mode artifacts.
