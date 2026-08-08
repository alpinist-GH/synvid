import tempfile
import unittest
from pathlib import Path

from worker.paths import AppPaths


class PathTests(unittest.TestCase):
    def test_app_paths_are_relative_to_application_support(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = AppPaths.under(Path(temp))
            paths.create()
            self.assertEqual(paths.root, Path(temp) / "SynVid")
            self.assertTrue(paths.outputs.is_dir())
            self.assertTrue(paths.temporary.is_dir())
