import hashlib
import tempfile
import unittest
from pathlib import Path

from worker.model_security import ModelSecurityError, validate_download_request, verify_tree
from worker.models import REGISTRY


class ModelSecurityTests(unittest.TestCase):
    def setUp(self):
        self.spec = REGISTRY["flux-schnell"]

    def test_rejects_mutable_revision_remote_code_and_wrong_repository(self):
        with self.assertRaises(ModelSecurityError):
            validate_download_request(self.spec, self.spec.repository, "main", False)
        with self.assertRaises(ModelSecurityError):
            validate_download_request(self.spec, self.spec.repository, self.spec.revision, True)
        with self.assertRaises(ModelSecurityError):
            validate_download_request(self.spec, "https://example.invalid/model", self.spec.revision, False)

    def test_rejects_pickle_unexpected_files_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "model_index.json"
            allowed.write_bytes(b"reviewed")
            manifest = {"model_index.json": hashlib.sha256(b"reviewed").hexdigest()}
            verify_tree(root, self.spec, manifest)
            (root / "weights.pkl").write_bytes(b"unsafe")
            with self.assertRaisesRegex(ModelSecurityError, "unsafe model serialization"):
                verify_tree(root, self.spec, manifest)
            (root / "weights.pkl").unlink()
            (root / "README.md").write_text("unreviewed")
            with self.assertRaisesRegex(ModelSecurityError, "unexpected model file"):
                verify_tree(root, self.spec, manifest)
            (root / "README.md").unlink()
            allowed.write_bytes(b"tampered")
            with self.assertRaisesRegex(ModelSecurityError, "checksum mismatch"):
                verify_tree(root, self.spec, manifest)
