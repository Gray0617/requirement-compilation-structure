"""One-shot compilation for small tasks, with chunked continuation for large ones.

The staged compiler is deliberately incremental, but Keep/BookStack-sized
applications can be produced in one file-map response. This path trades design
artifacts and local Playwright suites for wall-clock time. Full tasks are split
into leaf groups because the model proxy caps a single response; each group
extends the files produced by the previous group and is built independently.
"""

import json
import os
from pathlib import Path

import yaml

from infrastructure.shared.agent.budgets import ultra_fast_mode
from infrastructure.shared.models.text import TextModel
from infrastructure.shared.project.dependencies import install_dependencies
from infrastructure.stages.frontend.ui.testing import FrontendChecks
from infrastructure.stages.requirements.tree import RequirementTree
from infrastructure.utils.files import read_source, save_json, save_text
from infrastructure.utils.presentation.console import DemoConsole


INSTRUCTIONS = """Build the complete application described by requirements_yaml in one response.
Return exactly one JSON object:
{"files": {"frontend/src/App.tsx": "...", "frontend/src/pages/...": "..."}}

This is a self-contained web application. Follow these rules:
- Use only React + TypeScript already installed in the project. Do not add packages,
  do not import react-router-dom, and do not require a backend.
- main.tsx already renders <App />. Implement App.tsx and any page/component files.
- Persist all mutable data in window.localStorage. Initialize every required
  seed/default prerequisite (for example a default Sidebar label) on first load.
- Implement every page and route, including direct URL entry. Read
  window.location.pathname and render the matching page; use native <a href> for
  navigation links and update history for in-app navigation.
- Implement all scenarios and interactions: forms, dialogs, buttons, lists,
  create/edit/delete/archive/pin/undo behavior, reload persistence and state.
- Preserve exact accessible names, visible labels, placeholders, roles, hrefs,
  default values, option lists and required feedback text from requirements_yaml.
  Use semantic native elements (button, a, input, select, dialog/role=dialog,
  region/role=region, status/role=status) and associate every form label.
- Accessible names are contracts. For role=navigation, role=region, role=dialog
  and role=status, set aria-label to the exact name stated by the requirement.
  Use <nav aria-label="..."> for a named navigation and
  <section role="region" aria-label="..."> for named regions.
- A role=status live message must also set aria-label to its exact visible
  message (for example aria-label="Note archived" or aria-label="Action undone").
  Tests locate status regions by role and name; visible text alone is not enough.
- When a status message offers an action (for example "Note archived" with
  "Undo"), render the message and the action button as descendants of ONE element
  with role="status" and the exact aria-label. Do not put the status role on a
  text-only span and render the action as a sibling; tests scope the action lookup
  to the status element.
- Use the exact role the requirement states. Do not substitute link for button,
  button for link, navigation for a labelled sidebar, or a generic div for a
  dialog/region/status.
- Keep each role+accessible-name pair unique on a page unless the requirement
  explicitly repeats it in a list, table or item row. In particular, do not repeat
  a navigation link/button in the page body or breadcrumb with the same name:
  tests locate controls by role and exact name and fail on two matches. Give the
  extra element a distinguishing accessible name or render it as non-interactive
  text.
- Every page must contain a heading whose accessible name is the page's exact
  name from the requirement/navigation (for example "Books"). A section or list
  label such as "Book list" is an additional named region; it must not replace
  the page heading. After navigation, cancel and delete actions return to that
  page and its heading must be visible.
- A required region or section must remain visible when it is empty: render a
  visible empty-state message inside it (for example "No notes in Trash"). An
  empty container has no layout box and is treated as hidden by automated checks.
- Suggestion, autocomplete, menu and dialog overlays must not intercept clicks on
  navigation or unrelated controls. Hide suggestions when the query is empty and
  close overlays on blur, navigation or Escape; do not keep a full-size invisible
  overlay over the sidebar or page.
- Use data-testid only as a supplement; the application must work through
  accessible roles and names.
- Return complete file contents, never diffs or placeholders. Keep files under
  frontend/src. Do not return main.tsx, ui-runtime.ts, package.json or backend files.
"""

PHASED_SUFFIX = """

PHASED MODE: This response is part {part} of {total} of a larger requirement set.
requirements_yaml contains only this part. If existing_files is supplied, it is the
current application: preserve every existing route, page, control, state and
behavior, and extend it rather than replacing it. Return complete contents of every
file you change or add; you may omit files that do not change. Do not remove an
earlier feature to make room for this part.
"""

REPAIR_SUFFIX = """

REPAIR: The current application failed to build. Read correction.error and the
supplied existing_files. Return complete contents of the files you change; preserve
all existing behavior and fix the actual compile error. Do not return a partial
diff or only an explanation.
"""

FILE_PREFIX = "frontend/src/"
COMPILER_OWNED = {"frontend/src/main.tsx", "frontend/src/ui-runtime.ts"}
SINGLE_PASS_CHARS = 20_000


def _valid_files(value, require_app=True):
    """Return a clean path->content map, or raise a repairable response error."""
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        raise ValueError('Response must contain a "files" object mapping paths to source text')
    files = {}
    for path, content in value["files"].items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("Every files entry must map a string path to string source")
        path = path.replace("\\", "/").lstrip("./")
        if not path.startswith(FILE_PREFIX) or path in COMPILER_OWNED:
            continue
        if path.endswith((".json", ".lock")):
            continue
        files[path] = content
    if require_app and "frontend/src/App.tsx" not in files:
        raise ValueError("files must include frontend/src/App.tsx")
    return files


def _write(files, output):
    for path, content in files.items():
        save_text(output / path, content)


def _current_files(output, paths):
    return {path: read_source(output / path) for path in sorted(paths)
        if (output / path).is_file()}


def _node_index(root):
    nodes = {}
    def walk(node):
        nodes[node["id"]] = node
        for child in node.get("children", []) or []:
            walk(child)
    walk(root)
    return nodes


def _part_yaml(root, nodes, leaf_ids):
    subset = {
        "id": root.get("id", "ROOT"),
        "name": root.get("name", ""),
        "description": root.get("description", {}),
        "scenarios": root.get("scenarios", []) or [],
        "children": [nodes[rid] for rid in leaf_ids],
    }
    return yaml.safe_dump(subset, sort_keys=False, allow_unicode=True)


def _chunks(tree):
    size = max(1, int(os.getenv("ULTRA_FAST_CHUNK_LEAVES", "4")))
    return [tree.leaves[index:index + size] for index in range(0, len(tree.leaves), size)]


def _build(output):
    install_dependencies(output)
    return FrontendChecks(output).build()


def _contract():
    return {"allowed_prefix": FILE_PREFIX,
        "persistence": "window.localStorage",
        "framework": "React 18 + TypeScript, no new dependencies"}


def _request_part(model, prompt, payload, require_app):
    """Request one file map, retrying once on a malformed response."""
    response = None
    try:
        response = model.generate_json(prompt, payload, temperature=0.0)
        return _valid_files(response, require_app=require_app)
    except RuntimeError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        payload["correction"] = {"error": str(error), "previous_response": response}
        response = model.generate_json(prompt, payload, temperature=0.0)
        return _valid_files(response, require_app=require_app)


def _repair(model, output, arc_dir, all_files, requirements_yaml, part, total, console):
    """One bounded repair call using the current application and build error."""
    build = _build(output)
    if build["returncode"] == 0:
        return build
    payload = {"requirements_yaml": requirements_yaml, "file_contract": _contract(),
        "existing_files": all_files,
        "correction": {"error": build["output"][-16000:]}}
    try:
        files = _request_part(model, INSTRUCTIONS + REPAIR_SUFFIX, payload, require_app=False)
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError) as error:
        console.show(f"Ultra-fast | Repair failed for part {part}/{total}", str(error)[:1000])
        return build
    _write(files, output)
    all_files.update(files)
    console.show(f"Ultra-fast | Repaired {len(files)} files in part {part}/{total}",
        "\n".join(sorted(files)))
    return _build(output)


def _compile_single(model, requirements, output, arc_dir, console):
    """Try one file-map response; return (report, files) or (None, current_files)."""
    payload = {"requirements_yaml": requirements, "file_contract": _contract()}
    try:
        files = _request_part(model, INSTRUCTIONS, payload, require_app=True)
    except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as error:
        console.show("Ultra-fast | Single pass unavailable; switching to continuation groups",
            str(error)[:1000])
        return None, {}
    _write(files, output)
    all_files = dict(files)
    console.show(f"Ultra-fast | Generated {len(files)} files in single pass",
        "\n".join(sorted(files)))
    build = _repair(model, output, arc_dir, all_files, requirements, 1, 1, console)
    save_json(arc_dir / "ultra_fast.json", {"status": "GREEN" if build["returncode"] == 0 else "REPAIRING",
        "mode": "single", "files": sorted(all_files),
        "failure": None if build["returncode"] == 0 else build["output"][-8000:]})
    if build["returncode"]:
        return None, all_files
    return {"status": "GREEN", "mode": "single", "files": sorted(all_files)}, all_files


def _compile_chunked(model, root, tree, nodes, requirements, output, arc_dir, console, existing):
    """Extend a small file map in 4-leaf groups, building each group."""
    chunks = _chunks(tree)
    total = len(chunks)
    all_files = dict(existing)
    completed, failed = [], []
    for part, leaf_ids in enumerate(chunks, 1):
        part_yaml = _part_yaml(root, nodes, leaf_ids)
        payload = {"requirements_yaml": part_yaml, "file_contract": _contract()}
        if all_files:
            payload["existing_files"] = all_files
        prompt = INSTRUCTIONS + PHASED_SUFFIX.format(part=part, total=total)
        console.show(f"Ultra-fast | Part {part}/{total}", ", ".join(leaf_ids))
        try:
            files = _request_part(model, prompt, payload, require_app=not all_files)
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as error:
            if all_files:
                console.show(f"Ultra-fast | Part {part}/{total} unavailable; keeping partial app",
                    str(error)[:1000])
                break
            raise
        _write(files, output)
        all_files.update(files)
        completed.append(part)
        console.show(f"Ultra-fast | Generated {len(files)} files in part {part}/{total}",
            "\n".join(sorted(files)))
        build = _repair(model, output, arc_dir, all_files, part_yaml, part, total, console)
        if build["returncode"]:
            failed.append(part)
        save_json(arc_dir / "ultra_fast.json", {"status": "PARTIAL" if failed else "RUNNING",
            "mode": "chunked", "parts": completed, "failed_parts": failed,
            "part": part, "total": total, "files": sorted(all_files),
            "failure": None if not build["returncode"] else build["output"][-8000:]})
    final = _build(output)
    if final["returncode"]:
        raise ValueError("Ultra-fast generation did not produce a building application")
    report = {"status": "GREEN", "mode": "chunked", "parts": completed,
        "failed_parts": failed, "total_parts": total, "files": sorted(all_files)}
    save_json(arc_dir / "ultra_fast.json", report)
    return report


def compile_ultra_fast(input_dir: Path, arc_dir: Path, output_dir: Path, pause: bool = False) -> dict:
    """Prefer one complete response; fall back to 4-leaf continuation groups."""
    source = input_dir / "requirements.yaml"
    if not source.is_file():
        raise ValueError(f"Missing requirement document: {source}")
    output = arc_dir.parent.resolve()
    console = DemoConsole(pause)
    requirements = source.read_text(encoding="utf-8-sig")
    save_text(arc_dir / "requirements.yaml", requirements)
    root = yaml.safe_load(requirements)
    tree = RequirementTree(root)
    nodes = _node_index(root)
    # The model proxy truncates a single response near 180 s. Give a complete
    # response just under that ceiling; a failure falls back to smaller groups.
    os.environ.setdefault("FAST_MODEL_TIMEOUT_SECONDS",
        os.getenv("ULTRA_FAST_MODEL_TIMEOUT_SECONDS", "185"))
    model = TextModel()
    single_char_limit = int(os.getenv("ULTRA_FAST_SINGLE_CHARS", "60000"))
    files = {}
    if len(requirements) <= single_char_limit:
        report, files = _compile_single(model, requirements, output, arc_dir, console)
        if report is not None:
            return report
        console.show("Ultra-fast | Switching to 4-leaf continuation groups",
            "The single response either timed out or did not build. Existing files are preserved.")
    else:
        console.show("Ultra-fast | Large requirements; using 4-leaf continuation groups",
            f"{len(tree.leaves)} leaves, {len(requirements):,} characters")
    return _compile_chunked(model, root, tree, nodes, requirements, output, arc_dir, console, files)


def should_ultra_fast(input_dir: Path) -> bool:
    """Choose the one-shot path only for small/medium tasks in fast mode."""
    if not ultra_fast_mode(None):
        return False
    try:
        root = yaml.safe_load((input_dir / "requirements.yaml").read_text(encoding="utf-8-sig"))
        leaves = len(RequirementTree(root).leaves)
        return ultra_fast_mode(leaves)
    except (OSError, ValueError, TypeError, KeyError):
        return False
