#!/usr/bin/env python3
"""Generate and measure one controlled Wan candidate on the local MPS device."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worker.model_security import verify_tree
from worker.models import REGISTRY


def _peak_rss_bytes() -> int:
    # macOS reports ru_maxrss in bytes; Linux reports KiB. SynVid's v1 target
    # is macOS, but retain a portable conversion for host-only test runs.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-support", type=Path, required=True)
    parser.add_argument("--model", choices=("wan2.1-1.3b", "wan2.1-14b"), default="wan2.1-1.3b")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--frames", type=int, default=17)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--guidance-scale", type=float, default=5.0)
    parser.add_argument("--dtype", choices=("float16", "bfloat16"), default="bfloat16")
    parser.add_argument("--strategy", choices=("direct", "sliced"), default="direct")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prompt", default="A yellow flower gently moving in a spring breeze")
    args = parser.parse_args()
    if min(args.width, args.height, args.frames, args.fps, args.steps) <= 0 or args.guidance_scale < 0:
        raise ValueError("dimensions, frame count, FPS, steps, and guidance must be valid")

    model_root = args.app_support / "SynVid" / "models" / args.model / "snapshot"
    manifest_path = model_root.parent / f"{args.model}.sha256.json"
    manifest = json.loads(manifest_path.read_text())
    verify_tree(model_root, REGISTRY[args.model], manifest)

    import torch
    from diffusers import WanPipeline
    from diffusers.utils import export_to_video

    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16}[args.dtype]
    peak_mps_allocated_bytes = 0

    def on_step_end(_pipeline, _step, _timestep, callback_kwargs):
        nonlocal peak_mps_allocated_bytes
        if torch.backends.mps.is_available():
            peak_mps_allocated_bytes = max(
                peak_mps_allocated_bytes,
                torch.mps.current_allocated_memory(),
            )
        return callback_kwargs

    pipeline = WanPipeline.from_pretrained(
        str(model_root), torch_dtype=dtype, local_files_only=True, trust_remote_code=False,
        low_cpu_mem_usage=True,
    )
    if args.strategy == "sliced":
        pipeline.enable_attention_slicing()
        pipeline.vae.enable_slicing()
        pipeline.vae.enable_tiling()
    pipeline.to("mps")
    pipeline.set_progress_bar_config(disable=True)
    result = pipeline(
        prompt=args.prompt,
        width=args.width,
        height=args.height,
        num_frames=args.frames,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        generator=torch.Generator(device="cpu").manual_seed(args.seed),
        callback_on_step_end=on_step_end,
    )
    video = args.output_dir / "video.mp4"
    export_to_video(result.frames[0], str(video), fps=args.fps)
    elapsed_seconds = time.monotonic() - started
    ffprobe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames,duration",
            "-of", "json", str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    profile = {
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "fps": args.fps,
        "steps": args.steps,
        "guidance_scale": args.guidance_scale,
        "dtype": args.dtype,
        "strategy": args.strategy,
        "model": args.model,
        "estimated_disk_bytes": video.stat().st_size,
        "peak_rss_bytes": _peak_rss_bytes(),
        "peak_mps_allocated_bytes": peak_mps_allocated_bytes,
        "wall_time_seconds": elapsed_seconds,
        "ffprobe": json.loads(ffprobe.stdout),
        "prompt": args.prompt,
        "seed": args.seed,
    }
    (args.output_dir / "measurement.json").write_text(json.dumps(profile, indent=2, sort_keys=True))
    print(json.dumps(profile, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
