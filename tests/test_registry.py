import unittest

from worker.models import IMMUTABLE_REVISION, REGISTRY, RETIRED_MODEL_IDS, resolve
from worker.providers.base import Capability


class RegistryTests(unittest.TestCase):
    def test_shareable_profile_excludes_noncommercial_flux(self):
        resolved = resolve(Capability.IMAGE_GENERATION, "shareable")
        self.assertEqual([item.model_id for item in resolved], ["flux-schnell"])

    def test_registry_pins_reviewable_model_metadata(self):
        self.assertTrue(all(IMMUTABLE_REVISION.fullmatch(spec.revision) for spec in REGISTRY.values()))
        self.assertTrue(all(spec.expected_size_gib > 0 and spec.checksum_source for spec in REGISTRY.values()))

    def test_quality_failed_wan_models_are_retired_from_the_download_registry(self):
        self.assertEqual(RETIRED_MODEL_IDS, {"wan2.1-1.3b", "wan2.1-14b", "wan2.2-ti2v-5b"})
        self.assertTrue(RETIRED_MODEL_IDS.isdisjoint(REGISTRY))

    def test_hunyuan15_has_pinned_personal_t2v_and_i2v_entries(self):
        t2v = REGISTRY["hunyuan15-480p-t2v"]
        i2v = REGISTRY["hunyuan15-480p-i2v"]
        self.assertEqual(t2v.repository, "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_t2v")
        self.assertEqual(t2v.revision, "286be7ce72277246578a3e3cc2487e95ddae5bcf")
        self.assertEqual(t2v.expected_size_gib, 53.4)
        self.assertEqual(t2v.supported_modes, frozenset({"text"}))
        self.assertEqual(i2v.repository, "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-480p_i2v")
        self.assertEqual(i2v.revision, "5a700ee883ff4c1b3d887ec4188755a7a5e2f698")
        self.assertEqual(i2v.expected_size_gib, 54.2)
        self.assertEqual(i2v.supported_modes, frozenset({"image"}))
        self.assertEqual(t2v.profile, i2v.profile, "territory-restricted models must not enter the shareable profile")
        self.assertIn("territory-restricted", t2v.license_name)
        self.assertTrue(t2v.requires_access_confirmation and i2v.requires_access_confirmation)

    def test_hunyuan15_allowlist_includes_the_mllm_chat_template(self):
        # Regression test: the allowlist previously omitted tokenizer/*.jinja,
        # so the required Qwen2.5-VL chat_template.jinja was never downloaded
        # and every generation failed in encode_prompt.
        for model_id in ("hunyuan15-480p-t2v", "hunyuan15-480p-i2v"):
            self.assertIn("tokenizer/*.jinja", REGISTRY[model_id].allowed_files)
