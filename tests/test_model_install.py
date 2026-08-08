import hashlib
import tempfile
import unittest
from pathlib import Path

from worker.model_install import ModelInstallError, RemoteFile, install_snapshot
from worker.models import REGISTRY
from worker.paths import AppPaths


class ModelInstallTests(unittest.TestCase):
    def test_promotes_only_a_checksum_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = AppPaths.under(Path(temp))
            content = b"reviewed model index"

            def download(staging):
                (staging / ".cache").mkdir()
                (staging / "model_index.json").write_bytes(content)

            result = install_snapshot(
                paths,
                REGISTRY["flux-schnell"],
                [RemoteFile("model_index.json", hashlib.sha256(content).hexdigest())],
                download,
            )

            snapshot = paths.models / "flux-schnell" / "snapshot"
            self.assertEqual(result["files"], 1)
            self.assertEqual(result["installed_bytes"], len(content))
            self.assertEqual((snapshot / "model_index.json").read_bytes(), content)
            self.assertTrue((paths.models / "flux-schnell" / "flux-schnell.sha256.json").is_file())
            self.assertFalse((snapshot / ".cache").exists())

    def test_never_replaces_an_existing_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = AppPaths.under(Path(temp))
            snapshot = paths.models / "flux-schnell" / "snapshot"
            snapshot.mkdir(parents=True)
            with self.assertRaises(ModelInstallError):
                install_snapshot(paths, REGISTRY["flux-schnell"], [], lambda _staging: None)

    def test_refuses_tampered_upstream_lfs_file_before_promotion(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = AppPaths.under(Path(temp))

            def download(staging):
                (staging / "model_index.json").write_text("tampered")

            with self.assertRaisesRegex(Exception, "checksum mismatch"):
                install_snapshot(
                    paths,
                    REGISTRY["flux-schnell"],
                    [RemoteFile("model_index.json", hashlib.sha256(b"reviewed").hexdigest())],
                    download,
                )
            self.assertFalse((paths.models / "flux-schnell" / "snapshot").exists())
