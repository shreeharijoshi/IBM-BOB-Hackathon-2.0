"""Generic fallback analysis for compiler errors not matched by deterministic rules.

Works without any API key or external service. Provides a helpful, structured
generic explanation based on the raw compiler message text.
"""

import re


# Map of common GCC/Clang message fragments to friendly category hints
_HINT_PATTERNS = [
    (re.compile(r"template", re.I), "This looks like a template-related error."),
    (re.compile(r"namespace", re.I), "This may involve a namespace or missing #include."),
    (re.compile(r"overload", re.I), "This may be an overload resolution failure."),
    (re.compile(r"virtual", re.I), "This may involve a virtual function or polymorphism."),
    (re.compile(r"const", re.I), "This may involve a const correctness issue."),
    (re.compile(r"reference|lvalue|rvalue", re.I), "This may involve a reference or value category issue."),
    (re.compile(r"pointer|dereference", re.I), "This may involve a pointer operation."),
    (re.compile(r"cast|conversion|convert", re.I), "This may involve a type conversion."),
    (re.compile(r"inherit|base class|derived", re.I), "This may involve inheritance."),
    (re.compile(r"static|extern", re.I), "This may involve storage class or linkage."),
    (re.compile(r"decltype|auto", re.I), "This may involve type deduction."),
]


def _build_hint(message: str) -> str:
    for pattern, hint in _HINT_PATTERNS:
        if pattern.search(message):
            return hint
    return "Review the compiler message carefully for specific clues."


def analyze_with_bob(contextual_diagnostic: dict) -> dict:
    """Return a structured generic diagnosis for errors not handled deterministically.

    Never raises or returns None. Always returns a complete unified schema dict.
    Works entirely without API keys or external services.
    """
    compiler_message = (
        contextual_diagnostic.get("normalized")
        or contextual_diagnostic.get("message")
        or ""
    )

    diag_line = contextual_diagnostic.get("line")
    source_context = contextual_diagnostic.get("source_context", [])

    # Build evidence: start with safe-empty using the compiler's reported line.
    # If the source context contains an exact line-number match, use its code.
    # Never fall back to an unrelated context entry (e.g. source_context[0])
    # when the diagnostic line is absent from the window — that would replace
    # the correct compiler line with whatever line happens to be first in the
    # context window, which is the root cause of the 3 → 5 corruption.
    evidence: dict = {"line": diag_line, "code": ""}
    if diag_line is not None and source_context:
        for entry in source_context:
            if entry.get("line") == diag_line:
                evidence = {"line": entry["line"], "code": entry.get("code", "")}
                break
        # No else: if diag_line not found in context, evidence stays {"line": diag_line, "code": ""}

    # When context is absent and source_line_count is known, flag OOB mismatches.
    source_line_count = contextual_diagnostic.get("source_line_count")
    if (
        evidence["code"] == ""
        and diag_line is not None
        and source_line_count is not None
        and diag_line > source_line_count
    ):
        evidence["line_mismatch"] = True

    hint = _build_hint(compiler_message)

    return {
        "error_type": "UNKNOWN",
        "analysis_mode": "ai",
        "compiler_message": compiler_message,
        "compiler_explanation": (
            f"This error was not matched by a specific rule. {hint} "
            "Read the full compiler message above for exact details."
        ),
        "source_explanation": (
            "The compiler found an issue in your code that requires closer inspection. "
            "Check the flagged line and its context carefully."
        ),
        "evidence": evidence,
        "what_to_check": (
            "Review the flagged line and its surrounding context carefully. "
            "Check for mismatched types, incorrect scoping, missing definitions, "
            "or unsatisfied template constraints."
        ),
        "suggestion": (
            "Read the full compiler message carefully — it often contains the specific "
            "identifier, type, or location involved. Search for the error message online "
            "or consult the C++ reference for the relevant feature."
        ),
    }
