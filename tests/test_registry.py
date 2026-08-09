import unittest

from worker.models import IMMUTABLE_REVISION, REGISTRY, resolve
from worker.providers.base import Capability


class RegistryTests(unittest.TestCase):
    def test_shareable_profile_excludes_noncommercial_flux(self):
        resolved = resolve(Capability.IMAGE_GENERATION, "shareable")
        self.assertEqual([item.model_id for item in resolved], ["flux-schnell"])

    def test_registry_pins_reviewable_model_metadata(self):
        self.assertTrue(all(IMMUTABLE_REVISION.fullmatch(spec.revision) for spec in REGISTRY.values()))
        self.assertTrue(all(spec.expected_size_gib > 0 and spec.checksum_source for spec in REGISTRY.values()))

    def test_wan22_tiiv_5b_is_an_explicit_experimental_video_option(self):
        spec = REGISTRY["wan2.2-ti2v-5b"]
        self.assertIn(Capability.VIDEO_GENERATION, spec.capabilities)
        self.assertEqual(spec.repository, "Wan-AI/Wan2.2-TI2V-5B-Diffusers")
        self.assertFalse(spec.requires_access_confirmation)
        self.assertIn("MPS", spec.reason)
