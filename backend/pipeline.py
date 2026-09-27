"""Shared diagnostic pipeline helpers.

Both the HTTP endpoint (main.py) and the CLI adapter (diagnose.py) run
identical per-diagnostic analysis.  This module owns that shared logic so
neither caller duplicates it.

Public API
----------
analyse_one(error, source_code) -> dict
    Run the full context + rule pipeline on a single parsed compiler
    diagnostic and return the unified diagnosis dict (with 'raw' attached).

build_result(errors, source_code) -> dict
    Filter a list of parsed diagnostics down to real errors, run
    ``analyse_one`` on every one, and return the shaped result dict
    (``status``, ``diagnosis``, ``diagnostics``).  Returns
    ``{"status": "clean"}`` when no errors are present.
"""

from backend.bob import analyze_with_bob
from backend.context import enrich_context
from backend.diagnostics import analyze_simple_errors


def analyse_one(error: dict, source_code: str) -> dict:
    """Run the full context + analysis pipeline on a single diagnostic.

    Attaches a 'raw' field with the original compiler diagnostic fields
    (file, line, column, severity, message).
    """
    contextual = enrich_context(error, source_code)
    diagnosis = analyze_simple_errors(contextual) or analyze_with_bob(contextual)
    diagnosis["raw"] = {
        "file": error.get("file", ""),
        "line": error.get("line"),
        "column": error.get("column"),
        "severity": error.get("severity", "error"),
        "message": error.get("message", ""),
    }
    return diagnosis


def build_result(errors: list, source_code: str) -> dict:
    """Build the standard result dict from a list of compiler error dicts.

    Only entries whose severity is 'error' or 'fatal error' are processed;
    callers should pre-filter, but this function does not require it.

    Returns ``{"status": "clean"}`` when *errors* is empty.
    """
    if not errors:
        return {"status": "clean"}

    all_diagnoses = [analyse_one(err, source_code) for err in errors]
    return {
        "status": "ok",
        "diagnosis": all_diagnoses[0],   # first error — backward compat
        "diagnostics": all_diagnoses,    # all errors — full multi-error support
    }
