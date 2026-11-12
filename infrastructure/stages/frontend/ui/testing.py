"""Deterministic UI existence suites and bounded browser/typecheck commands."""

from infrastructure.shared.agent.feedback import CheckFailure

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile

from infrastructure.stages.frontend.ui.design import (accessible_name, data_symbols,
    element_role, in_repeated_container, page_elements, visible)
from infrastructure.utils.processes import capture_process, npm_command

KEY_TYPES = {"page", "form", "input", "checkbox", "radio", "radiogroup", "select", "button",
    "heading", "link", "list", "image", "alert", "table", "text"}


def display_states(design: dict, pid: str):
    """Enumerate the frontend values that expose each conditional element once."""
    elements = page_elements(design, pid)
    variables = data_symbols(elements)
    empty_values = {"string": "", "integer": 0, "number": 0, "boolean": False,
        "array": [], "object": {}, "null": None}
    values = {}
    for name, declaration in variables.items():
        values[name] = empty_values[declaration["dtype"]]
    states = [values]
    for uid in elements:
        overrides = {}
        current, seen = uid, set()
        possible = True
        while current in elements and current not in seen:
            seen.add(current)
            node = elements[current]
            rule = node.get("prop", {}).get("visible", True)
            if rule is False:
                possible = False
                break
            if isinstance(rule, dict):
                name, expected = rule["var"], rule["equals"]
                if name in overrides and overrides[name] != expected:
                    possible = False
                    break
                overrides[name] = expected
            current = node.get("parent")
        candidate = {**values, **overrides}
        if possible and candidate not in states:
            states.append(candidate)
    return elements, variables, states


def accessible_name(node: dict) -> str | None:
    """Return the static accessible name a design element promises, if any."""
    prop = node.get("prop") or {}
    kind = node.get("type")
    keys = ("label", "placeholder") if kind in {"input", "select"} else ("text", "label", "alt")
    for key in keys:
        value = prop.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def accessible_role(node: dict) -> str | None:
    """Return the browser role an acceptance suite would query for this element."""
    return element_role(node)


def duplicate_named_controls(design: dict, pid: str, state: dict) -> list[dict]:
    """Return visible controls that share one role+name pair with another sibling.

    Automated suites locate a control with ``getByRole(role, { name, exact })`` and
    fail on two matches. A page with a navigation link, a breadcrumb link and a
    heading all reading "Books" breaks every such lookup, so duplicates outside
    list/table/item containers are reported here.
    """
    seen: dict[tuple, list[str]] = {}
    for uid, node in page_elements(design, pid).items():
        if not visible(design, uid, state) or in_repeated_container(design, uid):
            continue
        role, name = accessible_role(node), accessible_name(node)
        if not role or not name or role in {"alert", "img"}:
            continue
        seen.setdefault((role, name), []).append(uid)
    duplicates = []
    for (role, name), uids in seen.items():
        if len(uids) > 1:
            duplicates.append({"role": role, "name": name, "uids": sorted(uids),
                "message": f'{len(uids)} visible elements expose the same accessible name '
                    f'"{name}" with role "{role}" ({", ".join(sorted(uids))}); an automated '
                    f'getByRole("{role}", {{ name: "{name}", exact: true }}) lookup cannot '
                    'distinguish them. Keep the required one and rename or remove the others.'})
    return sorted(duplicates, key=lambda item: (item["role"], item["name"]))


def expected_unique_names(design: dict, pid: str, state: dict) -> list[dict]:
    """Return role+name pairs the page must expose exactly once.

    This is the invariant that actually failed in production: the design declared
    one "Books" link on the book page, but the implementation repeated the site
    navigation there, so getByRole('link', { name: 'Books' }) matched two elements
    and nine hidden tests died on one strict-mode violation.

    A pair is only asserted when the design declares exactly one such control and
    no list/table/item row reuses the name, because those legitimately repeat.
    """
    page_level: dict[tuple, list[str]] = {}
    repeated: set[tuple] = set()
    for uid, node in page_elements(design, pid).items():
        if not visible(design, uid, state):
            continue
        role, name = accessible_role(node), accessible_name(node)
        if not role or not name or role in {"alert", "img"}:
            continue
        key = (role, name)
        if in_repeated_container(design, uid):
            repeated.add(key)
        else:
            page_level.setdefault(key, []).append(uid)
    return [{"role": role, "name": name, "uid": uids[0]}
        for (role, name), uids in sorted(page_level.items())
        if len(uids) == 1 and (role, name) not in repeated]


def global_navigation_names(design: dict):
    """Return the role+name pairs the shared application chrome exposes.

    The design records the persistent navigation once, usually on the root page,
    while every implemented page renders it again. A page-local control that
    reuses one of those names creates a second match on that page, which is what
    broke nine hidden tests: a breadcrumb "Books" link beside the navigation
    "Books" link. Both the set of pairs and the element IDs are returned.
    """
    pages = design.get("pages") or {}
    root = next((pid for pid, page in pages.items()
        if isinstance(page, dict) and page.get("route") == "/"), None)
    if root is None:
        return set(), set()
    names, uids = set(), set()
    for uid, node in page_elements(design, root).items():
        if in_repeated_container(design, uid):
            continue
        role, name = accessible_role(node), accessible_name(node)
        if role in {"link", "button"} and name:
            names.add((role, name))
            uids.add(uid)
    return names, uids


def expected_accessibility(design: dict, pid: str, state: dict) -> list[dict]:
    """Return visible named elements and the role/name/href they must expose."""
    expected = []
    for uid, node in page_elements(design, pid).items():
        if not visible(design, uid, state):
            continue
        role, name = accessible_role(node), accessible_name(node)
        href = (node.get("prop") or {}).get("href") if node.get("type") == "link" else None
        if role is None:
            # An input without a declared input_type still renders as a text box or a
            # concealed field, so assert its name and label but never a role the
            # design did not promise. Other untyped nodes are not controls.
            if node.get("type") != "input" or not name:
                continue
        if role != "alert" and not name:
            continue
        if role == "alert" and name is None:
            pass
        expected.append({"uid": uid, "role": role, "name": name, "href": href,
            "field": node.get("type") in {"input", "select"}})
    return expected



def presentation_run_status(result):
    """Repair existing page runtime failures; only assertion failures establish valid RED."""
    if result["returncode"] == 0:
        require_green(result)
        return "GREEN"
    headlines = [message.split("\n", 1)[0] for message in result.get("failures", [])]
    if not result.get("errors") and not result.get("skipped") and not result.get("flaky") and result.get("failed") and any("UI_RUNTIME_ERROR" in message or "UI_BUSINESS_CALL" in message for message in headlines):
        return "REPAIR_REQUIRED"
    require_red(result)
    return "RED_CONFIRMED"


def test_path(pid: str) -> str:
    """Return a stable, filesystem-safe Playwright spec path for a page ID."""
    return f"frontend/tests/ui/{pid}.spec.ts"


def accessibility_path(pid: str) -> str:
    """Return the compiler-owned accessible-name conformance suite path."""
    return f"frontend/tests/ui/{pid}.a11y.spec.ts"


def suite_paths(pid: str) -> list[str]:
    """Return every compiler-owned browser suite for one page."""
    return [test_path(pid), accessibility_path(pid)]


def existing_suites(output, pids) -> list[str]:
    """Return the browser suites already published for the supplied page IDs."""
    return [path for pid in pids for path in suite_paths(pid) if (output / path).is_file()]


def existence_suite(design: dict, pid: str) -> str:
    """Supply test-local display data; check controls and actual application data keys."""
    page = design["pages"][pid]
    elements, variables, states = display_states(design, pid)

    lines = ['// Compiler-owned presence tests. Display values belong to this suite only.',
        'import { test, expect } from "@playwright/test";', '']
    for number, state in enumerate(states, 1):
        lines.append(f'test({json.dumps(pid + " / display " + str(number) + " / UI element existence")}, async ({{ page }}) => {{')
        lines.extend([
            '  const runtimeErrors: string[] = [];',
            '  page.on("pageerror", error => runtimeErrors.push(error.message));',
            '  await page.addInitScript(testData => { (window as unknown as { __ARC_UI_TEST_DATA__: unknown }).__ARC_UI_TEST_DATA__ = testData; }, '
                + json.dumps({"page": pid, "data": state}, ensure_ascii=False) + ');',
            f'  await page.goto({json.dumps(page["route"])});',
            '  if (runtimeErrors.length) throw new Error("UI_RUNTIME_ERROR: " + runtimeErrors.join("\\n"));',
            '  try {',
        ])
        for uid, node in elements.items():
            kind = node.get("type", "page" if uid == page["root"] else "container")
            if kind not in KEY_TYPES:
                continue
            locator = f"page.getByTestId({json.dumps(uid)})"
            shown = visible(design, uid, state)
            lines.append(f"  await expect({locator}){'' if shown else '.not'}.toBeVisible();")
        lines.extend([
            '  await expect.poll(() => page.evaluate(({ pageId, keys }) => {',
            '    const state = (window as unknown as { __ARC_UI_DATA__?: { page: string; data: Record<string, unknown> } }).__ARC_UI_DATA__;',
            '    return state?.page === pageId && keys.every(key => Object.prototype.hasOwnProperty.call(state.data, key));',
            '  }, ' + json.dumps({"pageId": pid, "keys": sorted(variables)}) + ')).toBe(true);',
            '  } catch (error) {',
            '    if (runtimeErrors.length) throw new Error("UI_RUNTIME_ERROR: " + runtimeErrors.join("\\n"));',
            '    throw error;',
            '  }',
            '  if (runtimeErrors.length) throw new Error("UI_RUNTIME_ERROR: " + runtimeErrors.join("\\n"));',
            '});', '',
        ])
    return "\n".join(lines)


_JS_REGEX_SPECIALS = re.compile(r"([.*+?^${}()|\[\]\\])")

# A design name may carry runtime data: "Pin {title}", "{title} pinned note".
# Such a name must be rendered with the item's value, never as literal braces.
_PLACEHOLDER = re.compile(r"\{[^{}]{1,80}\}")


def name_pattern(name: str) -> str:
    """Return an anchored RegExp source where each {placeholder} accepts runtime data."""
    parts, cursor = [], 0
    for match in _PLACEHOLDER.finditer(name):
        parts.append(re.escape(name[cursor:match.start()]))
        parts.append(".+")
        cursor = match.end()
    parts.append(re.escape(name[cursor:]))
    return "^" + "".join(parts) + "$"


def template_segments(name: str) -> tuple:
    """Return a name's literal parts, with every placeholder collapsed to one gap.

    "Pin {title}" and "Pin {note.title}" describe the same control because their
    literal text matches; a plain "Pin" does not.
    """
    parts, cursor = [], 0
    for match in _PLACEHOLDER.finditer(name):
        parts.append(name[cursor:match.start()])
        cursor = match.end()
    parts.append(name[cursor:])
    return tuple(parts)


def _js_name(name: str) -> str:
    """Encode an accessible-name expectation; placeholders become wildcards."""
    return json.dumps(name_pattern(name)) if _PLACEHOLDER.search(name) else _js_regex(name)


def _js_regex(text: str, suffix: str = "") -> str:
    """Return a JSON-encoded JavaScript RegExp literal source matching the whole text."""
    escaped = _JS_REGEX_SPECIALS.sub(r"\\\1", text)
    return json.dumps(f"^{escaped}{suffix}$")


def _href_pattern(href: str) -> str:
    """Accept one optional trailing slash without accepting a different target."""
    base = href.rstrip("/")
    return _js_regex(base or "/", "" if not base else "/?")


def accessibility_suite(design: dict, pid: str) -> str:
    """Assert that rendered controls expose the design's accessible names and roles.

    Hidden acceptance suites locate controls by role and exact accessible name.
    A page can exist with every data-testid and still fail those queries when a
    caption is renamed, an input label is not associated with its control, or a
    native button/link is replaced by an untyped element. This compiler-owned
    suite renders the recorded names/roles against the real DOM.

    Playwright's ``toHaveAccessibleName`` accepts only a string or RegExp, so an
    anchored RegExp carries the exact-match requirement.
    """
    page = design["pages"][pid]
    _, _, states = display_states(design, pid)
    chrome, _ = global_navigation_names(design)
    lines = ['// Compiler-owned accessible-name conformance tests. Display values belong to this suite only.',
        'import { test, expect } from "@playwright/test";', '']
    for number, state in enumerate(states, 1):
        expected = expected_accessibility(design, pid, state)
        unique = expected_unique_names(design, pid, state)
        # A page never declares the shared chrome, but it renders it: any page-local
        # control reusing a chrome name must be renamed rather than duplicated.
        declared = {(item["role"], item["name"]) for item in expected}
        interactive = any(item["role"] in {"link", "button"} for item in expected)
        chrome_only = sorted(chrome - declared) if interactive else []
        if not expected and not unique and not chrome_only:
            continue
        lines.append(f'test({json.dumps(pid + " / display " + str(number) + " / accessible names")}, async ({{ page }}) => {{')
        lines.extend([
            '  const runtimeErrors: string[] = [];',
            '  page.on("pageerror", error => runtimeErrors.push(error.message));',
            '  await page.addInitScript(testData => { (window as unknown as { __ARC_UI_TEST_DATA__: unknown }).__ARC_UI_TEST_DATA__ = testData; }, '
                + json.dumps({"page": pid, "data": state}, ensure_ascii=False) + ');',
            f'  await page.goto({json.dumps(page["route"])});',
            '  if (runtimeErrors.length) throw new Error("UI_RUNTIME_ERROR: " + runtimeErrors.join("\\n"));',
            '  try {',
        ])
        for item in unique:
            # The design promises one control with this role and name, so extra
            # matches (duplicated chrome, a repeated navigation bar) must fail here
            # instead of in the hidden acceptance suite.
            locator = (f"page.getByRole({json.dumps(item['role'])}, "
                f"{{ name: new RegExp({_js_name(item['name'])}) }})")
            lines.append(f"  await expect({locator}).toHaveCount(1);")
        for role, name in chrome_only:
            locator = f"page.getByRole({json.dumps(role)}, {{ name: new RegExp({_js_name(name)}) }})"
            lines.append(f"  await expect({locator}).toHaveCount(1);")
        for item in expected:
            locator = f"page.getByTestId({json.dumps(item['uid'])})"
            if item["role"]:
                lines.append(f"  await expect({locator}).toHaveRole({json.dumps(item['role'])});")
            if item["name"]:
                lines.append(f"  await expect({locator}).toHaveAccessibleName(new RegExp({_js_name(item['name'])}));")
            if item["href"]:
                lines.append(f"  await expect({locator}).toHaveAttribute(\"href\", new RegExp({_href_pattern(item['href'])}));")
            if item.get("field") and item["name"] and not in_repeated_container(design, item["uid"]):
                # A placeholder supplies an accessible name but does NOT satisfy
                # getByLabel, which acceptance suites use for form fields.
                lines.append(f"  await expect(page.getByLabel(new RegExp({_js_name(item['name'])}))).toHaveCount(1);")
        lines.extend([
            '  } catch (error) {',
            '    if (runtimeErrors.length) throw new Error("UI_RUNTIME_ERROR: " + runtimeErrors.join("\\n"));',
            '    throw error;',
            '  }',
            '  if (runtimeErrors.length) throw new Error("UI_RUNTIME_ERROR: " + runtimeErrors.join("\\n"));',
            '});', '',
        ])
    return "\n".join(lines) if len(lines) > 3 else ""


def require_green(result: dict) -> None:
    """Require passing actual browser assertions with no reported runtime errors."""
    if result["returncode"] or result.get("errors") or not result.get("passed") or result.get("failed"):
        raise CheckFailure("UI checks are not GREEN", result)


def require_red(result: dict) -> None:
    """Require missing-component assertion failures rather than browser, server, or application runtime failures."""
    errors = result.get("failures", [])
    excluded = ("UI_RUNTIME_ERROR", "UI_BUSINESS_CALL", "ERR_", "browserType.launch", "page.goto:", "SyntaxError", "ReferenceError", "Cannot find module")
    # Code frames can contain the literal runtime-guard text from adjacent lines;
    # classify the actual error headline rather than those source snippets.
    headlines = [re.sub(r"\x1b\[[0-9;]*m", "", message).split("\n", 1)[0] for message in errors]
    if not result["returncode"] or not result.get("failed") or result.get("errors") or not errors or any(not re.search(r"expect\(|AssertionError", message) or any(word in message for word in excluded) for message in headlines):
        raise ValueError(f"Expected missing-component assertion RED, not infrastructure/runtime failure: {result}")


def parse_playwright_result(result: dict, include_files: bool = False) -> dict:
    """Parse the Playwright JSON reporter into counts, failures, runtime errors, and optional per-file totals."""
    try:
        report = json.loads(result.pop("stdout"))
    except (ValueError, KeyError) as error:
        raise ValueError(f"Playwright did not return its JSON report: {result['output']}") from error
    failures = []
    files = {}

    def visit(suite):
        """Accumulate nested Playwright suite/spec results into the normalized browser-check report."""
        for spec in suite.get("specs", []):
            filename = spec.get("file", suite.get("file", ""))
            files[filename] = files.get(filename, 0) + len(spec.get("tests", []))
            for test in spec.get("tests", []):
                for run in test.get("results", []):
                    if run.get("status") not in {"passed", "skipped"}:
                        failures.append("\n".join(item.get("message", "") for item in run.get("errors", [])) + "\nTest file: " + filename)
        for child in suite.get("suites", []):
            visit(child)

    for suite in report.get("suites", []):
        visit(suite)
    stats = report.get("stats", {})
    if isinstance(stats.get("duration"), (int, float)):
        result["test_seconds"] = stats["duration"] / 1000
    result.update({"passed": stats.get("expected", 0), "failed": stats.get("unexpected", 0),
        "skipped": stats.get("skipped", 0), "flaky": stats.get("flaky", 0),
        "errors": report.get("errors", []), "failures": failures})
    if include_files:
        result["files"] = files
    result["output"] = json.dumps({key: value for key, value in result.items() if key != "output"}, ensure_ascii=False, indent=2)[-24000:]
    return result


class FrontendChecks:
    """Compiler-owned browser installation, TypeScript, Playwright, and workspace build commands."""
    def __init__(self, output: Path, timeout: int = 600) -> None:
        """Bind browser/build checks to the generated workspace and locate Node.js and npm."""
        self.output, self.timeout = output.resolve(), timeout
        self.node = shutil.which("node")
        if not self.node:
            raise ValueError("Node.js is required for UI checks")
        self.node = str(Path(self.node).resolve())

    def execute(self, args: list[str], timeout: int | None = None, env: dict | None = None, executable: str | None = None) -> dict:
        """Run a fixed check command, capture its output, and stop its process group on timeout or interruption."""
        try:
            return capture_process([executable or self.node, *args], cwd=self.output,
                timeout=timeout or self.timeout, env=env)
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"Frontend check timed out after {timeout or self.timeout}s") from error

    def install_browser(self) -> None:
        """Install the Chromium browser used by the generated Playwright tests."""
        result = self.execute(["node_modules/@playwright/test/cli.js", "install", "chromium"], timeout=600)
        print(result["output"], flush=True)
        if result["returncode"]:
            raise ValueError("Playwright Chromium installation failed; install its OS libraries if reported above")

    def typecheck(self, files: list[str], *, test_roots=("frontend/tests/ui",), source_files=("src/**/*.ts", "src/**/*.tsx")) -> dict:
        """Compile selected frontend/test roots with a temporary TypeScript configuration, then remove that configuration."""
        includes = list(source_files)
        for relative in files:
            path = (self.output / relative).resolve()
            if not any(path.is_relative_to(self.output / root) for root in test_roots):
                raise ValueError("Typecheck only the supplied frontend test roots")
            includes.append(path.relative_to(self.output / "frontend").as_posix())
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", suffix=".json", prefix=".tsconfig-ui-", dir=self.output / "frontend", delete=False) as config:
            path = Path(config.name)
            json.dump({"extends": "./tsconfig.json", "compilerOptions": {"types": ["vite/client", "node"], "forceConsistentCasingInFileNames": True}, "include": includes}, config)
        try:
            result = self.execute(["node_modules/typescript/bin/tsc", "--noEmit", "-p", str(path)])
            result.pop("stdout", None)
            return result
        finally:
            path.unlink(missing_ok=True)

    def tests(self, files: list[str]) -> dict:
        """Run selected UI-existence specs with Playwright and normalize the reporter output."""
        if not files:
            raise ValueError("Do not run an empty UI suite")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {**os.environ, "ARC_UI_PORT": str(port)}
        result = self.execute(["node_modules/@playwright/test/cli.js", "test", "--config", "frontend/playwright.ui.config.ts", *files], env=env)
        return parse_playwright_result(result)

    def build(self) -> dict:
        """Run the generated workspace's frontend and backend build command and return the captured result."""
        command = npm_command()
        return self.execute([*command[1:], "run", "build"], executable=command[0])
