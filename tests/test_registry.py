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
