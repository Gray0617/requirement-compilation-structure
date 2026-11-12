"""Frontend triggers and database effects derived from their API actions."""

from copy import deepcopy

from infrastructure.stages.frontend.ui.design import page_context, page_elements
from infrastructure.stages.database.obligations.rules import union


def empty_triggers(tree):
    return {"triggers": {}, "requirements": {rid: [] for rid in tree.requirements},
        "steps": {sid: [] for sid in tree.steps}}


def relevant_pages(ui, rid):
    selected = set(ui["requirements"].get(rid, []))
    return {pid: page_context(ui, pid) for pid in ui["pages"] if selected.intersection(page_elements(ui, pid))}


def api_ids(trigger):
    return {action["api"] for action in _actions(trigger) if action.get("kind") == "api"}


def _actions(trigger):
    """Return the trigger's action list, tolerating a single object or a missing key."""
    action = trigger.get("action")
    if isinstance(action, dict):
        return [action]
    if not isinstance(action, list):
        return []
    return [step for step in action if isinstance(step, dict)]


def normalize_proposal(proposal):
    """Accept near-miss record shapes instead of failing the whole requirement.

    Models sometimes return ``triggers`` as a list of records with an ``id``, or a
    single ``action`` object where a list is documented. Both carry the same
    information, so they are reshaped rather than rejected.
    """
    if not isinstance(proposal, dict):
        raise ValueError("Trigger output must be a JSON object with a triggers field")
    triggers = proposal.get("triggers")
    if isinstance(triggers, list):
        keyed = {}
        for record in triggers:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                raise ValueError("Each trigger in a list must be an object with a string id")
            keyed[record["id"]] = {key: value for key, value in record.items() if key != "id"}
        triggers = keyed
    elif not isinstance(triggers, dict):
        raise ValueError("triggers must be an object mapping trigger IDs to records")
    normalized = {}
    for tid, record in triggers.items():
        if not isinstance(record, dict):
            raise ValueError(f"Trigger {tid} must be an object")
        record = dict(record)
        action = record.get("action")
        if isinstance(action, dict):
            record["action"] = [action]
        elif isinstance(action, list):
            if any(not isinstance(step, dict) for step in action):
                raise ValueError(f"Each action of trigger {tid} must be an object, for example "
                    '{"kind": "api", "api": "API-X", "description": "..."}')
            record["action"] = action
        elif action is not None:
            raise ValueError(f"Trigger {tid} action must be a list of action objects")
        else:
            record["action"] = []
        normalized[tid] = record
    requirement_triggers = proposal.get("requirement_triggers")
    if isinstance(requirement_triggers, dict):
        requirement_triggers = list(requirement_triggers)
    if not isinstance(requirement_triggers, list):
        raise ValueError("requirement_triggers must list the trigger IDs of this requirement")
    step_triggers = proposal.get("step_triggers") or {}
    if not isinstance(step_triggers, dict):
        raise ValueError("step_triggers must map step IDs to trigger ID lists")
    return {"triggers": normalized, "requirement_triggers": requirement_triggers,
        "step_triggers": step_triggers}


def trigger_obligations(trigger, backend):
    """Compute DB R/W for implementation context, without frontend coverage gates."""
    return union(backend["apis"][aid] for aid in api_ids(trigger))


def apply_decomposition(current, proposal, rid):
    proposal = normalize_proposal(proposal)
    updated = deepcopy(current)
    updated["triggers"].update(deepcopy(proposal["triggers"]))
    updated["requirements"][rid] = proposal["requirement_triggers"]
    updated["steps"].update(deepcopy(proposal["step_triggers"]))
    return updated


def validate_coverage(design, backend, obligations, tree, requirement_ids):
    """Compare declared calls with required DB effects, not source-code behavior.

    Step associations must be explicit. A step's calls may include conditional API
    branches with broader possible effects (e.g. a rejected registration), so only
    missing step effects are errors. Requirement reads are minimum capabilities;
    requirement writes retain exact coverage of their possible effects.
    """
    from infrastructure.stages.backend.primitives import ObligationCoverageError, coverage
    from infrastructure.stages.database.obligations.rules import difference
    for rid in requirement_ids:
        effects = [trigger_obligations(design['triggers'][tid], backend)
            for tid in design['requirements'][rid]]
        required = obligations['requirements'][rid]
        gaps = coverage(required, effects)
        if any(values for gap in gaps.values() for values in gap.values()):
            raise ObligationCoverageError(rid, required, union(effects), gaps)
        for sid in tree.own_steps[rid]:
            effects = [trigger_obligations(design['triggers'][tid], backend)
                for tid in design['steps'][sid]]
            required = obligations['steps'][sid]
            missing = difference(required, union(effects))
            if any(missing.values()):
                raise ObligationCoverageError(sid, required, union(effects), {'missing': missing})
