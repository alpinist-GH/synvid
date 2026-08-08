#!/usr/bin/env python3
"""Run the Stage 1 real-LTX MPS feasibility gate without downloading models.

The checkpoint must already be an explicitly authorized, verified SynVid model
install.  The script produces one immutable output, inspects it with ffprobe,
and records the sole profile that Stage 2 may expose.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
import resource
from pathlib import Path

from worker.paths import AppPaths
from worker.providers.ltx import LtxProvider
from worker.providers.base import Capability, OperationRequest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-support", type=Path, required=True)
    parser.add_argument("--prompt", default="A calm cinematic shot of waves on a dark shore")
    parser.add_argument("--dtype", choices=("float16", "bfloat16"), required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    parser.add_argument("--steps", type=int, required=True)
    parser.add_argument("--guidance-scale", type=float, required=True)
    parser.add_argument("--estimated-disk-bytes", type=int, required=True)
    parser.add_argument("--recipe", choices=("Draft", "Balanced", "High"), default="Balanced")
    parser.add_argument("--source-image", type=Path)
    args = parser.parse_args()
    paths = AppPaths.under(args.app_support)
    paths.create()
    model_dir = paths.models / "ltx-video"
    profile_path = model_dir / "measured-profile.json"
    profile = {
        "width": args.width, "height": args.height, "frames": args.frames, "fps": args.fps,
        "steps": args.steps, "guidance_scale": args.guidance_scale, "dtype": args.dtype,
        "estimated_disk_bytes": args.estimated_disk_bytes, "peak_rss_bytes": 0,
    }
    candidate_profile = paths.temporary / f"stage1-profile-{uuid.uuid4()}.json"
    # Keep every independently measured recipe.  A candidate is exercised
    # before this durable map is updated, so a failed run never becomes UI
    # selectable merely because it was requested.
    candidate_profile.write_text(json.dumps(profile, sort_keys=True))
    provider = LtxProvider(model_dir / "snapshot", candidate_profile)
    output_dir = paths.temporary / f"stage1-smoke-{uuid.uuid4()}"
    output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    result = provider.run(OperationRequest(
        operation_id="stage1-smoke", capability=Capability.VIDEO_GENERATION, prompt=args.prompt,
        output_dir=output_dir, seed=0, width=args.width, height=args.height, frames=args.frames,
        fps=args.fps, steps=args.steps, guidance_scale=args.guidance_scale, source_image=args.source_image,
    ), lambda fraction, text: print(f"{fraction:.0%} {text}"), lambda: False)
    video = output_dir / result["media_file"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)], text=True, capture_output=True, check=True)
    streams = json.loads(probe.stdout)["streams"]
    video_stream = next(stream for stream in streams if stream.get("codec_type") == "video")
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    profile["peak_rss_bytes"] = max_rss if sys.platform == "darwin" else max_rss * 1024
    evidence = {"wall_seconds": round(time.monotonic() - started, 3), "video": str(video), "profile": profile, "ffprobe": video_stream}
    (output_dir / "evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=2))
    try:
        existing = json.loads(profile_path.read_text())
        recipes = existing.get("recipes", {"Balanced": existing})
        if not isinstance(recipes, dict):
            recipes = {}
    except (OSError, ValueError, json.JSONDecodeError):
        recipes = {}
    recipes[args.recipe] = profile
    profile_path.write_text(json.dumps({"schema_version": 2, "recipes": recipes}, sort_keys=True))
    print(json.dumps({**evidence, "recipe": args.recipe}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
