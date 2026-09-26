"""Bob/AI fallback stub for complex or unrecognised compiler errors."""


def analyze_with_bob(contextual_diagnostic: dict) -> dict:
    """Return a stubbed unified diagnosis for errors not handled deterministically.

    Uses the same safe evidence-extraction approach as diagnostics.py:
    prefer the source_context entry whose line matches the reported line,
    fall back to the first context entry, then to bare line/empty code.
    """
    compiler_message = (
        contextual_diagnostic.get("normalized")
        or contextual_diagnostic.get("message")
        or ""
    )

    diag_line = contextual_diagnostic.get("line")
    source_context = contextual_diagnostic.get("source_context", [])

    evidence = {"line": diag_line, "code": ""}
    if diag_line is not None and source_context:
        for entry in source_context:
            if entry.get("line") == diag_line:
                evidence = {"line": entry["line"], "code": entry.get("code", "")}
                break
        else:
            first = source_context[0]
            evidence = {"line": first.get("line"), "code": first.get("code", "")}
    elif source_context:
        first = source_context[0]
        evidence = {"line": first.get("line"), "code": first.get("code", "")}

    return {
        "error_type": "UNKNOWN",
        "analysis_mode": "ai",
        "compiler_message": compiler_message,
        "compiler_explanation": (
            "This error could not be matched to a known pattern and requires "
            "deeper analysis."
        ),
        "source_explanation": (
            "Bob AI analysis is pending for this error type. The error may "
            "involve complex type interactions, templates, or project-specific "
            "code patterns."
        ),
        "evidence": evidence,
        "what_to_check": (
            "Review the flagged line and its surrounding context carefully. "
            "Check for mismatched types, incorrect scoping, or missing "
            "definitions."
        ),
        "suggestion": (
            "Consult the compiler documentation for this error code, or ask "
            "Bob for further guidance once AI integration is enabled."
        ),
    }
