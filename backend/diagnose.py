"""CLI adapter for the VS Code extension.

Usage:
    python backend/diagnose.py --file /absolute/path/to/file.cpp

Reads the C++ source file, runs the full diagnostic pipeline, and writes a
single JSON object to stdout.  stderr is reserved for Python tracebacks only.

Exit codes:
    0 — always (the JSON payload carries status; the extension must not rely on
        the process exit code to determine success vs. compile error).

Output schema
-------------
On clean compilation:
    {"status": "clean"}

On one or more compiler errors (first error is analysed):
    {
        "status": "ok",
        "diagnosis": {
            "error_type":           str,   # e.g. "MISSING_SEMICOLON"
            "analysis_mode":        str,   # "deterministic" | "ai"
            "compiler_message":     str,
            "compiler_explanation": str,
            "source_explanation":   str,
            "evidence":             {"line": int | null, "code": str},
            "what_to_check":        str,
            "suggestion":           str
        }
    }

On infrastructure errors (GCC missing, timeout, unreadable file, …):
    {"status": "error", "message": str}
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running as  `python backend/diagnose.py`  from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compiler import (
    GCCNotFoundError,
    GCCTimeoutError,
    parse_compiler_output,
    run_compiler,
)
from backend.context import enrich_context
from backend.diagnostics import analyze_simple_errors
from backend.bob import analyze_with_bob


def _emit(payload: dict) -> None:
    """Write *payload* as a single JSON line to stdout and flush."""
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def diagnose(source_code: str) -> dict:
    """Run the full pipeline on *source_code* and return the result dict.

    Separated from I/O so tests can call it directly without spawning a process.
    """
    try:
        result = run_compiler(source_code)
    except GCCNotFoundError as exc:
        return {"status": "error", "message": str(exc)}
    except GCCTimeoutError as exc:
        return {"status": "error", "message": str(exc)}

    diagnostics = parse_compiler_output(result["stderr"])

    # Only errors are actionable; skip warnings and notes.
    errors = [d for d in diagnostics if d["severity"] in ("error", "fatal error")]

    if not errors:
        return {"status": "clean"}

    # Analyse the first error only — the VS Code prototype shows one at a time.
    first = errors[0]
    contextual = enrich_context(first, source_code)
    diagnosis = analyze_simple_errors(contextual) or analyze_with_bob(contextual)
    return {"status": "ok", "diagnosis": diagnosis}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose a C++ file and print a JSON result to stdout."
    )
    parser.add_argument("--file", required=True, help="Path to the C++ source file.")
    args = parser.parse_args()

    try:
        source_code = Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        _emit({"status": "error", "message": f"Cannot read file: {exc}"})
        return

    _emit(diagnose(source_code))


if __name__ == "__main__":
    main()
