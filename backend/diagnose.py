"""CLI adapter for the VS Code extension.

Usage:
    python backend/diagnose.py --file /absolute/path/to/file.cpp
    python backend/diagnose.py --file /path/to/file.cpp --check-code

Reads the C++ source file, runs the diagnostic pipeline or Gemini code review,
and writes a single JSON object to stdout. stderr is reserved for Python tracebacks only.

Exit codes:
    0 — always (the JSON payload carries status; the extension must not rely on
        the process exit code to determine success vs. compile error).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow running as `python backend/diagnose.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compiler import (
    GCCNotFoundError,
    GCCTimeoutError,
    parse_compiler_output,
    run_compiler,
)
from backend.gemini import check_code_with_gemini, get_gemini_config
from backend.pipeline import build_result


def _emit(payload: dict) -> None:
    """Write *payload* as a single JSON line to stdout and flush."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def diagnose(
    source_code: str,
    gemini_config: dict[str, Any] | None = None,
    prefer_gemini: bool = False,
) -> dict:
    """Run the full pipeline on *source_code* and return the result dict.

    Returns deep analysis for ALL compiler errors, with accurate root-cause
    location resolution and optional Gemini AI fallback.
    """
    try:
        result = run_compiler(source_code)
    except GCCNotFoundError as exc:
        return {"status": "error", "message": str(exc)}
    except GCCTimeoutError as exc:
        return {"status": "error", "message": str(exc)}

    parsed = parse_compiler_output(result["stderr"])
    errors = [d for d in parsed if d["severity"] in ("error", "fatal error")]

    return build_result(
        errors,
        source_code,
        gemini_config=gemini_config,
        prefer_gemini=prefer_gemini,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose a C++ file and print a JSON result to stdout."
    )
    parser.add_argument("--file", required=True, help="Path to the C++ source file.")
    parser.add_argument(
        "--check-code",
        action="store_true",
        help="Run Gemini AI code review on the file or selection.",
    )
    parser.add_argument(
        "--prefer-gemini",
        action="store_true",
        help="Use Gemini AI for all diagnostic explanations when key is configured.",
    )
    parser.add_argument(
        "--selection-start",
        type=int,
        default=None,
        help="1-based start line of selected code.",
    )
    parser.add_argument(
        "--selection-end",
        type=int,
        default=None,
        help="1-based end line of selected code.",
    )
    parser.add_argument("--gemini-key", default=None, help="Gemini API key override.")
    parser.add_argument("--gemini-model", default=None, help="Gemini model override.")
    parser.add_argument(
        "--gemini-timeout", type=float, default=None, help="Gemini timeout in seconds."
    )
    parser.add_argument(
        "--gemini-enabled",
        type=lambda x: str(x).lower() in ("true", "1", "yes"),
        default=None,
        help="Enable or disable Gemini.",
    )

    args = parser.parse_args()

    try:
        source_code = Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        _emit({"status": "error", "message": f"Cannot read file: {exc}"})
        return

    gemini_cfg = get_gemini_config(
        override_key=args.gemini_key,
        override_model=args.gemini_model,
        override_timeout=args.gemini_timeout,
        override_enabled=args.gemini_enabled,
    )

    if args.check_code:
        sel_range = None
        if args.selection_start is not None and args.selection_end is not None:
            sel_range = {
                "start_line": args.selection_start,
                "end_line": args.selection_end,
            }
        review = check_code_with_gemini(
            source_code=source_code,
            selection_range=sel_range,
            file_path=args.file,
            config=gemini_cfg,
        )
        _emit(review)
        return

    _emit(diagnose(source_code, gemini_config=gemini_cfg, prefer_gemini=args.prefer_gemini))


if __name__ == "__main__":
    main()
