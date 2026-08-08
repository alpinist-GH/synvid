#!/usr/bin/env python3
"""Run and record one controlled FLUX.1-schnell MPS feasibility render.

The checkpoint must already be an explicitly authorized SynVid snapshot.  A
measured profile is written only after Pillow can reopen the emitted PNG, so a
failed candidate never becomes selectable by the worker.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from worker.paths import AppPaths
from worker.providers.base import Capability, OperationRequest
from worker.providers.flux import FluxSchnellProvider


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-support", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt", default="A yellow flower in a glass vase, soft morning light")
    parser.add_argument("--dtype", choices=("float16", "bfloat16"), required=True)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--guidance-scale", type=float, default=0.0)
    parser.add_argument("--max-sequence-length", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if min(args.width, args.height, args.steps, args.max_sequence_length) <= 0 or args.guidance_scale < 0:
        raise ValueError("dimensions, steps, sequence length, and guidance must be valid")

    paths = AppPaths.under(args.app_support)
    paths.create()
    model_dir = paths.models / "flux-schnell"
    snapshot = model_dir / "snapshot"
    if args.output_dir.exists():
        raise ValueError("output directory must not already exist")

    profile = {
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "guidance_scale": args.guidance_scale,
        "max_sequence_length": args.max_sequence_length,
        "dtype": args.dtype,
        "estimated_disk_bytes": _tree_size(snapshot),
        "peak_rss_bytes": 1,
        "peak_mps_allocated_bytes": 0,
    }
    candidate = paths.temporary / f"flux-profile-{uuid.uuid4()}.json"
    candidate.write_text(json.dumps(profile, sort_keys=True))
    args.output_dir.mkdir(parents=True)
    started = time.monotonic()
    provider = FluxSchnellProvider(snapshot, candidate)
    import torch

    peak_mps_allocated_bytes = 0

    def progress(fraction: float, text: str) -> None:
        nonlocal peak_mps_allocated_bytes
        if torch.backends.mps.is_available():
            peak_mps_allocated_bytes = max(
                peak_mps_allocated_bytes,
                torch.mps.current_allocated_memory(),
            )
        print(f"{fraction:.0%} {text}")

    result = provider.run(
        OperationRequest(
            operation_id="flux-smoke",
            capability=Capability.IMAGE_GENERATION,
            prompt=args.prompt,
            output_dir=args.output_dir,
            seed=args.seed,
            width=args.width,
            height=args.height,
            frames=1,
            fps=1,
            steps=args.steps,
            guidance_scale=args.guidance_scale,
        ),
        progress,
        lambda: False,
    )
    image_path = args.output_dir / result["media_file"]
    with Image.open(image_path) as image:
        image.verify()
    with Image.open(image_path) as image:
        facts = {"format": image.format, "width": image.width, "height": image.height, "mode": image.mode}
    profile["peak_rss_bytes"] = _peak_rss_bytes()
    profile["peak_mps_allocated_bytes"] = peak_mps_allocated_bytes
    evidence = {
        "wall_seconds": round(time.monotonic() - started, 3),
        "image": str(image_path),
        "image_bytes": image_path.stat().st_size,
        "image_facts": facts,
        "profile": profile,
        "prompt": args.prompt,
        "seed": args.seed,
    }
    (args.output_dir / "evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2))
    (model_dir / "measured-profile.json").write_text(json.dumps(profile, sort_keys=True, indent=2))
    candidate.unlink(missing_ok=True)
    provider.unload()
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
