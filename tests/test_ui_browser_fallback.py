"""Guard the browser-less runner fallback for stage 08.

A locked-down runner may refuse the Chromium download or launch. Aborting stage 08
there would leave every page unimplemented, so the gate degrades to TypeScript-only
acceptance and the report records why.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from infrastructure.stages.frontend.ui import implementation


def design():
    return {"pages": {"home": {"name": "Home", "route": "/", "root": "PAGE", "images": []}},
        "elements": {"PAGE": {"type": "page", "parent": None, "data": [], "mode": [], "prop": {}},
            "GREETING": {"type": "heading", "parent": "PAGE", "data": [], "mode": [],
                "prop": {"text": "Welcome"}}},
        "requirements": {"REQ-1": ["PAGE", "GREETING"]}, "steps": {}}


class BrowserFallback(unittest.TestCase):
    def run_stage(self, browser_error):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        arc = root / ".arc"
        arc.mkdir()
        checks = Mock()
        checks.typecheck.return_value = {"returncode": 0, "output": "compiled"}
        checks.build.return_value = {"returncode": 0, "output": "built"}
        checks.tests.return_value = {"returncode": 0, "passed": 1, "failed": 0, "output": "passed"}
        if browser_error is None:
            checks.install_browser.return_value = None
        else:
            checks.install_browser.side_effect = browser_error
        report = {"status": "RUNNING", "pages": {}}
        accepted = {"turns": 1, "checks": {"typecheck": {"returncode": 0}, "tests": None}}

        with patch.object(implementation, "prepare_frontend"), \
                patch.object(implementation, "install_dependencies"), \
                patch.object(implementation, "FrontendChecks", return_value=checks), \
                patch.object(implementation, "run_file_agent", return_value=accepted):
            implementation.implement_ui(arc, design(), Mock(), Mock(), report, max_turns=1)
        return checks, report

    def test_browser_failure_degrades_instead_of_aborting(self):
        checks, report = self.run_stage(ValueError("Playwright Chromium installation failed"))
        self.assertEqual(report["browser"], "unavailable")
        self.assertEqual(report["pages"]["home"]["status"], "GREEN")
        self.assertEqual(checks.tests.call_count, 0)
        self.assertGreaterEqual(checks.typecheck.call_count, 1)
        self.assertEqual(report["status"], "GREEN")

    def test_browser_present_keeps_the_full_gate(self):
        checks, report = self.run_stage(None)
        self.assertEqual(report["browser"], "available")
        self.assertGreaterEqual(checks.tests.call_count, 1)

    def test_report_records_the_unavailable_reason(self):
        _, report = self.run_stage(OSError("cannot start browser"))
        self.assertEqual(report["status"], "GREEN")
        self.assertIn("browser", report)


if __name__ == "__main__":
    unittest.main()
