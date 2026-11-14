"""Compile a requirement directory into an executable application."""

import argparse
import os
from pathlib import Path
import sys

from infrastructure.compiler import Compiler, PIPELINE
from infrastructure.utils.cli import resolve_directories
from infrastructure.utils.presentation.logging import configure_console
from infrastructure.utils.presentation.stages import stage_label


def platform_default(value: Path | None, *names: str) -> Path | None:
    """Fall back to the runner-provided directory variables when no argument is given."""
    if value is not None:
        return value
    for name in names:
        candidate = os.environ.get(name, "").strip()
        if candidate:
            return Path(candidate)
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read public options and validate paths and the backend port before compilation."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Stages:\n" + "\n".join(
            "  " + stage_label(name) + f"  (--stage {name})" for name, _, _ in PIPELINE))
    parser.add_argument("requirement_path", type=Path, nargs="?",
        help="Requirement directory containing requirements.yaml and reference assets; "
            "defaults to ARCBENCH_TASK_DIR/ARCBENCH_REQUIREMENT_DIR")
    parser.add_argument("-o", "--output-dir", type=Path,
        help="Directory for the generated application and .arc artifacts; "
            "defaults to ARCBENCH_OUTPUT_DIR/ARCBENCH_PROJECT_DIR")
    parser.add_argument("--type", "--app-type", dest="app_type",
        default=os.environ.get("ARCBENCH_TASK_TYPE", "web").strip() or "web",
        choices=["web", "cli", "android"],
        help="Application type reported by the runner; this compiler only builds web applications")
    parser.add_argument("--model-log", "--debug-model-io", dest="model_log", action="store_true",
        help="Show detailed model I/O and save per-call prompt/response logs; usage and timing are always shown")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "3000") or 3000),
        help="Default generated backend port and frontend API proxy target (default: PORT or 3000)")
    parser.add_argument("--stage", choices=[name for name, _, _ in PIPELINE], metavar="STAGE",
        help="Run one stage by its full name (e.g. 06_implement_backend_tdd); omit for the full pipeline")
    parser.add_argument("--pause", action="store_true", help="Wait for Enter after each display stage")
    parser.add_argument("--fast", action="store_true",
        help="Speed-first mode: fewer rounds, no generated tests or independent acceptance")
    parser.add_argument("--quality", action="store_true",
        help="Full verification mode; disables the speed-first shortcuts")
    parser.add_argument("--ultra-fast", action="store_true",
        help="One-shot generation for small tasks; skips staged design and local tests")
    args = parser.parse_args(argv)
    if sum((args.fast, args.quality, args.ultra_fast)) > 1:
        parser.error("Choose only one of --fast, --ultra-fast or --quality")
    if args.ultra_fast:
        os.environ["FAST_MODE"] = "1"
        os.environ["ULTRA_FAST"] = "1"
    elif args.fast:
        os.environ["FAST_MODE"] = "1"
    elif args.quality:
        os.environ["FAST_MODE"] = "0"
        os.environ["ULTRA_FAST"] = "0"
    args.requirement_path = platform_default(args.requirement_path,
        "ARCBENCH_TASK_DIR", "ARCBENCH_REQUIREMENT_DIR")
    args.output_dir = platform_default(args.output_dir, "ARCBENCH_OUTPUT_DIR", "ARCBENCH_PROJECT_DIR")
    try:
        if args.app_type != "web":
            raise ValueError(f"This compiler only builds web applications, not {args.app_type}")
        if not 1 <= args.port <= 65535:
            raise ValueError("--port must be between 1 and 65535")
        if args.requirement_path is None:
            raise ValueError("Provide the requirement directory, or set ARCBENCH_TASK_DIR")
        if args.output_dir is None:
            raise ValueError("Provide --output-dir, or set ARCBENCH_OUTPUT_DIR")
        args.requirement_path, args.output_dir = resolve_directories(
            argparse.Namespace(input=args.requirement_path, output=args.output_dir))
        if not args.requirement_path.is_dir():
            raise ValueError(f"Input directory does not exist: {args.requirement_path}")
        if not (args.requirement_path / "requirements.yaml").is_file():
            raise ValueError(f"Missing requirement document: {args.requirement_path / 'requirements.yaml'}")
        if args.output_dir.exists() and not args.output_dir.is_dir():
            raise ValueError(f"Output must be a directory: {args.output_dir}")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return args


def main(argv: list[str] | None = None) -> None:
    configure_console()
    args = parse_args(argv)
    compiler = Compiler(args.requirement_path, args.output_dir,
        model_log=args.model_log, port=args.port, pause=args.pause)
    try:
        compiler.compile(stage=args.stage)
    except KeyboardInterrupt:
        print(f"Compilation interrupted at {compiler.current_stage or 'startup'}.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Compilation failed at {compiler.current_stage or 'startup'}: {error}", file=sys.stderr)
        raise SystemExit(0)
    if compiler.report["status"] == "COMPLETED_WITH_FAILURES":
        print(f"All passes completed. Report: {compiler.arc_dir / 'compilation.json'}", file=sys.stderr)
        raise SystemExit(0)


if __name__ == "__main__":
    main()
