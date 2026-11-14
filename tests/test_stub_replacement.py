"""Small scaffold stubs may be replaced once; extended files stay incremental."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from infrastructure.shared.agent.tools import FileTools
from infrastructure.shared.agent.tools.files import FileToolError, read_file


APP_STUB = (
    "export function App() {\n"
    "  return <main><h1>Application</h1><p>The project is ready for requirement implementation.</p></main>;\n"
    "}\n"
)
REGISTER_STUB = (
    'import type { Express } from "express";\n'
    'import type Database from "better-sqlite3";\n\n'
    "export function registerApis(_app: Express, _connection: Database.Database): void {}\n"
)


def replace(files, path, old_content, new_content):
    return files.execute([{"tool": "edit_file", "path": path, "operation": "replace",
        "old_content": old_content, "new_content": new_content}])


class ScaffoldStubReplacement(unittest.TestCase):
    def test_app_stub_can_be_replaced_wholesale_once(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "frontend/src/App.tsx"
            path.parent.mkdir(parents=True)
            path.write_text(APP_STUB, encoding="utf-8")
            files = FileTools(root, ["frontend/src/App.tsx"])
            replace(files, "frontend/src/App.tsx", APP_STUB,
                "export function App() { return <main>Notes</main>; }\n")
            self.assertIn("Notes", path.read_text(encoding="utf-8"))
            with self.assertRaises(FileToolError) as raised:
                replace(files, "frontend/src/App.tsx", path.read_text(encoding="utf-8"),
                    "export function App() { return <main>Other</main>; }\n")
            self.assertEqual(raised.exception.feedback["code"], "WHOLE_FILE_REPLACEMENT_NOT_ALLOWED")

    def test_register_stub_can_be_replaced_but_routes_are_preserved(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "backend/src/api/register.ts"
            path.parent.mkdir(parents=True)
            path.write_text(REGISTER_STUB, encoding="utf-8")
            files = FileTools(root, ["backend/src/api/register.ts"])
            routed = (REGISTER_STUB.replace("void {}", 'void { app.get("/api/x", (_q, r) => r.json({})); }'))
            replace(files, "backend/src/api/register.ts", REGISTER_STUB, routed)
            self.assertIn("app.get", path.read_text(encoding="utf-8"))
            with self.assertRaises(FileToolError) as raised:
                replace(files, "backend/src/api/register.ts", path.read_text(encoding="utf-8"),
                    "export function registerApis(): void {}\n")
            self.assertEqual(raised.exception.feedback["code"], "WHOLE_FILE_REPLACEMENT_NOT_ALLOWED")

    def test_directory_read_reports_the_specific_problem(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "frontend/tests/e2e").mkdir(parents=True)
            files = FileTools(root, [])
            with self.assertRaises(FileToolError) as raised:
                read_file(files, "frontend/tests/e2e")
            self.assertEqual(raised.exception.feedback["code"], "PATH_IS_DIRECTORY")


if __name__ == "__main__":
    unittest.main()
