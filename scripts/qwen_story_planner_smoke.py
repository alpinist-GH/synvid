#!/usr/bin/env python3
"""Measure local Qwen story-planning and require strict bounded scene JSON."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import resource
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--app-support", type=Path, required=True); args = parser.parse_args()
    root = args.app_support / "SynVid/models/qwen-story-planner"
    snapshot, manifest = root / "snapshot", root / "qwen-story-planner.sha256.json"
    if not snapshot.is_dir() or not manifest.is_file(): raise SystemExit("verified story planner is unavailable")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    if not torch.backends.mps.is_available(): raise SystemExit("MPS is unavailable")
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(str(snapshot), local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(str(snapshot), dtype=torch.bfloat16, local_files_only=True, trust_remote_code=False).to("mps")
    instruction = ('Return JSON only, with exactly this shape: {"scenes":[{"prompt":"string","narration":"string"}]}. '
                   'Create exactly three short visual scenes for this premise: A lone boat reaches shore before a storm. No markdown.')
    text = tokenizer.apply_chat_template([{"role": "system", "content": "You produce strict JSON."}, {"role": "user", "content": instruction}], tokenize=False, add_generation_prompt=True)
    inputs = tokenizer([text], return_tensors="pt").to("mps")
    generated = model.generate(**inputs, max_new_tokens=256, do_sample=False)
    response = tokenizer.decode(generated[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
    if response.startswith("```json\n") and response.endswith("\n```") and response.count("```") == 2:
        response = response[8:-4]
    try:
        result = json.loads(response)
    except json.JSONDecodeError as error:
        raise SystemExit(f"planner did not return JSON: {response[:500]!r}") from error
    scenes = result.get("scenes") if isinstance(result, dict) else None
    if not isinstance(scenes, list) or len(scenes) != 3 or any(not isinstance(scene, dict) or not isinstance(scene.get("prompt"), str) or not isinstance(scene.get("narration"), str) or not scene["prompt"].strip() or not scene["narration"].strip() or len(scene["prompt"]) > 4_000 or len(scene["narration"]) > 4_000 for scene in scenes):
        raise SystemExit("planner JSON does not match the scene schema")
    raw_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    profile = {"dtype": "bfloat16", "max_new_tokens": 256, "scene_count": 3, "wall_seconds": time.monotonic() - started, "peak_mps_allocated_bytes": int(torch.mps.current_allocated_memory()), "peak_rss_bytes": raw_rss if sys.platform == "darwin" else raw_rss * 1024}
    target = root / "measured-profile.json"; temporary = target.with_suffix(".json.partial"); temporary.write_text(json.dumps(profile, sort_keys=True, indent=2)); temporary.replace(target)
    print(json.dumps({"profile": profile, "scenes": scenes}, sort_keys=True))
    model = None; gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache()
    return 0


if __name__ == "__main__": raise SystemExit(main())
