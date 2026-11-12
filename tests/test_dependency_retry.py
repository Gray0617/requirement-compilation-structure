"""npm install survives one registry/cache failure.

A single transient `EPERM` from a locked npm cache previously aborted stages 04, 06,
08 and 10 in the same run. These tests pin the retry and the degrade-to-existing
node_modules behaviour.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from infrastructure.shared.project import dependencies


class InstallRetry(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "package.json").write_text("{}", encoding="utf-8")

    def run_install(self, attempts):
        calls = []
        queue = list(attempts)

        def once(output_dir):
            calls.append(output_dir)
            error = queue.pop(0) if queue else None
            if error is not None:
                raise RuntimeError(error)

        with patch.object(dependencies, "_install_once", side_effect=once), \
                patch.object(dependencies, "sleep", return_value=None):
            dependencies.install_dependencies(self.root)
        return calls

    def test_a_transient_failure_is_retried_once(self):
        calls = self.run_install(["npm install failed with exit code 1", None])
        self.assertEqual(len(calls), 2)

    def test_success_needs_a_single_attempt(self):
        self.assertEqual(len(self.run_install([None])), 1)

    def test_existing_node_modules_prevents_an_abort(self):
        (self.root / "node_modules").mkdir()
        self.assertEqual(len(self.run_install(["boom", "boom"])), 2)

    def test_missing_node_modules_still_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            self.run_install(["boom", "boom"])

    def test_missing_package_json_is_still_a_configuration_error(self):
        (self.root / "package.json").unlink()
        with self.assertRaises(ValueError):
            dependencies.install_dependencies(self.root)


if __name__ == "__main__":
    unittest.main()
