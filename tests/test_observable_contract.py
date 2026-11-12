"""Explicit requirement UI wording becomes a deterministic design gate.

Hidden acceptance suites locate controls by accessible role and exact name, so a
paraphrased caption fails while every local check stays green. These tests pin
the extractor to explicit wording only and pin the bounded repair behaviour.
"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from infrastructure.stages.frontend.ui.contract import contract_gaps, observable_contract
from infrastructure.stages.frontend.ui.synthesis import synthesize_ui
from infrastructure.stages.requirements.tree import RequirementTree


def element(kind, parent, text=None, href=None, label=None, placeholder=None, **props):
    prop = {key: value for key, value in (("text", text), ("href", href), ("label", label),
        ("placeholder", placeholder)) if value is not None}
    prop.update(props)
    return {"type": kind, "parent": parent, "data": [], "mode": [], "prop": prop}


def requirement(rid, description, *steps):
    return {"id": rid, "description": {"text": description}, "scenarios": [{"id": "SCN-" + rid,
        "steps": [{"id": f"STEP-{rid}-{index}", "Given": given, "When": when, "Then": then}
            for index, (given, when, then) in enumerate(steps, 1)]}]}


def design_with(elements, pages, requirements):
    return {"elements": elements, "pages": pages, "requirements": requirements, "steps": {}}


class TestContractExtraction(unittest.TestCase):
    def test_extracts_names_roles_links_fields_routes_and_alerts(self):
        record = requirement("REQ-1",
            'The registration page /register contains Username, Password, and Email address fields. '
            'The submit button is labeled "Next step" and the agreement checkbox is labeled "I agree". '
            'Error role=alert. Register link whose href is /register is also required.',
            ("a visitor", "click Register", "the page opens"))
        contract = observable_contract(record)
        self.assertEqual(contract["routes"], ["/register"])
        self.assertIn({"role": "button", "name": "Next step"}, contract["labels"])
        self.assertIn({"role": "checkbox", "name": "I agree"}, contract["labels"])
        self.assertEqual(contract["fields"], ["Email address", "Password", "Username"])
        self.assertIn({"name": "Register", "href": "/register"}, contract["links"])
        self.assertTrue(contract["alerts"])

    def test_role_comes_from_the_nearest_clause_word(self):
        record = requirement("REQ-2",
            'The password field is labeled "Password". The submit button is labeled "LOGIN".',
            ("a user", "submit", "signed in"))
        labels = {label["name"]: label["role"] for label in observable_contract(record)["labels"]}
        self.assertEqual(labels, {"Password": "textbox", "LOGIN": "button"})

    def test_unquoted_prose_is_not_turned_into_a_contract(self):
        record = requirement("REQ-3", "Save the note and show a friendly confirmation.",
            ("a user", "save", "a confirmation appears"))
        contract = observable_contract(record)
        self.assertEqual(contract["labels"], [])
        self.assertEqual(contract["fields"], [])
        self.assertEqual(contract["links"], [])


class TestDesignGaps(unittest.TestCase):
    def contract(self):
        return observable_contract(requirement("REQ-1",
            'The registration page /register contains Username and Password and Email address fields. '
            'The submit button is labeled "Next step". Error role=alert.'))

    def design(self):
        return design_with(
            {"PAGE": element("page", None), "USER": element("input", "PAGE", label="Username"),
                "PASS": element("input", "PAGE", label="Password"),
                "MAIL": element("input", "PAGE", label="Email address"),
                "NEXT": element("button", "PAGE", text="Next step"), "ERROR": element("alert", "PAGE")},
            {"register": {"name": "Register", "route": "/register", "root": "PAGE", "images": []}},
            {"REQ-1": ["PAGE", "USER", "PASS", "MAIL", "NEXT", "ERROR"]})

    def test_matching_design_has_no_gaps(self):
        self.assertEqual(contract_gaps(self.design(), "REQ-1", self.contract(), created_pages=["register"]), [])

    def test_paraphrased_caption_and_missing_alert_are_reported(self):
        design = self.design()
        design["elements"]["NEXT"]["prop"]["text"] = "Submit"
        design["elements"]["ERROR"]["type"] = "text"
        gaps = contract_gaps(design, "REQ-1", self.contract(), created_pages=["register"])
        kinds = {gap["kind"] for gap in gaps}
        self.assertEqual(kinds, {"label", "alert"})
        self.assertIn('"Next step"', " ".join(gap["message"] for gap in gaps))

    def test_renamed_field_href_and_route_are_reported(self):
        design = self.design()
        design["elements"]["USER"]["prop"]["label"] = "User name"
        design["pages"]["register"]["route"] = "/signup"
        gaps = contract_gaps(design, "REQ-1", self.contract(), created_pages=["register"])
        self.assertIn("field", {gap["kind"] for gap in gaps})
        self.assertIn("route", {gap["kind"] for gap in gaps})
        design["elements"]["LINK"] = element("link", "PAGE", text="Register", href="/sign-up")
        gaps = contract_gaps(design, "REQ-1", observable_contract(requirement("REQ-1",
            'Show a Register link whose href is /register.')), created_pages=[])
        self.assertEqual([gap["kind"] for gap in gaps], ["link"])

    def test_unpaired_href_is_reported_once(self):
        design = self.design()
        gaps = contract_gaps(design, "REQ-1", observable_contract(requirement("REQ-1",
            'Expose a control whose href is /help.')), created_pages=[])
        self.assertEqual([gap["kind"] for gap in gaps], ["href"])

    def test_concealed_password_must_be_a_password_input(self):
        record = requirement("REQ-1",
            'The register page /register contains Username, Password, and Email address fields. '
            'Password fields conceal typed characters.')
        contract = observable_contract(record)
        self.assertTrue(contract["concealed"])
        self.assertEqual(contract["password_fields"], ["Password"])
        design = design_with(
            {"PAGE": element("page", None), "USER": element("input", "PAGE", label="Username"),
                "PASS": element("input", "PAGE", label="Password", input_type="text"),
                "MAIL": element("input", "PAGE", label="Email address")},
            {"register": {"name": "Register", "route": "/register", "root": "PAGE", "images": []}},
            {"REQ-1": ["PAGE", "USER", "PASS", "MAIL"]})
        gaps = contract_gaps(design, "REQ-1", contract, created_pages=["register"])
        self.assertEqual([gap["kind"] for gap in gaps], ["password_type"])
        design["elements"]["PASS"]["prop"]["input_type"] = "password"
        self.assertEqual(contract_gaps(design, "REQ-1", contract, created_pages=["register"]), [])

    def test_concealment_without_a_stated_field_is_not_enforced(self):
        contract = observable_contract(requirement("REQ-1", "Typed characters are concealed."))
        self.assertTrue(contract["concealed"])
        self.assertEqual(contract["password_fields"], [])
        self.assertEqual(contract_gaps(self.design(), "REQ-1", contract, created_pages=[]), [])


class TestExplicitRoleAnnotations(unittest.TestCase):
    """A requirement may state the role directly: "Book list"(role=region)."""

    def contract(self):
        return observable_contract(requirement("REQ-1",
            'Clicking "Books"(role=link) on the page displays "Book list"(role=region). '
            'Each book appears exactly once as "{item name}"(role=button).'))

    def design(self, role=None, entry="{item name}"):
        list_prop = {"text": "Book list"}
        if role:
            list_prop["role"] = role
        return design_with(
            {"PAGE": element("page", None),
             "NAV": element("link", "PAGE", text="Books", href="/books"),
             "LIST": {"type": "container", "parent": "PAGE", "data": [], "mode": [],
                      "prop": list_prop},
             "ITEM": element("item", "LIST"),
             "ENTRY": element("button", "ITEM", text=entry)},
            {"home": {"name": "Home", "route": "/", "root": "PAGE", "images": []}},
            {"REQ-1": ["PAGE", "NAV", "LIST", "ITEM", "ENTRY"]})

    def test_the_annotation_is_extracted_and_placeholders_are_kept(self):
        roles = self.contract()["roles"]
        self.assertIn({"role": "region", "name": "Book list"}, roles)
        self.assertIn({"role": "link", "name": "Books"}, roles)
        # A template is a contract: the control must interpolate the row's value.
        self.assertIn({"role": "button", "name": "{item name}"}, roles)

    def test_a_plain_name_does_not_satisfy_a_template(self):
        gaps = contract_gaps(self.design(role="region", entry="Open"),
            "REQ-1", self.contract(), created_pages=[])
        role_gaps = [(gap["role"], gap["name"]) for gap in gaps if gap["kind"] == "role"]
        self.assertEqual(role_gaps, [("button", "{item name}")])

    def test_a_template_name_satisfies_a_differently_spelled_placeholder(self):
        gaps = contract_gaps(self.design(role="region", entry="{title}"),
            "REQ-1", self.contract(), created_pages=[])
        self.assertEqual([gap for gap in gaps if gap["kind"] == "role"], [])

    def test_a_container_without_the_role_is_a_gap(self):
        gaps = contract_gaps(self.design(), "REQ-1", self.contract(), created_pages=[])
        role_gaps = [gap for gap in gaps if gap["kind"] == "role"]
        self.assertEqual([(gap["role"], gap["name"]) for gap in role_gaps],
            [("region", "Book list")])

    def test_declaring_prop_role_satisfies_the_contract(self):
        gaps = contract_gaps(self.design(role="region"), "REQ-1", self.contract(),
            created_pages=[])
        self.assertEqual([gap for gap in gaps if gap["kind"] == "role"], [])

class TestDuplicateNameGaps(unittest.TestCase):
    """A navigation link and a breadcrumb link named alike break exact lookups."""

    def page_design(self, breadcrumb_name="Books"):
        return {
            "pages": {"books": {"name": "Books", "route": "/books", "root": "PAGE", "images": []}},
            "elements": {
                "PAGE": element("page", None),
                "NAV": element("container", "PAGE"),
                "NAV_BOOKS": element("link", "NAV", text="Books", href="/books"),
                "CRUMB": element("container", "PAGE"),
                "CRUMB_BOOKS": element("link", "CRUMB", text=breadcrumb_name, href="/books"),
                "TITLE": element("heading", "PAGE", text="Books"),
            },
            "requirements": {"REQ-1": ["PAGE", "NAV_BOOKS", "CRUMB_BOOKS", "TITLE"]},
            "steps": {},
        }

    def test_same_role_and_name_pair_is_reported(self):
        from infrastructure.stages.frontend.ui.contract import contract_gaps, observable_contract
        design = self.page_design()
        gaps = contract_gaps(design, "REQ-1", observable_contract(
            requirement("REQ-1", "The workspace lists books.")), created_pages=[])
        duplicates = [gap for gap in gaps if gap["kind"] == "duplicate_name"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["uids"], ["CRUMB_BOOKS", "NAV_BOOKS"])
        self.assertIn("Books", duplicates[0]["message"])

    def test_distinct_names_are_not_reported(self):
        from infrastructure.stages.frontend.ui.contract import contract_gaps, observable_contract
        design = self.page_design(breadcrumb_name="Books breadcrumb")
        gaps = contract_gaps(design, "REQ-1", observable_contract(
            requirement("REQ-1", "The workspace lists books.")), created_pages=[])
        self.assertEqual([gap for gap in gaps if gap["kind"] == "duplicate_name"], [])

    def test_heading_and_link_sharing_text_are_distinct_roles(self):
        from infrastructure.stages.frontend.ui.contract import contract_gaps, observable_contract
        design = self.page_design(breadcrumb_name="Other")
        gaps = contract_gaps(design, "REQ-1", observable_contract(
            requirement("REQ-1", "The workspace lists books.")), created_pages=[])
        # TITLE heading "Books" and NAV_BOOKS link "Books" differ by role, so both stay.
        self.assertEqual([gap for gap in gaps if gap["kind"] == "duplicate_name"], [])

    def test_repeated_container_children_are_exempt(self):
        from infrastructure.stages.frontend.ui.contract import contract_gaps, observable_contract
        design = self.page_design(breadcrumb_name="Other")
        design["elements"]["LIST"] = element("list", "PAGE")
        design["elements"]["ITEM_A"] = element("item", "LIST")
        design["elements"]["ITEM_B"] = element("item", "LIST")
        design["elements"]["DELETE_A"] = element("button", "ITEM_A", text="Delete")
        design["elements"]["DELETE_B"] = element("button", "ITEM_B", text="Delete")
        design["requirements"]["REQ-1"] += ["LIST", "ITEM_A", "ITEM_B", "DELETE_A", "DELETE_B"]
        gaps = contract_gaps(design, "REQ-1", observable_contract(
            requirement("REQ-1", "The workspace lists books.")), created_pages=[])
        self.assertEqual([gap for gap in gaps if gap["kind"] == "duplicate_name"], [])


class TestBoundedDesignRepair(unittest.TestCase):
    def setUp(self):
        self.raw = {"id": "ROOT", "name": "Auth", "children": [requirement("REQ-1",
            'The registration page /register has a submit button labeled "Next step".',
            ("a visitor", "submit", "registered"))]}
        self.tree = RequirementTree(self.raw)
        self.contexts = {rid: {**self.tree.requirements[rid],
            "steps": [self.tree.steps[sid] for sid in self.tree.own_steps[rid]], "images": {}}
            for rid in self.tree.leaves}
        self.missing = {"pages": {"register": {"name": "Register", "route": "/register",
            "root": "PAGE", "images": []}}, "requirement_ui": ["PAGE", "SUBMIT"], "step_ui": {},
            "elements": {"PAGE": element("page", None), "SUBMIT": element("button", "PAGE", text="Submit")}}
        self.correct = json.loads(json.dumps(self.missing))
        self.correct["elements"]["SUBMIT"]["prop"]["text"] = "Next step"

    def compile(self, responses):
        calls = []
        queue = list(responses)

        def generate(instructions, payload):
            calls.append(payload)
            return json.loads(json.dumps(queue.pop(0)))

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        arc = Path(temporary.name) / ".arc"
        arc.mkdir()
        design = synthesize_ui(arc, self.tree, {}, self.contexts,
            Mock(generate_json=generate), Mock())
        report = json.loads((arc / "observable_contract.json").read_text(encoding="utf-8"))
        return design, calls, report

    def test_matching_design_needs_no_extra_call(self):
        design, calls, report = self.compile([self.correct])
        self.assertEqual(len(calls), 1)
        self.assertEqual(design["elements"]["SUBMIT"]["prop"]["text"], "Next step")
        self.assertEqual(report["REQ-1"]["gaps"], [])
        self.assertEqual(report["REQ-1"]["repair_attempts"], 0)

    def test_gap_is_fed_back_and_repaired(self):
        design, calls, report = self.compile([self.missing, self.correct])
        self.assertEqual(len(calls), 2)
        self.assertEqual(design["elements"]["SUBMIT"]["prop"]["text"], "Next step")
        self.assertEqual(report["REQ-1"]["repair_attempts"], 1)
        correction = calls[1]["correction"]
        self.assertEqual(correction["details"]["code"], "OBSERVABLE_CONTRACT")
        self.assertIn("Next step", json.dumps(correction, ensure_ascii=False))

    def test_repair_budget_is_bounded_and_gap_is_recorded(self):
        design, calls, report = self.compile([self.missing, self.missing, self.missing])
        self.assertEqual(len(calls), 3)
        self.assertEqual(design["elements"]["SUBMIT"]["prop"]["text"], "Submit")
        self.assertEqual(report["REQ-1"]["repair_attempts"], 2)
        self.assertEqual(len(report["REQ-1"]["gaps"]), 1)
        self.assertEqual((report["REQ-1"]["gaps"][0]["kind"]), "label")


if __name__ == "__main__":
    unittest.main()
