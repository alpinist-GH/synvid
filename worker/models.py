"""Pinned model facts and profile-aware capability resolution.

This registry is metadata only.  It deliberately has no "download this URL"
entry point: a later explicit user confirmation is required before a downloader
is introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .providers.base import Capability


IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    display_name: str
    reason: str
    capabilities: frozenset[Capability]
    profile: str
    repository: str
    revision: str
    license_name: str
    expected_size_gib: float
    requires_access_confirmation: bool
    allowed_files: tuple[str, ...]
    checksum_source: str
    supported_modes: frozenset[str] = frozenset({"text", "image"})

    def __post_init__(self) -> None:
        if not IMMUTABLE_REVISION.fullmatch(self.revision):
            raise ValueError("model revision must be a 40-character immutable commit")
        if not self.allowed_files:
            raise ValueError("model must declare allowed files")
        if not self.supported_modes.issubset({"text", "image"}):
            raise ValueError("model modes must be text and/or image")


# Revisions and estimated cache sizes were recorded from the official Hugging
# Face model metadata on 2026-08-07.  The source exposes LFS SHA-256 values for
# weight files; the future download flow must obtain the complete file manifest
# at *this exact commit*, review it, and pass it to model_security.verify_tree.
# Allowed patterns intentionally contain only inert Diffusers data/config files.
_DIFFUSERS_FILES = (
    "model_index.json", "scheduler/*.json", "text_encoder/*.json",
    "text_encoder/*.safetensors", "text_encoder_2/*.json",
    "text_encoder_2/*.safetensors", "tokenizer/*.json", "tokenizer/*.txt",
    "tokenizer/*.model", "tokenizer/merges.txt", "tokenizer/vocab.json",
    "tokenizer_2/*.json", "tokenizer_2/*.txt", "tokenizer_2/*.model",
    "tokenizer_2/merges.txt", "tokenizer_2/vocab.json",
    "transformer/*.json", "transformer/*.safetensors",
    "vae/*.json", "vae/*.safetensors", "ae.safetensors",
)

_HF_LFS = "Hugging Face LFS SHA-256 metadata at the pinned commit"

_TRANSFORMERS_FILES = (
    "config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
    "merges.txt", "vocab.json", "model.safetensors", "LICENSE", "README.md",
)

_HUNYUAN_DIFFUSERS_FILES = (
    "model_index.json", "guider/*.json", "scheduler/*.json",
    "feature_extractor/*.json", "image_encoder/*.json", "image_encoder/*.safetensors",
    "text_encoder/*.json", "text_encoder/*.safetensors",
    "text_encoder_2/*.json", "text_encoder_2/*.safetensors",
    "tokenizer/*.json", "tokenizer/*.txt", "tokenizer/*.model", "tokenizer/*.jinja",
    "tokenizer/merges.txt", "tokenizer/vocab.json",
    "tokenizer_2/*.json", "tokenizer_2/*.txt", "tokenizer_2/*.model", "tokenizer_2/*.jinja",
    "tokenizer_2/merges.txt", "tokenizer_2/vocab.json",
    "transformer/*.json", "transformer/*.safetensors",
    "vae/*.json", "vae/*.safetensors",
)

_HUNYUAN_LICENSE = "Tencent Hunyuan Community License Agreement (territory-restricted)"

_WAN22_TI2V_5B_MLX_LICENSE = (
    "Apache-2.0 (Wan-AI/Wan2.2-TI2V-5B weights); MLX conversion/generation code is a "
    "vendored, patched subset of Blaizzy/mlx-video (MIT) — see worker/vendor/mlx_video_wan2/NOTICE.md"
)

# Unlike every other entry, these are not the upstream repository's own file
# names: they are produced by a local, pinned MLX conversion
# (worker/vendor/mlx_video_wan2) of the upstream .safetensors/.pth weights.
# The raw .pth files are fetched to an ephemeral, non-app-owned location,
# LFS-checksum-verified, converted, and discarded — they are never written
# below SynVid's model root, since model_security.py forbids pickle-based
# serialization (.pth/.pt/.bin/.pkl) anywhere in the app-owned tree.
_WAN_MLX_CONVERTED_FILES = (
    "config.json", "model.safetensors", "t5_encoder.safetensors", "vae.safetensors",
    "tokenizer/special_tokens_map.json", "tokenizer/spiece.model",
    "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json",
)

_SYNVID_CONVERTED = (
    "SynVid-computed SHA-256 of files produced by a pinned local MLX conversion "
    "(worker/vendor/mlx_video_wan2, commit 87db56a51758fefb748a359b90a5283bb8ba4837) of "
    "upstream Hugging Face LFS-verified source weights at the pinned revision below; "
    "the source .pth files are verified against upstream LFS metadata during install "
    "and never persisted in app storage"
)

# These models failed the local watchability gate. Keep their IDs only so an
# existing SynVid-owned install can be displayed as removal-only cleanup; they
# must not be selectable or offered for a new download.
RETIRED_MODELS = {
    "wan2.1-1.3b": ("Wan 2.1 1.3B", "Retired after its local quality gate failed; remove the existing install to reclaim disk space."),
    "wan2.1-14b": ("Wan 2.1 14B", "Retired after its local quality gate failed; remove the existing install to reclaim disk space."),
    "wan2.2-ti2v-5b": ("Wan 2.2 TI2V 5B", "Retired after its local quality gate failed; remove the existing install to reclaim disk space."),
}
RETIRED_MODEL_IDS = frozenset(RETIRED_MODELS)

REGISTRY = {
    "ltx-video": ModelSpec("ltx-video", "LTX Video", "Creates local videos from text and edits completed LTX videos.", frozenset({Capability.VIDEO_GENERATION, Capability.VIDEO_EDITING}), "shareable", "Lightricks/LTX-Video", "8984fa25007f376c1a299016d0957a37a2f797bb", "LTX-Video Open Weights License", 24.0, True, _DIFFUSERS_FILES + ("ltxv-2b-0.9.8-distilled.safetensors",), _HF_LFS),
    "flux-schnell": ModelSpec("flux-schnell", "FLUX.1-schnell", "Creates still images for prompts and Story Mode storyboards.", frozenset({Capability.IMAGE_GENERATION}), "shareable", "black-forest-labs/FLUX.1-schnell", "741f7c3ce8b383c54771c7003378a50191e9efe9", "Apache-2.0", 54.0, True, _DIFFUSERS_FILES + ("flux1-schnell.safetensors",), _HF_LFS),
    "flux-dev": ModelSpec("flux-dev", "FLUX.1-dev", "Optional higher-capacity still-image model for personal research only.", frozenset({Capability.IMAGE_GENERATION}), "personal-research", "black-forest-labs/FLUX.1-dev", "3de623fc3c33e44ffbe2bad470d0f45bccf2eb21", "FLUX.1-dev non-commercial", 54.0, True, _DIFFUSERS_FILES + ("LICENSE.md", "flux1-dev.safetensors"), _HF_LFS),
    "flux-kontext-dev": ModelSpec("flux-kontext-dev", "FLUX.1-Kontext-dev", "Optional instruction-based image editing for personal research only.", frozenset({Capability.IMAGE_GENERATION}), "personal-research", "black-forest-labs/FLUX.1-Kontext-dev", "24e9dedc4ef646698dc8eb4e18ae2cec3c9fea0d", "FLUX.1-dev non-commercial", 54.0, True, _DIFFUSERS_FILES + ("LICENSE.md", "flux1-kontext-dev.safetensors"), _HF_LFS),
    # The shareable image-editing choice.  This is intentionally not exposed
    # until a measured MPS profile exists; its sizeable checkpoint must not be
    # presented as a supported feature merely because it is permissively licensed.
    "qwen-image-edit": ModelSpec("qwen-image-edit", "Qwen Image Edit", "Edits a completed image from a text instruction.", frozenset({Capability.IMAGE_EDITING}), "shareable", "Qwen/Qwen-Image-Edit", "ac7f9318f633fc4b5778c59367c8128225f1e3de", "Apache-2.0", 57.7, True, _DIFFUSERS_FILES + ("processor/*.json",), _HF_LFS),
    "qwen-story-planner": ModelSpec("qwen-story-planner", "Qwen Story Planner", "Optional local draft-scene assistant; currently unavailable because its structured-output quality gate did not pass.", frozenset(), "shareable", "Qwen/Qwen2.5-1.5B-Instruct", "989aa7980e4cf806f80c7fef2b1adb7bc71aa306", "Apache-2.0", 2.9, False, _TRANSFORMERS_FILES, _HF_LFS),
    "hunyuan15-480p-t2v": ModelSpec("hunyuan15-480p-t2v", "HunyuanVideo 1.5 480p T2V", "Experimental 8.3B text-to-video provider with a measured MPS Balanced profile on this Mac (848x480, 25 frames/24fps, 20 steps, ~34.3 GiB peak MPS allocation); longer frame counts (61+) thrash this Mac's unified memory and remain unmeasured. The Tencent license excludes the EU, UK, and South Korea.", frozenset({Capability.VIDEO_GENERATION}), "personal-research", "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_t2v", "286be7ce72277246578a3e3cc2487e95ddae5bcf", _HUNYUAN_LICENSE, 53.4, True, _HUNYUAN_DIFFUSERS_FILES, _HF_LFS, frozenset({"text"})),
    "hunyuan15-480p-i2v": ModelSpec("hunyuan15-480p-i2v", "HunyuanVideo 1.5 480p I2V", "Experimental 8.3B image-to-video provider; requires a real MPS memory, cancellation, and watchability gate. The Tencent license excludes the EU, UK, and South Korea.", frozenset({Capability.VIDEO_GENERATION}), "personal-research", "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_i2v", "5a700ee883ff4c1b3d887ec4188755a7a5e2f698", _HUNYUAN_LICENSE, 54.2, True, _HUNYUAN_DIFFUSERS_FILES, _HF_LFS, frozenset({"image"})),
    # A distinct runtime path from the retired "wan2.2-ti2v-5b" Diffusers/MPS
    # attempt (RETIRED_MODELS below), not a replacement for that entry: this
    # one runs the same upstream weights through an Apple-native MLX port
    # instead of Diffusers' WanPipeline. Validated once on this Mac
    # (1280x704/41 frames/40 steps, directly-inspected watchable output,
    # ~30 GiB peak system memory); not yet run through the same multi-prompt
    # quality gate that failed the Diffusers path. See
    # docs/measurements/wan2.2-ti2v-5b-mlx-gate-2026-08-10.md.
    "wan2.2-ti2v-5b-mlx": ModelSpec("wan2.2-ti2v-5b-mlx", "Wan 2.2 TI2V 5B", "5B text-and-image-to-video provider via an Apple-native MLX port. Balanced Landscape text-to-video is measured; alternate quality, duration, aspect, and image-to-video profiles must be measured locally before use.", frozenset({Capability.VIDEO_GENERATION}), "personal-research", "Wan-AI/Wan2.2-TI2V-5B", "921dbaf3f1674a56f47e83fb80a34bac8a8f203e", _WAN22_TI2V_5B_MLX_LICENSE, 23.0, True, _WAN_MLX_CONVERTED_FILES, _SYNVID_CONVERTED, frozenset({"text", "image"})),
}


def resolve(capability: Capability, profile: str) -> tuple[ModelSpec, ...]:
    return tuple(spec for spec in REGISTRY.values() if capability in spec.capabilities and spec.profile == profile)
