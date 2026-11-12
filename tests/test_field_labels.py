"""A field's accessible name is not enough: getByLabel needs a real label.

The bookstack page editor rendered
    <input placeholder="Page title">
Placeholder text supplies an accessible name, so the conformance suite passed,
but the hidden suite locates fields with getByLabel('Page title'), which ignores
placeholders. Every page-flow test then timed out.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from infrastructure.stages.frontend.ui.testing import accessibility_suite


def element(kind, parent, **props):
    return {"type": kind, "parent": parent, "data": [], "mode": [], "prop": props}


def design():
    return {
        "pages": {"form": {"name": "Form", "route": "/create", "root": "PAGE", "images": []}},
        "elements": {
            "PAGE": element("page", None),
            "TITLE": element("input", "PAGE", label="Page title"),
            "CONTENT": element("input", "PAGE", label="Write your page content here..."),
            "SAVE": element("button", "PAGE", text="Save Page"),
        },
        "requirements": {"REQ-1": ["PAGE", "TITLE", "CONTENT", "SAVE"]},
        "steps": {},
    }


class FieldLabels(unittest.TestCase):
    def test_every_declared_field_is_reachable_by_label(self):
        suite = accessibility_suite(design(), "form")
        self.assertIn('page.getByLabel(new RegExp("^Page title$"))', suite)
        self.assertIn('page.getByLabel(new RegExp("^Write your page content here\\\\.\\\\.\\\\.$"))', suite)
        self.assertEqual(suite.count("getByLabel"), 2)

    def test_buttons_are_not_asserted_through_get_by_label(self):
        suite = accessibility_suite(design(), "form")
        self.assertNotIn('getByLabel(new RegExp("^Save Page$"))', suite)

    def test_repeated_row_fields_are_not_asserted_at_page_level(self):
        repeated = design()
        repeated["elements"].update({
            "LIST": element("list", "PAGE"),
            "ITEM": element("item", "LIST"),
            "ROW_TITLE": element("input", "ITEM", label="Note title"),
        })
        suite = accessibility_suite(repeated, "form")
        self.assertNotIn('getByLabel(new RegExp("^Note title$"))', suite)


if __name__ == "__main__":
    unittest.main()
