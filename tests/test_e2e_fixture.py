"""The E2E fixture must compile for tasks without backend API tests.

Stage 06 writes backend/tests/support/context.ts only when the task designs
backend APIs. Anonymous catalog tasks (BookStack) can legitimately have no API
suite, so the E2E fixture must not import that file.
"""

import unittest

from infrastructure.stages.frontend.triggers.e2e_runtime import FIXTURE_SOURCE


class E2EFixture(unittest.TestCase):
    def test_password_hash_helper_is_self_contained(self):
        self.assertNotIn("backend/tests/support/context.js", FIXTURE_SOURCE)
        self.assertIn("export function fixturePasswordHash(password: string): string", FIXTURE_SOURCE)
        self.assertIn("scryptSync", FIXTURE_SOURCE)
        self.assertIn("00112233445566778899aabbccddeeff", FIXTURE_SOURCE)

    def test_fixture_still_exposes_the_test_contract(self):
        self.assertIn('import { test as base, expect } from "@playwright/test"', FIXTURE_SOURCE)
        self.assertIn("export const test", FIXTURE_SOURCE)
        self.assertIn("export { expect }", FIXTURE_SOURCE)
        self.assertIn("applicationErrors", FIXTURE_SOURCE)


if __name__ == "__main__":
    unittest.main()
