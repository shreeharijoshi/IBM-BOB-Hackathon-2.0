"""Deterministic diagnostic engine for known simple C++ compiler errors."""

# Each rule: needle matched against the normalized compiler message,
# plus all unified-output fields hardcoded per error type.
_RULES = [
    {
        "needle": "expected ';'",
        "error_type": "MISSING_SEMICOLON",
        "compiler_explanation": (
            "The compiler reached a token it did not expect because a "
            "semicolon is missing at the end of the previous statement."
        ),
        "source_explanation": (
            "A statement on the line before the flagged location is not "
            "terminated with a semicolon (';')."
        ),
        "what_to_check": (
            "Look at the line immediately before the one the compiler flagged "
            "and confirm it ends with a semicolon."
        ),
        "suggestion": "Add a semicolon (';') at the end of the incomplete statement.",
    },
    {
        "needle": "expected ',' or ';'",
        "error_type": "MISSING_SEMICOLON",
        "compiler_explanation": (
            "The compiler reached a token it did not expect because a "
            "semicolon is missing at the end of the previous statement."
        ),
        "source_explanation": (
            "A statement on the line before the flagged location is not "
            "terminated with a semicolon (';')."
        ),
        "what_to_check": (
            "Look at the line immediately before the one the compiler flagged "
            "and confirm it ends with a semicolon."
        ),
        "suggestion": "Add a semicolon (';') at the end of the incomplete statement.",
    },
    {
        "needle": "was not declared in this scope",
        "error_type": "UNDEFINED_VARIABLE",
        "compiler_explanation": (
            "The compiler could not find a declaration for this identifier "
            "in the current or any enclosing scope."
        ),
        "source_explanation": (
            "A variable or function name is used before it has been declared, "
            "or it is misspelled."
        ),
        "what_to_check": (
            "Verify the identifier is spelled correctly, that it is declared "
            "before use, and that it is visible in the current scope."
        ),
        "suggestion": (
            "Declare the variable before using it, or fix the spelling if it "
            "is a typo."
        ),
    },
    {
        "needle": "cannot convert",
        "error_type": "TYPE_MISMATCH",
        "compiler_explanation": (
            "The compiler cannot implicitly convert the value on the right-hand "
            "side to the type required on the left-hand side."
        ),
        "source_explanation": (
            "A value of one type (e.g. a string literal) is being assigned or "
            "passed where a different, incompatible type is expected (e.g. int)."
        ),
        "what_to_check": (
            "Check that both sides of the assignment or the function argument "
            "and parameter agree on their types."
        ),
        "suggestion": (
            "Use a value of the correct type, or add an explicit cast if the "
            "conversion is intentional."
        ),
    },
    {
        "needle": "invalid conversion",
        "error_type": "TYPE_MISMATCH",
        "compiler_explanation": (
            "The compiler detected an invalid implicit type conversion."
        ),
        "source_explanation": (
            "A value is being converted to an incompatible type without an "
            "explicit cast."
        ),
        "what_to_check": (
            "Check the types on both sides of the assignment or in the function "
            "call arguments."
        ),
        "suggestion": (
            "Use a value of the correct type, or add an explicit cast if the "
            "conversion is intentional."
        ),
    },
    {
        "needle": "no matching function",
        "error_type": "WRONG_ARGUMENTS",
        "compiler_explanation": (
            "The compiler could not find an overload of the function that "
            "accepts the argument types or count provided."
        ),
        "source_explanation": (
            "The function is called with the wrong number of arguments or "
            "with arguments of types that do not match any declared overload."
        ),
        "what_to_check": (
            "Compare the call site with the function declaration: check the "
            "number of arguments and their types."
        ),
        "suggestion": (
            "Adjust the call to match the function signature — ensure the "
            "correct number and types of arguments are passed."
        ),
    },
    {
        "needle": "too few arguments to function",
        "error_type": "WRONG_ARGUMENTS",
        "compiler_explanation": (
            "The function call provides fewer arguments than the function "
            "declaration requires."
        ),
        "source_explanation": (
            "The function is called with too few arguments. One or more "
            "required parameters have not been supplied."
        ),
        "what_to_check": (
            "Compare the call site with the function declaration: count the "
            "number of parameters and ensure all required ones are provided."
        ),
        "suggestion": (
            "Add the missing argument(s) to the function call to match the "
            "function signature."
        ),
    },
    {
        "needle": "too many arguments to function",
        "error_type": "WRONG_ARGUMENTS",
        "compiler_explanation": (
            "The function call provides more arguments than the function "
            "declaration accepts."
        ),
        "source_explanation": (
            "The function is called with too many arguments. Extra values are "
            "being passed that the function does not have parameters for."
        ),
        "what_to_check": (
            "Compare the call site with the function declaration: count the "
            "number of parameters and remove the extra argument(s)."
        ),
        "suggestion": (
            "Remove the extra argument(s) from the function call to match the "
            "function signature."
        ),
    },
    {
        "needle": "is not a member of 'std'",
        "error_type": "MISSING_INCLUDE",
        "compiler_explanation": (
            "The compiler does not recognise the identifier as a member of the "
            "'std' namespace because the required standard-library header has "
            "not been included."
        ),
        "source_explanation": (
            "A standard-library symbol such as 'std::cout' or 'std::string' is "
            "used without the corresponding #include directive at the top of the "
            "file."
        ),
        "what_to_check": (
            "Check that the required #include (e.g. #include <iostream>) is "
            "present at the top of the file."
        ),
        "suggestion": (
            "Add the missing #include directive for the standard-library header "
            "that defines the symbol you are using."
        ),
    },
]


def _extract_evidence(contextual_diagnostic: dict) -> dict:
    """Extract the most relevant evidence line from the diagnostic dict.

    Prefers the source_context entry whose 'line' matches the diagnostic's
    reported line number. Falls back to the first available context entry,
    then to None/empty if nothing is present.
    """
    diag_line = contextual_diagnostic.get("line")
    source_context = contextual_diagnostic.get("source_context", [])

    if diag_line is not None and source_context:
        for entry in source_context:
            if entry.get("line") == diag_line:
                return {"line": entry["line"], "code": entry.get("code", "")}

    # Fall back to first available context entry
    if source_context:
        first = source_context[0]
        return {"line": first.get("line"), "code": first.get("code", "")}

    # No context available — return what we have from the top-level fields
    return {"line": diag_line, "code": ""}


def analyze_simple_errors(contextual_diagnostic: dict) -> dict | None:
    """Match the compiler message against known error rules.

    Returns a unified diagnosis dict when a rule matches, or None if the
    error is not recognised.
    """
    text = (
        contextual_diagnostic.get("normalized")
        or contextual_diagnostic.get("message")
        or ""
    )

    for rule in _RULES:
        if rule["needle"] in text:
            evidence = _extract_evidence(contextual_diagnostic)
            return {
                "error_type": rule["error_type"],
                "analysis_mode": "deterministic",
                "compiler_message": text,
                "compiler_explanation": rule["compiler_explanation"],
                "source_explanation": rule["source_explanation"],
                "evidence": evidence,
                "what_to_check": rule["what_to_check"],
                "suggestion": rule["suggestion"],
            }

    return None
