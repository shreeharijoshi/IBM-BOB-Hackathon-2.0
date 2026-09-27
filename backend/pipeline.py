"""Shared diagnostic pipeline helpers.

Both the HTTP endpoint (main.py) and the CLI adapter (diagnose.py) run
identical per-diagnostic analysis. This module owns that shared logic so
neither caller duplicates it.

Public API
----------
analyse_one(error, source_code, gemini_config=None) -> dict
    Run the full context + location-resolution + rule/Gemini pipeline on a single
    parsed compiler diagnostic and return the unified diagnosis dict.

build_result(errors, source_code, gemini_config=None) -> dict
    Filter a list of parsed diagnostics down to real errors, eliminate duplicate
    and cascade noise, run ``analyse_one`` on every error, and return the shaped
    result dict (``status``, ``diagnosis``, ``diagnostics``).
"""

from __future__ import annotations

import re
from typing import Any

from backend.bob import analyze_with_bob
from backend.context import enrich_context
from backend.diagnostics import analyze_simple_errors
from backend.gemini import analyze_compiler_error_with_gemini
from backend.location import resolve_root_cause


# Patterns commonly produced as follow-up cascade noise after syntax errors
_CASCADE_PATTERNS = [
    re.compile(r"expected unqualified-id before", re.I),
    re.compile(r"expected primary-expression before", re.I),
    re.compile(r"expected constructor, destructor, or type conversion", re.I),
]


def _is_cascade_noise(err: dict, previous_errors: list[dict]) -> bool:
    """Return True if err is likely an unhelpful syntax-cascade artifact of a previous error."""
    msg = err.get("message", "")
    line = err.get("line")
    if line is None:
        return False

    for prev in previous_errors:
        prev_line = prev.get("line")
        prev_msg = prev.get("message", "")
        # If previous error was a missing semicolon or syntax error on or right before this line
        if prev_line is not None and abs(line - prev_line) <= 1:
            if "expected ';'" in prev_msg or "expected ',' or ';'" in prev_msg:
                if any(p.search(msg) for p in _CASCADE_PATTERNS):
                    return True
    return False


def deduplicate_and_filter_errors(errors: list[dict]) -> list[dict]:
    """Filter out non-errors, exact duplicates, and obvious cascade artifacts."""
    real_errors = [
        d for d in errors if d.get("severity") in ("error", "fatal error")
    ]
    if not real_errors:
        return []

    filtered: list[dict] = []
    seen_signatures: set[tuple[str, int | None, int | None, str]] = set()

    for err in real_errors:
        file_path = str(err.get("file", ""))
        line = err.get("line")
        col = err.get("column")
        msg = err.get("message", "").strip()

        sig = (file_path, line, col, msg)
        if sig in seen_signatures:
            continue

        if _is_cascade_noise(err, filtered):
            continue

        seen_signatures.add(sig)
        filtered.append(err)

    return filtered or real_errors


def analyse_one(
    error: dict,
    source_code: str,
    gemini_config: dict[str, Any] | None = None,
    prefer_gemini: bool = False,
) -> dict[str, Any]:
    """Run context extraction, location resolution, and analysis on a single diagnostic.

    Attaches:
    - compiler_location: location reported by GCC
    - root_cause_location: accurate location of the actual mistake in source
    - confidence: float score
    - explanation: unified text explanation
    - raw: original compiler diagnostic fields for backward compatibility
    """
    contextual = enrich_context(error, source_code)

    diagnosis = None
    should_use_gemini_first = prefer_gemini or bool(
        gemini_config and gemini_config.get("prefer_gemini")
    )

    # 1. When Gemini is preferred and configured, generate Gemini-driven answers
    if should_use_gemini_first:
        diagnosis = analyze_compiler_error_with_gemini(
            contextual, config=gemini_config
        )
        if diagnosis:
            # Enrich with deterministic error_type if known
            deterministic_match = analyze_simple_errors(contextual)
            if deterministic_match and diagnosis.get("error_type") in ("COMPLEX_ERROR", "UNKNOWN"):
                diagnosis["error_type"] = deterministic_match["error_type"]

    # 2. Normal fallthrough: Deterministic rules (runs first when Gemini not preferred/available)
    if diagnosis is None:
        diagnosis = analyze_simple_errors(contextual)

    # 3. Gemini fallback for complex or unknown errors (if enabled/configured)
    if diagnosis is None:
        diagnosis = analyze_compiler_error_with_gemini(
            contextual, config=gemini_config
        )

    # 4. Offline Bob generic fallback (guaranteed to never fail or need keys)
    if diagnosis is None:
        diagnosis = analyze_with_bob(contextual)

    error_type = diagnosis.get("error_type", "UNKNOWN")
    analysis_mode = diagnosis.get("analysis_mode", "deterministic")

    # Resolve accurate root cause location based on compiler message and error type
    root_cause = resolve_root_cause(
        contextual,
        error_type=error_type,
        source_code=source_code,
    )

    compiler_location = {
        "file": error.get("file", ""),
        "line": error.get("line"),
        "column": error.get("column"),
    }

    confidence = diagnosis.get(
        "confidence",
        1.0 if analysis_mode == "deterministic" else 0.85,
    )

    explanation = (
        diagnosis.get("explanation")
        or diagnosis.get("source_explanation")
        or diagnosis.get("compiler_explanation")
        or ""
    )

    # Attach all unified schema keys
    diagnosis["compiler_location"] = compiler_location
    diagnosis["root_cause_location"] = root_cause
    diagnosis["confidence"] = confidence
    diagnosis["explanation"] = explanation

    diagnosis["raw"] = {
        "file": error.get("file", ""),
        "line": error.get("line"),
        "column": error.get("column"),
        "severity": error.get("severity", "error"),
        "message": error.get("message", ""),
    }

    return diagnosis


def build_result(
    errors: list[dict],
    source_code: str,
    gemini_config: dict[str, Any] | None = None,
    prefer_gemini: bool = False,
) -> dict[str, Any]:
    """Build the standard result dict from a list of compiler error dicts.

    Filters real errors, deduplicates, runs ``analyse_one`` on every error,
    and returns ``{"status": "ok", "diagnosis": ..., "diagnostics": [...]}``.
    Returns ``{"status": "clean"}`` when *errors* is empty.
    """
    filtered = deduplicate_and_filter_errors(errors)
    if not filtered:
        return {"status": "clean"}

    all_diagnoses = [
        analyse_one(
            err,
            source_code,
            gemini_config=gemini_config,
            prefer_gemini=prefer_gemini,
        )
        for err in filtered
    ]

    # Additional deduplication on root_cause_location to avoid redundant highlights
    unique_diagnoses: list[dict[str, Any]] = []
    seen_locations: set[tuple[int | None, int | None, str]] = set()

    for d in all_diagnoses:
        rc = d.get("root_cause_location", {})
        loc_key = (rc.get("line"), rc.get("column"), d.get("error_type", ""))
        if loc_key in seen_locations and rc.get("line") is not None:
            continue
        seen_locations.add(loc_key)
        unique_diagnoses.append(d)

    final_list = unique_diagnoses or all_diagnoses

    return {
        "status": "ok",
        "diagnosis": final_list[0],
        "diagnostics": final_list,
    }
