"""Deterministic observable-UI contract extraction and design conformance.

Requirement documents state some interface facts in machine-checkable form:
exact accessible names, link targets, ``role=alert`` and explicit field lists.
Hidden acceptance suites assert those facts through accessible roles and exact
names, so a design that paraphrases them fails while every local check stays
green.

This module extracts only facts the requirement states explicitly. It never
guesses wording that the requirement leaves open, and it never invents a role
that the sentence does not name.
"""

import re

from infrastructure.stages.frontend.ui.design import element_role, page_elements

# Role words are ordered most specific first; a tie resolves to the nearest match.
_ROLE_WORDS = (
    ("checkbox", "checkbox"),
    ("textbox", "textbox"),
    ("heading", "heading"),
    ("button", "button"),
    ("link", "link"),
    ("radio", "radio"),
    ("field", "textbox"),
    ("input", "textbox"),
    ("tab", "tab"),
)

_NAME_PATTERNS = (
    re.compile(r'accessible name\s+"([^"]+)"', re.IGNORECASE),
    re.compile(r'label(?:l)?ed\s+"([^"]+)"', re.IGNORECASE),
)

_LINK_WITH_HREF = re.compile(
    r'([A-Z][\w-]*)\s+link\s+whose\s+href\s+is\s+"?(/[^\s".,;)]+)', re.IGNORECASE
)
_TWO_LINKS_WITH_HREFS = re.compile(
    r'([A-Z][\w-]*)\s+and\s+([A-Z][\w-]*)\s+links?\s+with\s+href\s+values?\s+'
    r'"?(/[^\s".,;)]+)"?\s+and\s+"?(/[^\s".,;)]+)"?',
    re.IGNORECASE,
)
_HREF = re.compile(r'href\s+(?:is|values?)\s+"?(/[^\s".,;)]+)', re.IGNORECASE)
_ROUTE_AFTER_PAGE = re.compile(r'\bpage\s+(/(?:[a-z][\w/-]*)?)', re.IGNORECASE)
_ROUTE_BEFORE_PAGE = re.compile(r'(/(?:[a-z][\w/-]*)?)\s+page\b', re.IGNORECASE)
_FIELD_LIST = re.compile(r'\b(?:contains|includes)\b([^.]{5,400}?)\bfields?\b', re.IGNORECASE)
_ALERT = re.compile(r'role\s*=\s*alert', re.IGNORECASE)
_CONCEAL = re.compile(r'\b(?:conceal\w*|masked|obscured|hidden characters)\b', re.IGNORECASE)
# Requirement authors annotate a name with its role directly: "Book list"(role=region).
# That is the exact pair a hidden suite queries, so it is a contract, not a hint.
_EXPLICIT_ROLE = re.compile(r'"([^"]{1,200})"\s*\(\s*role\s*=\s*([a-z][a-z-]{1,30})\s*\)',
    re.IGNORECASE)
_ARIA_ROLES = {"link", "button", "region", "alert", "heading", "textbox", "combobox",
    "checkbox", "radio", "radiogroup", "tab", "tablist", "dialog", "list", "listitem",
    "table", "img", "navigation", "main", "banner", "contentinfo", "form", "group",
    "status", "listbox", "option", "search", "menu", "menuitem"}

MAX_NAME_LENGTH = 200
MAX_FIELD_LENGTH = 40
MIN_FIELD_LIST = 3


class ObservableContractError(ValueError):
    """A design omits or paraphrases an explicit requirement interface fact."""

    def __init__(self, requirement_id, gaps):
        self.gaps = list(gaps)
        detail = "; ".join(gap["message"] for gap in self.gaps)
        super().__init__(f"{requirement_id} UI design conflicts with the requirement: {detail}")
        self.feedback = {
            "code": "OBSERVABLE_CONTRACT",
            "requirement": requirement_id,
            "gaps": self.gaps,
            "instruction": (
                "The original requirement states these interface facts explicitly. "
                "Preserve the exact spelling and case in the element prop (text, label, "
                "placeholder or href) and use the required element type/role. Return the "
                "complete affected elements/pages again, not only a note."
            ),
        }


def _requirement_text(requirement):
    """Collect the description and every Given/When/Then step for one requirement."""
    description = requirement.get("description") or ""
    if isinstance(description, dict):
        description = description.get("text") or ""
    parts = [description]
    for step in requirement.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        for key in ("Given", "When", "Then"):
            parts.append(step.get(key) or "")
    return "\n".join(part for part in parts if isinstance(part, str))


def _clause(text, position):
    """Return the sentence fragment before a match, so role words stay in scope."""
    return re.split(r"(?<=[.;])\s|\n", text[:position])[-1]


def _role_before(text, position):
    """Pick the nearest explicit UI role word in the same clause, or None."""
    clause = _clause(text, position)
    nearest = None
    for word, role in _ROLE_WORDS:
        for match in re.finditer(rf"\b{word}\b", clause, re.IGNORECASE):
            if nearest is None or match.end() > nearest[0]:
                nearest = (match.end(), role)
    return nearest[1] if nearest else None


def _field_names(text):
    """Extract only long, comma-separated capitalised label lists anchored by "fields"."""
    names = []
    for match in _FIELD_LIST.finditer(text):
        raw = match.group(1)
        parts = [part.strip(" .:;") for part in re.split(r",|\s+and\s+", raw)]
        parts = [re.sub(r"^(?:a|an|the)\s+", "", part, flags=re.IGNORECASE) for part in parts if part]
        parts = [part for part in parts if re.fullmatch(
            rf"[A-Z][A-Za-z0-9 /-]{{0,{MAX_FIELD_LENGTH - 1}}}", part) and " with " not in part]
        if len(parts) >= MIN_FIELD_LIST:
            names.extend(parts)
    return sorted(set(names))


def observable_contract(requirement):
    """Return the explicit, checkable UI facts stated by one requirement.

    Keys: ``labels`` ({role, name}), ``fields`` (label strings), ``links``
    ({name, href}), ``hrefs``, ``routes`` and ``alerts``. Unknown wording is
    never synthesised, so an empty result means "nothing explicit to check".
    """
    text = _requirement_text(requirement)
    labels, seen = [], set()
    for pattern in _NAME_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip()
            if not name or len(name) > MAX_NAME_LENGTH:
                continue
            role = _role_before(text, match.start())
            key = (role, name)
            if key in seen:
                continue
            seen.add(key)
            labels.append({"role": role, "name": name})
    links, seen_links = [], set()
    for match in _LINK_WITH_HREF.finditer(text):
        pair = (match.group(1), match.group(2))
        if pair not in seen_links:
            seen_links.add(pair)
            links.append({"name": pair[0], "href": pair[1]})
    for match in _TWO_LINKS_WITH_HREFS.finditer(text):
        for name, href in ((match.group(1), match.group(3)), (match.group(2), match.group(4))):
            if (name, href) not in seen_links:
                seen_links.add((name, href))
                links.append({"name": name, "href": href})
    routes = {route for route in _ROUTE_AFTER_PAGE.findall(text) if route}
    routes.update(route for route in _ROUTE_BEFORE_PAGE.findall(text) if route)
    roles, seen_roles = [], set()
    for match in _EXPLICIT_ROLE.finditer(text):
        name, role = match.group(1).strip(), match.group(2).strip().lower()
        # "{item name}" is a template for runtime data, so it stays a contract:
        # the design must declare a control whose literal text matches its shape.
        if not name or role not in _ARIA_ROLES:
            continue
        if (role, name) in seen_roles:
            continue
        seen_roles.add((role, name))
        roles.append({"role": role, "name": name})
    return {
        "labels": labels,
        "roles": roles,
        "fields": _field_names(text),
        "links": links,
        "hrefs": sorted(set(_HREF.findall(text))),
        "routes": sorted(routes),
        "alerts": bool(_ALERT.search(text)),
        # "Password fields conceal typed characters" is a stated implementation fact,
        # not a styling preference: the control must be a real concealed input.
        "concealed": bool(_CONCEAL.search(text)),
        "password_fields": _password_fields(labels, _field_names(text)),
    }


def _password_fields(labels, fields):
    """Return every explicitly named field that must conceal its input."""
    names = {name for name in fields if "password" in name.lower()}
    names.update(label["name"] for label in labels if "password" in label["name"].lower())
    return sorted(names)


def _prop(node):
    value = node.get("prop")
    return value if isinstance(value, dict) else {}


def _names(node):
    """Return every static name an element declares, in contract-precedence order."""
    prop = _prop(node)
    return [value for key in ("text", "label", "placeholder", "alt")
        if isinstance(value := prop.get(key), str) and value.strip()]


def _element_role(node):
    """Map a design element to the browser role an acceptance suite would query."""
    return element_role(node)


def _name_declared(elements, role, name):
    """True when an element exposes that role and a matching name.

    A name carrying braces is a template: "Pin {title}" is satisfied by any
    element whose literal parts match, so the design may spell the placeholder
    differently as long as the rendered text is the same shape.
    """
    from infrastructure.stages.frontend.ui.testing import template_segments

    wanted = template_segments(name)
    for node in elements:
        if _element_role(node) != role:
            continue
        if any(template_segments(declared) == wanted for declared in _names(node)):
            return True
    return False


def requirement_pages(design, requirement_id):
    """Return page IDs that own elements of this requirement, else every page.

    Falling back to every page keeps a proposal that omitted ``requirement_ui``
    from turning a real gap into a false pass.
    """
    owned = set(design.get("requirements", {}).get(requirement_id, []) or [])
    pages = [pid for pid in design.get("pages", {})
        if owned & set(page_elements(design, pid))]
    return pages or list(design.get("pages", {}))


def _page_elements(design, requirement_id):
    """Return the element records this requirement owns or shares a page with.

    Explicit ``requirement_ui`` ownership is consulted first because an element
    may be declared without a parent chain; the page subtrees then add shared
    controls such as an existing error banner.
    """
    selected = {}
    for uid in design.get("requirements", {}).get(requirement_id, []) or []:
        node = design.get("elements", {}).get(uid)
        if isinstance(node, dict):
            selected[uid] = node
    for pid in requirement_pages(design, requirement_id):
        selected.update(page_elements(design, pid))
    return list(selected.values()) if selected else list(design.get("elements", {}).values())


def contract_gaps(design, requirement_id, contract, created_pages=()):
    """List explicit requirement facts the current design does not preserve."""
    elements = _page_elements(design, requirement_id)
    gaps = []
    for label in contract.get("labels", []):
        role, name = label["role"], label["name"]
        if not any((role is None or _element_role(node) == role) and name in _names(node)
                for node in elements):
            role_text = f"{role} " if role else ""
            gaps.append({"kind": "label", "role": role, "name": name,
                "message": f'no {role_text}element on the requirement pages declares the exact '
                    f'accessible name "{name}"'})
    for entry in contract.get("roles", []):
        role, name = entry["role"], entry["name"]
        if not _name_declared(elements, role, name):
            gaps.append({"kind": "role", "role": role, "name": name,
                "message": f'the requirement states "{name}" with role={role}, but no element on '
                    f'these pages exposes both that exact name and that role; give the element that '
                    f'name and that role (set prop.role when the element type implies another role)'})
    for field in contract.get("fields", []):
        if not any(field in _names(node) for node in elements):
            gaps.append({"kind": "field", "name": field,
                "message": f'no form element declares the required label or placeholder "{field}"'})
    for link in contract.get("links", []):
        name, href = link["name"], link["href"]
        if not any(node.get("type") == "link" and name in _names(node)
                and _prop(node).get("href") == href for node in elements):
            gaps.append({"kind": "link", "name": name, "href": href,
                "message": f'no link element declares both name "{name}" and href "{href}"'})
    linked_hrefs = {link["href"] for link in contract.get("links", [])}
    for href in contract.get("hrefs", []):
        if href in linked_hrefs:
            continue  # Already covered by the named-link check above.
        if not any(node.get("type") == "link" and _prop(node).get("href") == href for node in elements):
            gaps.append({"kind": "href", "href": href,
                "message": f'no link element declares href "{href}"'})
    if contract.get("alerts") and not any(
            node.get("type") == "alert" or _prop(node).get("role") == "alert" for node in elements):
        gaps.append({"kind": "alert",
            "message": "the requirement defines an error with role=alert, but no alert element is designed"})
    if contract.get("concealed"):
        for name in contract.get("password_fields", []):
            match = [node for node in elements if name in _names(node)]
            if match and not any(_prop(node).get("input_type") == "password" for node in match):
                gaps.append({"kind": "password_type", "name": name,
                    "message": f'field "{name}" must conceal typed characters, so its element needs '
                        f'input_type "password" rather than a styled text input'})
    if contract.get("routes") and created_pages:
        expected = set(contract["routes"])
        actual = {design["pages"][pid].get("route") for pid in created_pages if pid in design.get("pages", {})}
        if not expected & actual:
            routes = sorted(route for route in actual if isinstance(route, str))
            gaps.append({"kind": "route", "expected": sorted(expected), "actual": routes,
                "message": f'the page created by this proposal uses route(s) {routes}, '
                    f'but the requirement defines {sorted(expected)}'})
    gaps.extend(_duplicate_name_gaps(design))
    gaps.extend(_global_name_gaps(design, requirement_id))
    return gaps


def _global_name_gaps(design, requirement_id):
    """Report page controls that reuse a name the shared navigation already shows.

    The design states the persistent navigation once (on the root page) while
    every page renders it. A page-local control with the same role and name then
    matches that lookup twice on the page, exactly as a breadcrumb "Books" link
    did beside the navigation "Books" link.
    """
    from infrastructure.stages.frontend.ui.design import accessible_name, in_repeated_container
    from infrastructure.stages.frontend.ui.testing import global_navigation_names

    names, global_uids = global_navigation_names(design)
    if not names:
        return []
    owned = set(design.get("requirements", {}).get(requirement_id, []) or [])
    gaps, seen = [], set()
    for pid, page in design.get("pages", {}).items():
        if isinstance(page, dict) and page.get("route") == "/":
            continue  # the navigation lives here; it is the definition, not a copy
        for uid, node in page_elements(design, pid).items():
            if uid in global_uids or in_repeated_container(design, uid):
                continue
            if owned and uid not in owned:
                continue
            role, name = element_role(node), accessible_name(node)
            if (role, name) not in names or (pid, role, name) in seen:
                continue
            seen.add((pid, role, name))
            gaps.append({"kind": "global_name", "page": pid, "role": role, "name": name,
                "uid": uid,
                "message": f'page "{pid}" declares {role} "{name}", but the shared navigation '
                    f'already exposes that exact role and name on every page. A role-and-name '
                    f'lookup on this page would match both. Give this page-level control a '
                    f'distinguishing accessible name (for example "{name} breadcrumb") or remove it.'})
    return gaps


def _duplicate_name_gaps(design):
    """Report visible controls on one page that share a role+accessible name pair.

    Acceptance suites locate controls with an exact role+name query; two matches
    make that query ambiguous and fail every lookup, so duplicates outside
    list/table/item containers are a design-time gap.
    """
    from infrastructure.stages.frontend.ui.testing import display_states, duplicate_named_controls

    gaps, seen = [], set()
    for pid in design.get("pages", {}):
        try:
            _, _, states = display_states(design, pid)
        except (KeyError, TypeError, ValueError):
            continue
        for state in states:
            for duplicate in duplicate_named_controls(design, pid, state):
                key = (pid, duplicate["role"], duplicate["name"])
                if key in seen:
                    continue
                seen.add(key)
                gaps.append({"kind": "duplicate_name", "page": pid,
                    "role": duplicate["role"], "name": duplicate["name"],
                    "uids": duplicate["uids"], "message": duplicate["message"]})
    return gaps
