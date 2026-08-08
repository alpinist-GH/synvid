#!/usr/bin/env python3
"""Run the Stage 6 Qwen Image Edit MPS gate and write a profile only on success."""

from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import resource
import sys
import time

from PIL import Image, ImageChops


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-support", type=Path, required=True)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--steps", type=int, default=4)
    args = parser.parse_args()
    if args.width <= 0 or args.height <= 0 or args.steps <= 0:
        raise SystemExit("width, height, and steps must be positive")

    model_root = args.app_support / "SynVid/models/qwen-image-edit/snapshot"
    manifest = args.app_support / "SynVid/models/qwen-image-edit/qwen-image-edit.sha256.json"
    if not model_root.is_dir() or not manifest.is_file():
        raise SystemExit("verified Qwen Image Edit installation is unavailable")

    import torch
    from diffusers import QwenImageEditPipeline

    if not torch.backends.mps.is_available():
        raise SystemExit("MPS is unavailable")
    source = Image.new("RGB", (args.width, args.height), "#2d6a9f")
    # A strong, unambiguous instruction supports direct visual inspection of
    # the result while avoiding any copyrighted or personal source material.
    prompt = "Change the plain blue square into a plain bright orange square, preserving the square composition."
    started = time.monotonic()
    pipeline = QwenImageEditPipeline.from_pretrained(
        str(model_root), torch_dtype=torch.bfloat16, local_files_only=True,
        trust_remote_code=False,
    ).to("mps")
    pipeline.set_progress_bar_config(disable=True)
    image = pipeline(
        image=source, prompt=prompt, width=args.width, height=args.height,
        num_inference_steps=args.steps, guidance_scale=1.0, true_cfg_scale=1.0,
        max_sequence_length=512, generator=torch.Generator(device="cpu").manual_seed(42),
    ).images[0]
    elapsed = time.monotonic() - started
    output = args.app_support / "SynVid/temporary/qwen-image-edit-smoke.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    inspected = Image.open(output).convert("RGB")
    if inspected.size != (args.width, args.height) or inspected.getbbox() is None:
        raise SystemExit("Qwen Image Edit produced an invalid image")
    # Ensure the pipeline did not simply return our flat source unchanged.
    if ImageChops.difference(source, inspected).getbbox() is None:
        raise SystemExit("Qwen Image Edit returned the source unchanged")
    peak_mps = int(torch.mps.current_allocated_memory())
    # macOS reports ru_maxrss in bytes, while Linux reports KiB.
    raw_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    peak_rss = raw_rss if sys.platform == "darwin" else raw_rss * 1024
    profile = {
        "width": args.width, "height": args.height, "steps": args.steps,
        "guidance_scale": 1.0, "true_cfg_scale": 1.0, "max_sequence_length": 512,
        "dtype": "bfloat16", "estimated_disk_bytes": sum(item.stat().st_size for item in model_root.rglob("*") if item.is_file()),
        "peak_rss_bytes": peak_rss, "peak_mps_allocated_bytes": peak_mps,
        "wall_seconds": elapsed,
    }
    destination = args.app_support / "SynVid/models/qwen-image-edit/measured-profile.json"
    temporary = destination.with_suffix(".json.partial")
    temporary.write_text(json.dumps(profile, sort_keys=True, indent=2))
    temporary.replace(destination)
    print(json.dumps({"output": str(output), "profile": profile}, sort_keys=True))
    pipeline = None
    gc.collect()
    torch.mps.synchronize()
    torch.mps.empty_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
