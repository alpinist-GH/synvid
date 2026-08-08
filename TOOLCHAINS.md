# SynVid build toolchains

Stage 0 development baseline, recorded on 2026-08-07:

- Apple Silicon (`arm64`) macOS 14.0 or later deployment target.
- Python 3.11.15, recreated from `requirements.lock`; the development venv is
  never embedded in an app bundle.
- Node.js 26.5.0 and npm 11.17.0.
- Rust 1.97.0 with Cargo 1.97.0.
- Tauri 2 dependencies are locked in `app/package-lock.json` and
  `app/src-tauri/Cargo.lock` after the first dependency resolution.
- PyInstaller 6.18.0 in the worker build environment.

The minimum macOS target is intentionally conservative: it is a distribution
target, not evidence that every model runs on every supported Apple GPU. Model
support remains subject to the Stage 1 MPS gate.
