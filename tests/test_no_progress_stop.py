"""A repair that repeats the same failure must stop instead of burning the budget.

A real ARC-Bench run stayed on one TypeScript error for eight consecutive rounds
(`TS2345: 'string | null' is not assignable ...`) and ran for two hours. These tests
pin the fingerprint-based early stop and the surviving legitimate retries.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from infrastructure.shared.agent.loop import (AgentExhausted, failure_fingerprint,
    run_file_agent)
from infrastructure.shared.agent.tools import FileTools


def workspace(root):
    (root / "page.tsx").write_text("export const Page = () => null;\n", encoding="utf-8")
    return FileTools(root, ["page.tsx"])


def model_that_edits(root):
    """Always returns the same no-op edit so the gate keeps failing identically."""

    def generate(instructions, payload):
        return {"tool_calls": [{"tool": "edit_file", "path": "page.tsx", "operation": "replace",
            "old_content": "() => null;", "new_content": "() => null; // attempt"}]}

    return Mock(generate_json=Mock(side_effect=generate))


def failing_gate(message):
    def gate():
        raise ValueError(message)
    return gate


class Fingerprint(unittest.TestCase):
    def test_volatile_timings_do_not_change_the_fingerprint(self):
        first = failure_fingerprint({"error": "Expect failed after 4000ms\n  at line 3"})
        second = failure_fingerprint({"error": "Expect failed after 10000ms\n\n  at line 3"})
        self.assertEqual(first, second)

    def test_ansi_colour_codes_are_ignored(self):
        first = failure_fingerprint({"error": "\x1b[31mTS2345\x1b[39m bad"})
        self.assertEqual(first, failure_fingerprint({"error": "TS2345 bad"}))

    def test_different_failures_stay_distinct(self):
        self.assertNotEqual(failure_fingerprint({"error": "TS2345"}),
            failure_fingerprint({"error": "TS2451"}))


class EarlyStop(unittest.TestCase):
    def run_agent(self, gate, max_turns=10):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        return run_file_agent(model_that_edits(root), "", {}, workspace(root), gate,
            max_turns, Mock(), "Example")

    def test_identical_failures_stop_after_two_rounds(self):
        with self.assertRaises(AgentExhausted) as raised:
            self.run_agent(failing_gate("TS2345: 'string | null' is not assignable"))
        self.assertEqual(raised.exception.turns, 2)
        self.assertIn("no progress", str(raised.exception))

    def test_changing_failures_keep_using_the_round_budget(self):
        messages = iter(["first failure", "second failure", "third failure"])

        def gate():
            raise ValueError(next(messages))

        with self.assertRaises(AgentExhausted) as raised:
            self.run_agent(gate, max_turns=3)
        self.assertEqual(raised.exception.turns, 3)
        self.assertNotIn("no progress", str(raised.exception))

    def test_success_first_time_needs_no_extra_round(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        result = run_file_agent(model_that_edits(root), "", {}, workspace(root),
            lambda: {"ok": True}, 3, Mock(), "Example")
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["checks"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
