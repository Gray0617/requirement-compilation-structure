"""Speed-first compiler switches shared by every pipeline stage.

The competition runner sets ARCBENCH_OUTPUT_DIR/ARCBENCH_TASK_DIR, so uploaded
agents default to FAST_MODE. Local runs and unit tests keep the quality path
unless FAST_MODE=1 or --fast is passed. Every limit is configurable so a slow
task can be tightened without another code change.
"""

import os


def _flag(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    return value.strip().lower() not in {"0", "false", "no", "off", "quality"}


def fast_mode() -> bool:
    """Return True when the compiler should trade verification depth for speed."""
    configured = _flag(os.getenv("FAST_MODE"))
    if configured is not None:
        return configured
    # The ARC-Bench runner always injects these paths. Local invocations do not.
    return bool(os.getenv("ARCBENCH_OUTPUT_DIR", "").strip()
        or os.getenv("ARCBENCH_TASK_DIR", "").strip()
        or os.getenv("ARCBENCH_PROJECT_DIR", "").strip())


def _positive(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def clamp_turns(default: int) -> int:
    """Limit edit rounds in fast mode; keep the caller's budget otherwise."""
    return min(default, _positive("FAST_MAX_TURNS", 3)) if fast_mode() else default


def clamp_attempts(default: int) -> int:
    """Limit design-proposal attempts in fast mode."""
    return min(default, _positive("FAST_MAX_ATTEMPTS", 3)) if fast_mode() else default


def clamp_tests(default: int) -> int:
    """Limit generated E2E cases per requirement in fast mode."""
    return min(default, _positive("FAST_MAX_TESTS", 1)) if fast_mode() else default


def ultra_fast_mode(leaf_count: int | None = None) -> bool:
    """One-shot compilation for small tasks; disabled outside fast/competition runs."""
    configured = _flag(os.getenv("ULTRA_FAST"))
    if configured is not None:
        return configured
    if not fast_mode():
        return False
    return leaf_count is None or leaf_count <= _positive("ULTRA_FAST_MAX_LEAVES", 100)


def skip_independent_acceptance() -> bool:
    """The independent acceptance authoring loop is the largest model cost."""
    return fast_mode()


def skip_generated_tests() -> bool:
    """Fast mode implements code directly instead of authoring tests first."""
    return fast_mode()


def parallel_workers() -> int:
    """Bounded concurrency for independent model calls."""
    return _positive("FAST_PARALLEL_WORKERS", 4)


def model_timeout_seconds() -> int:
    """Do not let one stalled model call hold a stage for ten minutes."""
    return _positive("FAST_MODEL_TIMEOUT_SECONDS", 120) if fast_mode() else 600
