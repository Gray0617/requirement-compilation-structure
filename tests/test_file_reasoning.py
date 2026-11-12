"""Edit rounds must not pay for hidden reasoning they never use.

Measured runs spent 95% of every output token on thinking inside mechanical edit
rounds (9,400 reasoning tokens per call). The stage planner keeps its reasoning;
the file agent switches it off unless MODEL_REASONING_FILES says otherwise.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from infrastructure.shared.agent.loop import run_file_agent
from infrastructure.shared.agent.tools import FileTools
from infrastructure.shared.models.text import DEFAULT_FILE_REASONING, TextModel


class FileReasoning(unittest.TestCase):
    def test_edit_agent_prefers_the_no_thinking_transport(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "page.tsx").write_text("export const Page = () => null;\n", encoding="utf-8")
        files = FileTools(root, ["page.tsx"])

        class Model:
            def __init__(self):
                self.used = None

            def generate_file_json(self, instructions, payload):
                self.used = "file"
                return {"tool_calls": [{"tool": "edit_file", "path": "page.tsx",
                    "operation": "replace", "old_content": "() => null;",
                    "new_content": "() => null; // edited"}]}

            def generate_json(self, instructions, payload):
                self.used = "stage"
                raise AssertionError("the edit loop must use the file transport")

        model = Model()
        result = run_file_agent(model, "", {}, files, lambda: {"ok": True}, 2,
            _Quiet(), "Example")
        self.assertEqual(model.used, "file")
        self.assertEqual(result["turns"], 1)

    def test_a_model_without_the_file_transport_still_works(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "page.tsx").write_text("export const Page = () => null;\n", encoding="utf-8")
        files = FileTools(root, ["page.tsx"])

        class Model:
            def generate_json(self, instructions, payload):
                return {"tool_calls": [{"tool": "edit_file", "path": "page.tsx",
                    "operation": "replace", "old_content": "() => null;",
                    "new_content": "() => null; // edited"}]}

        result = run_file_agent(Model(), "", {}, files, lambda: {"ok": True}, 2,
            _Quiet(), "Example")
        self.assertEqual(result["turns"], 1)


class FileReasoningMode(unittest.TestCase):
    def test_the_default_skips_thinking_for_edit_rounds(self):
        # Measured: thinking produced 95% of output tokens in edit rounds without a
        # matching accuracy gain. Planning stages keep their own reasoning.
        self.assertEqual(DEFAULT_FILE_REASONING, "none")

    def model(self, mode):
        # Bypass __init__: these tests only exercise the reasoning hand-off.
        model = TextModel.__new__(TextModel)
        model.file_reasoning = mode
        model.calls = []

        def generate_json(instructions, payload, reasoning=None):
            model.calls.append(reasoning)
            return {"ok": True}

        model.generate_json = generate_json
        return model

    def test_file_transport_forwards_the_configured_mode(self):
        model = self.model("none")
        self.assertEqual(model.generate_file_json("instructions", {}), {"ok": True})
        self.assertEqual(model.calls, ["none"])

    def test_the_stage_transport_keeps_its_own_mode(self):
        model = self.model("none")
        model.reasoning = "low"
        self.assertEqual(model.generate_json("instructions", {}), {"ok": True})
        self.assertEqual(model.calls, [None])


class _Quiet:
    def show(self, *arguments):
        """Swallow console output during tests."""


if __name__ == "__main__":
    unittest.main()
