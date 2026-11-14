"""Speed-first switches and deterministic DDL lowering."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from infrastructure.shared.agent.budgets import (clamp_attempts, clamp_tests,
    clamp_turns, fast_mode, skip_generated_tests, skip_independent_acceptance,
    ultra_fast_mode)
from infrastructure.shared.agent.loop import AgentExhausted, run_file_agent
from infrastructure.shared.agent.tools import FileTools
from infrastructure.pipeline.ultra_fast import _valid_files, should_ultra_fast
from infrastructure.stages.database.prebuilt import render_table_sql


class FastModeFlags(unittest.TestCase):
    def test_local_runs_default_to_quality(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(fast_mode())

    def test_runner_environment_defaults_to_fast(self):
        with patch.dict(os.environ, {"ARCBENCH_OUTPUT_DIR": "/workspace/template"}, clear=True):
            self.assertTrue(fast_mode())

    def test_explicit_quality_overrides_runner_environment(self):
        with patch.dict(os.environ, {"ARCBENCH_OUTPUT_DIR": "/workspace/template",
                "FAST_MODE": "0"}, clear=True):
            self.assertFalse(fast_mode())

    def test_clamps_are_configurable(self):
        with patch.dict(os.environ, {"FAST_MODE": "1", "FAST_MAX_TURNS": "2",
                "FAST_MAX_ATTEMPTS": "2", "FAST_MAX_TESTS": "1"}, clear=True):
            self.assertEqual(clamp_turns(10), 2)
            self.assertEqual(clamp_attempts(10), 2)
            self.assertEqual(clamp_tests(5), 1)
            self.assertTrue(skip_generated_tests())
            self.assertTrue(skip_independent_acceptance())

    def test_quality_mode_does_not_change_budgets(self):
        with patch.dict(os.environ, {"FAST_MODE": "0"}, clear=True):
            self.assertEqual(clamp_turns(10), 10)
            self.assertEqual(clamp_attempts(10), 10)
            self.assertEqual(clamp_tests(5), 5)
            self.assertFalse(skip_generated_tests())

    def test_ultra_fast_is_limited_to_small_tasks_unless_forced(self):
        with patch.dict(os.environ, {"FAST_MODE": "1"}, clear=True):
            self.assertTrue(ultra_fast_mode(6))
            self.assertTrue(ultra_fast_mode(60))
            self.assertFalse(ultra_fast_mode(61))
        with patch.dict(os.environ, {"FAST_MODE": "1", "ULTRA_FAST": "1"}, clear=True):
            self.assertTrue(ultra_fast_mode(31))
        with patch.dict(os.environ, {"FAST_MODE": "1", "ULTRA_FAST": "0"}, clear=True):
            self.assertFalse(ultra_fast_mode(6))

    def test_one_shot_file_map_requires_app_and_filters_owned_files(self):
        files = _valid_files({"files": {
            "frontend/src/App.tsx": "export function App() { return null; }",
            "frontend/src/pages/Home.tsx": "export function Home() { return null; }",
            "frontend/src/main.tsx": "ignored",
            "backend/src/server.ts": "ignored",
        }})
        self.assertEqual(set(files), {"frontend/src/App.tsx", "frontend/src/pages/Home.tsx"})
        with self.assertRaises(ValueError):
            _valid_files({"files": {"frontend/src/pages/Home.tsx": "x"}})


class FastAgentTurnLimit(unittest.TestCase):
    def test_edit_rounds_are_clamped(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "page.tsx").write_text("export const Page = () => null;\n", encoding="utf-8")
        files = FileTools(root, ["page.tsx"])

        def generate(instructions, payload):
            return {"tool_calls": [{"tool": "edit_file", "path": "page.tsx",
                "operation": "replace", "old_content": "() => null;",
                "new_content": "() => null; // attempt"}]}

        messages = iter(["first failure", "second failure", "third failure", "fourth failure"])
        with patch.dict(os.environ, {"FAST_MODE": "1", "FAST_MAX_TURNS": "3"}, clear=True), \
                self.assertRaises(AgentExhausted) as raised:
            run_file_agent(Mock(generate_json=Mock(side_effect=generate)), "", {},
                files, lambda: (_ for _ in ()).throw(ValueError(next(messages))),
                10, Mock(), "Fast example")
        self.assertEqual(raised.exception.turns, 3)


class DeterministicDDL(unittest.TestCase):
    def test_renders_keys_nullability_uniqueness_reference_and_boolean(self):
        entity = {"description": "Example", "fields": {
            "id": {"type": "integer", "nullable": False, "primary_key": True,
                "unique": True, "references": None, "constraints": [], "description": "Key"},
            "owner_id": {"type": "integer", "nullable": False, "primary_key": False,
                "unique": False, "references": "Account.id", "constraints": [], "description": "Owner"},
            "title": {"type": "string", "nullable": False, "primary_key": False,
                "unique": True, "references": None, "constraints": [], "description": "Title"},
            "archived": {"type": "boolean", "nullable": True, "primary_key": False,
                "unique": False, "references": None, "constraints": [], "description": "Flag"},
        }}
        sql = render_table_sql("Note", entity)
        self.assertIn('CREATE TABLE IF NOT EXISTS "Note"', sql)
        self.assertIn('"id" INTEGER PRIMARY KEY NOT NULL', sql)
        self.assertIn('"owner_id" INTEGER NOT NULL REFERENCES "Account" ("id")', sql)
        self.assertIn('"title" TEXT NOT NULL UNIQUE', sql)
        self.assertIn('"archived" INTEGER CHECK ("archived" IN (0, 1))', sql)
        self.assertNotIn("SELECT", sql)


if __name__ == "__main__":
    unittest.main()
