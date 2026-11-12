"""The accessible-name suite mirrors what a hidden browser grader queries.

The existing presence suite deliberately asserts only data-testid visibility and
data keys. These tests keep the new conformance suite separate, and pin the
role/name/href it derives from a design.
"""

import json
import re
import unittest

from infrastructure.stages.frontend.ui.testing import (accessible_name, accessible_role,
    accessibility_path, accessibility_suite, display_states, duplicate_named_controls,
    existence_suite, expected_accessibility, expected_unique_names, global_navigation_names,
    suite_paths)


def element(kind, parent, data=(), **props):
    return {"type": kind, "parent": parent, "data": list(data), "mode": [], "prop": props}


def variable(name, dtype):
    return {"var": name, "dtype": dtype, "constraints": []}


def page_design():
    return {
        "pages": {"home": {"name": "Home", "route": "/", "root": "HOME", "images": []},
            "register": {"name": "Register", "route": "/register", "root": "REG", "images": []}},
        "elements": {
            "HOME": element("page", None),
            "REGISTER": element("link", "HOME", text="Register", href="/register"),
            "LOGIN": element("link", "HOME", text="Login", href="/login"),
            "LOGOUT": element("button", "HOME", text="Logout",
                visible={"var": "signedIn", "equals": True}),
            "REG": element("page", None),
            "USER": element("input", "REG", [variable("username", "string")], label="Username"),
            "PASS": element("input", "REG", label="Password", input_type="password"),
            "NEXT": element("button", "REG", text="Next step"),
            "ERROR": element("alert", "REG", visible={"var": "errorVisible", "equals": True}),
            "TITLE": element("heading", "REG", text="Create account"),
            "NOTE": element("text", "REG", [variable("hint", "string")]),
        },
        "requirements": {"REQ-1": ["HOME", "REGISTER", "LOGIN", "LOGOUT"],
            "REQ-2": ["REG", "USER", "PASS", "NEXT", "ERROR", "TITLE", "NOTE"]},
        "steps": {},
    }


class TestAccessibleExpectations(unittest.TestCase):
    def test_visible_elements_expose_role_and_static_name(self):
        design = page_design()
        _, _, states = display_states(design, "home")
        expected = {item["uid"]: item for item in expected_accessibility(design, "home", states[0])}
        self.assertEqual(expected["REGISTER"], {"uid": "REGISTER", "role": "link",
            "name": "Register", "href": "/register"})
        self.assertEqual(expected["LOGIN"]["href"], "/login")
        self.assertNotIn("LOGOUT", expected)


class TestPlaceholderNames(unittest.TestCase):
    """A row control whose name carries runtime data is not asserted page-wide."""

    def design(self):
        return {
            "pages": {"notes": {"name": "Notes", "route": "/", "root": "PAGE", "images": []}},
            "elements": {
                "PAGE": element("page", None),
                "LIST": element("list", "PAGE"),
                "ITEM": element("item", "LIST"),
                "PIN": element("button", "ITEM", text="Pin {title}"),
                "EDITOR_PIN": element("button", "PAGE", text="Pin note"),
            },
            "requirements": {"REQ-1": ["PAGE", "LIST", "ITEM", "PIN", "EDITOR_PIN"]},
            "steps": {},
        }

    def test_a_template_control_is_not_asserted_unique(self):
        # Row controls repeat by design; only page-level names are unique.
        unique = expected_unique_names(self.design(), "notes", {})
        self.assertNotIn("Pin {title}", [item["name"] for item in unique])


class TestSharedChromeNames(unittest.TestCase):
    """The shared navigation is rendered on every page but declared only once."""

    def design(self, breadcrumb="Books"):
        return {
            "pages": {
                "home": {"name": "Home", "route": "/", "root": "HOME", "images": []},
                "book": {"name": "Book", "route": "/books/:id", "root": "PAGE", "images": []},
            },
            "elements": {
                "HOME": element("page", None),
                "NAV": element("container", "HOME"),
                "NAV_BOOKS": element("link", "NAV", text="Books", href="/books"),
                "PAGE": element("page", None),
                "CRUMB": element("container", "PAGE"),
                "CRUMB_BOOKS": element("link", "CRUMB", text=breadcrumb, href="/books"),
                "TITLE": element("heading", "PAGE", text="A book"),
            },
            "requirements": {"REQ-1": ["HOME", "NAV_BOOKS", "PAGE", "CRUMB_BOOKS", "TITLE"]},
            "steps": {},
        }

    def test_chrome_names_come_from_the_root_page(self):
        names, uids = global_navigation_names(self.design())
        self.assertIn(("link", "Books"), names)
        self.assertEqual(uids, {"NAV_BOOKS"})

    def test_the_suite_demands_one_match_for_a_chrome_name_on_every_page(self):
        suite = accessibility_suite(self.design(), "book")
        self.assertIn('page.getByRole("link", { name: new RegExp("^Books$") })', suite)
        self.assertIn(".toHaveCount(1);", suite)

    def test_a_page_control_reusing_the_chrome_name_is_a_gap(self):
        from infrastructure.stages.frontend.ui.contract import _global_name_gaps
        gaps = _global_name_gaps(self.design(), "REQ-1")
        self.assertEqual([(gap["page"], gap["role"], gap["name"]) for gap in gaps],
            [("book", "link", "Books")])
        self.assertIn("shared navigation", gaps[0]["message"])

    def test_a_distinct_breadcrumb_name_has_no_gap(self):
        from infrastructure.stages.frontend.ui.contract import _global_name_gaps
        gaps = _global_name_gaps(self.design(breadcrumb="Books breadcrumb"), "REQ-1")
        self.assertEqual(gaps, [])


class TestDuplicateNameDetection(unittest.TestCase):
    def design(self):
        return {
            "pages": {"books": {"name": "Books", "route": "/books", "root": "PAGE", "images": []}},
            "elements": {
                "PAGE": element("page", None),
                "NAV": element("container", "PAGE"),
                "NAV_BOOKS": element("link", "NAV", text="Books", href="/books"),
                "CRUMB": element("container", "PAGE"),
                "CRUMB_BOOKS": element("link", "CRUMB", text="Books", href="/books"),
            },
            "requirements": {"REQ-1": ["PAGE", "NAV_BOOKS", "CRUMB_BOOKS"]},
            "steps": {},
        }

    def test_duplicate_named_controls_are_reported_with_a_clear_message(self):
        duplicates = duplicate_named_controls(self.design(), "books", {})
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["uids"], ["CRUMB_BOOKS", "NAV_BOOKS"])
        self.assertIn("cannot distinguish", duplicates[0]["message"])

    def test_the_suite_demands_exactly_one_match_when_the_design_declares_one(self):
        design = self.design()
        design["elements"]["CRUMB_BOOKS"]["prop"]["text"] = "Books breadcrumb"
        suite = accessibility_suite(design, "books")
        self.assertIn('page.getByRole("link", { name: new RegExp("^Books$") })', suite)
        self.assertIn(".toHaveCount(1);", suite)

    def test_a_duplicate_design_does_not_emit_an_impossible_single_match_test(self):
        # Two declared "Books" links cannot resolve to one; the design gate owns
        # that repair, so the browser suite must not assert an unpassable count.
        suite = accessibility_suite(self.design(), "books")
        self.assertNotIn(".toHaveCount(1);", suite)
        self.assertEqual(len(duplicate_named_controls(self.design(), "books", {})), 1)

    def test_conditional_control_is_expected_in_its_own_state(self):
        design = page_design()
        _, _, states = display_states(design, "home")
        signed_in = next(state for state in states if state.get("signedIn") is True)
        expected = {item["uid"] for item in expected_accessibility(design, "home", signed_in)}
        self.assertIn("LOGOUT", expected)

    def test_password_field_has_a_name_but_no_implicit_role(self):
        design = page_design()
        node = design["elements"]["PASS"]
        self.assertIsNone(accessible_role(node))
        self.assertEqual(accessible_name(node), "Password")

    def test_undeclared_input_type_does_not_promise_a_role(self):
        self.assertIsNone(accessible_role({"type": "input", "prop": {"label": "Secret"}}))
        self.assertEqual(accessible_role({"type": "input", "prop": {"input_type": "text"}}), "textbox")
        self.assertEqual(accessible_role({"type": "input", "prop": {"input_type": "email"}}), "textbox")
        self.assertEqual(accessible_role({"type": "input", "prop": {"input_type": "checkbox"}}), "checkbox")
        self.assertEqual(accessible_role({"type": "input", "prop": {"input_type": "radio"}}), "radio")
        self.assertIsNone(accessible_role({"type": "input", "prop": {"input_type": "file"}}))

    def test_unlabelled_text_and_container_elements_are_skipped(self):
        design = page_design()
        _, _, states = display_states(design, "register")
        expected = {item["uid"] for item in expected_accessibility(design, "register", states[0])}
        self.assertNotIn("NOTE", expected)
        self.assertNotIn("REG", expected)


def regex_sources(suite):
    """Return every RegExp source literal the suite emits, decoded from its JSON string."""
    return [json.loads(source) for source in re.findall(r'new RegExp\(("(?:[^"\\]|\\.)*")\)', suite)]


class TestAccessibilitySuite(unittest.TestCase):
    def test_suite_asserts_role_name_and_navigation_target(self):
        suite = accessibility_suite(page_design(), "home")
        self.assertIn('toHaveRole("link")', suite)
        sources = regex_sources(suite)
        self.assertIn("^Register$", sources)
        self.assertIn("^/register/?$", sources)

    def test_playwright_has_no_exact_option_on_accessible_name(self):
        # Playwright 1.64 accepts only string|RegExp plus {ignoreCase,signal,timeout}.
        suite = accessibility_suite(page_design(), "home")
        self.assertNotIn("exact: true", suite)
        self.assertIn("^Login$", regex_sources(suite))

    def test_regex_specials_in_a_name_are_escaped(self):
        design = page_design()
        design["elements"]["NEXT"]["prop"]["text"] = "Next step (required) [1.2]"
        suite = accessibility_suite(design, "register")
        source = r"^Next step \(required\) \[1\.2\]$"
        self.assertIn(source, regex_sources(suite))
        self.assertIsNotNone(re.fullmatch(source, "Next step (required) [1.2]"))

    def test_root_href_matches_only_the_root_path(self):
        design = page_design()
        design["elements"]["HOME_LINK"] = element("link", "REG", text="Home", href="/")
        suite = accessibility_suite(design, "register")
        root = [source for source in regex_sources(suite) if "/" in source]
        self.assertTrue(root)
        self.assertTrue(all(re.fullmatch(source, "/") for source in root))
        self.assertFalse(any(re.fullmatch(source, "/register") for source in root))

    def test_suite_covers_the_state_that_reveals_a_conditional_alert(self):
        suite = accessibility_suite(page_design(), "register")
        self.assertIn('toHaveRole("alert")', suite)
        sources = regex_sources(suite)
        self.assertIn("^Next step$", sources)
        # NEXT, TITLE, ERROR (state 2); the two inputs declare no input_type,
        # so no role is promised for them, but their names are still asserted.
        self.assertEqual(suite.count("toHaveRole"), 5)
        self.assertIn("^Password$", sources)

    def test_page_without_named_controls_has_no_suite(self):
        design = {"pages": {"plain": {"name": "Plain", "route": "/plain", "root": "P", "images": []}},
            "elements": {"P": element("page", None), "T": element("text", "P", text="hello")},
            "requirements": {}, "steps": {}}
        self.assertEqual(accessibility_suite(design, "plain"), "")

    def test_presence_suite_stays_minimal(self):
        suite = existence_suite(page_design(), "home")
        self.assertNotIn("toHaveAccessibleName", suite)
        self.assertNotIn("toHaveRole", suite)
        self.assertIn("toBeVisible", suite)

    def test_suite_paths_keep_both_compiler_owned_files(self):
        self.assertEqual(suite_paths("home"), ["frontend/tests/ui/home.spec.ts",
            "frontend/tests/ui/home.a11y.spec.ts"])
        self.assertEqual(accessibility_path("home"), "frontend/tests/ui/home.a11y.spec.ts")


class TestGateOwnership(unittest.TestCase):
    def test_accessibility_gate_is_not_editable_by_the_agents_it_gates(self):
        from infrastructure.shared.agent.context import SourceFiles
        from infrastructure.shared.agent.files import editable_application_path

        self.assertTrue(editable_application_path(None, "frontend/tests/ui/home.spec.ts"))
        self.assertFalse(editable_application_path(None, "frontend/tests/ui/home.a11y.spec.ts"))
        selection = SourceFiles.__new__(SourceFiles)
        selection.tests_root = "frontend/tests"
        self.assertTrue(selection.is_test("frontend/tests/ui/home.spec.ts"))
        self.assertFalse(selection.is_test("frontend/tests/ui/home.a11y.spec.ts"))


if __name__ == "__main__":
    unittest.main()
