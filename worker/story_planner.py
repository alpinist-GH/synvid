"""Verified local-only Qwen planner for optional Story Mode scene drafts."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable
from .model_security import ModelSecurityError, verify_tree
from .models import REGISTRY

class StoryPlannerError(RuntimeError): pass

class QwenStoryPlanner:
    def __init__(self, root: Path): self.root = root; self.model = None; self.tokenizer = None
    def draft(self, premise: str, style_bible: str, count: int) -> list[dict[str, str]]:
        if not 1 <= count <= 8: raise StoryPlannerError("requested scene count is unavailable")
        model, tokenizer = self._load()
        import torch
        instruction = (f'Return JSON only in this shape: {{"scenes":[{{"prompt":"string","narration":"string"}}]}}. '
                       f'Create exactly {count} concise scenes. Premise: {premise}. Style bible: {style_bible or "none"}. No markdown.')
        text = tokenizer.apply_chat_template([{"role":"system","content":"You produce strict JSON."},{"role":"user","content":instruction}], tokenize=False, add_generation_prompt=True)
        inputs = tokenizer([text], return_tensors="pt").to("mps")
        generated = model.generate(**inputs, max_new_tokens=256, do_sample=False)
        raw = tokenizer.decode(generated[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
        if raw.startswith("```json\n") and raw.endswith("\n```") and raw.count("```") == 2: raw = raw[8:-4]
        try: result = json.loads(raw)
        except json.JSONDecodeError as error: raise StoryPlannerError("planner returned invalid JSON") from error
        scenes = result.get("scenes") if isinstance(result, dict) else None
        if not isinstance(scenes, list) or len(scenes) != count or any(not isinstance(scene, dict) or not isinstance(scene.get("prompt"), str) or not isinstance(scene.get("narration"), str) or not scene["prompt"].strip() or not scene["narration"].strip() or len(scene["prompt"]) > 4000 or len(scene["narration"]) > 4000 for scene in scenes): raise StoryPlannerError("planner JSON does not match the scene schema")
        return [{"prompt": scene["prompt"].strip(), "narration": scene["narration"].strip()} for scene in scenes]
    def _load(self):
        if self.model is not None: return self.model, self.tokenizer
        try: manifest=json.loads((self.root / "qwen-story-planner.sha256.json").read_text()); verify_tree(self.root / "snapshot", REGISTRY["qwen-story-planner"], manifest)
        except (OSError, ValueError, json.JSONDecodeError, ModelSecurityError) as error: raise StoryPlannerError("story planner is not a verified SynVid install") from error
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.tokenizer=AutoTokenizer.from_pretrained(str(self.root / "snapshot"), local_files_only=True, trust_remote_code=False)
        self.model=AutoModelForCausalLM.from_pretrained(str(self.root / "snapshot"), dtype=torch.bfloat16, local_files_only=True, trust_remote_code=False).to("mps")
        return self.model, self.tokenizer

    def unload(self) -> None:
        """Release the instruction model before diffusion or TTS can load."""
        self.model = None
        self.tokenizer = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.synchronize()
                torch.mps.empty_cache()
        except (ImportError, RuntimeError):
            # An unavailable MPS runtime cannot retain this model in a usable
            # state, and cleanup must never hide the original planner result.
            pass
