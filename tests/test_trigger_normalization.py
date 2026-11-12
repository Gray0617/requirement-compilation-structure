"""Near-miss trigger records are reshaped instead of failing a requirement.

A real ARC-Bench run lost two requirements in stage 09 with
`list indices must be integers or slices, not str`, because the proposal carried a
list-shaped record where an object was documented. These tests pin the tolerant
normalization and the clear repair message for genuinely unusable input.
"""

import unittest

from infrastructure.stages.frontend.triggers.design import (api_ids, apply_decomposition,
    empty_triggers, normalize_proposal)


def tree():
    class Tree:
        requirements = {"ROOT": None, "REQ-1": None}
        steps = {"STEP-1": None}
    return Tree()


class Normalization(unittest.TestCase):
    def test_list_shaped_triggers_become_ids(self):
        proposal = {"triggers": [{"id": "TR-1", "source": "UI-1", "action": []}],
            "requirement_triggers": ["TR-1"], "step_triggers": {}}
        result = normalize_proposal(proposal)
        self.assertEqual(list(result["triggers"]), ["TR-1"])
        self.assertNotIn("id", result["triggers"]["TR-1"])

    def test_single_action_object_becomes_a_list(self):
        proposal = {"triggers": {"TR-1": {"action": {"kind": "api", "api": "API-X"}}},
            "requirement_triggers": ["TR-1"], "step_triggers": {}}
        result = normalize_proposal(proposal)
        self.assertEqual(result["triggers"]["TR-1"]["action"][0]["api"], "API-X")

    def test_missing_action_becomes_an_empty_list(self):
        result = normalize_proposal({"triggers": {"TR-1": {"source": "UI-1"}},
            "requirement_triggers": ["TR-1"], "step_triggers": {}})
        self.assertEqual(result["triggers"]["TR-1"]["action"], [])

    def test_requirement_triggers_mapping_is_accepted(self):
        result = normalize_proposal({"triggers": {}, "requirement_triggers": {"TR-1": True},
            "step_triggers": {}})
        self.assertEqual(result["requirement_triggers"], ["TR-1"])

    def test_nested_list_actions_report_a_repairable_error(self):
        with self.assertRaises(ValueError) as raised:
            normalize_proposal({"triggers": {"TR-1": {"action": [["api", "API-X"]]}},
                "requirement_triggers": [], "step_triggers": {}})
        self.assertIn("must be an object", str(raised.exception))

    def test_non_object_triggers_are_rejected_with_a_reason(self):
        with self.assertRaises(ValueError):
            normalize_proposal({"triggers": "TR-1", "requirement_triggers": [], "step_triggers": {}})


class Decomposition(unittest.TestCase):
    def test_apply_merges_normalized_records(self):
        current = empty_triggers(tree())
        updated = apply_decomposition(current, {"triggers": [
            {"id": "TR-1", "source": "UI-1", "action": [{"kind": "api", "api": "API-X"}]}],
            "requirement_triggers": ["TR-1"], "step_triggers": {"STEP-1": ["TR-1"]}}, "REQ-1")
        self.assertEqual(updated["requirements"]["REQ-1"], ["TR-1"])
        self.assertEqual(updated["steps"]["STEP-1"], ["TR-1"])
        self.assertEqual(api_ids(updated["triggers"]["TR-1"]), {"API-X"})

    def test_api_ids_ignores_malformed_actions(self):
        self.assertEqual(api_ids({"action": "nope"}), set())
        self.assertEqual(api_ids({"action": [["api"]]}), set())
        self.assertEqual(api_ids({"action": [{"kind": "ui"}]}), set())


if __name__ == "__main__":
    unittest.main()
