"""Tests for multiple diagnostic handling, deduplication, and cascade noise suppression.

Verifies:
- All real independent compiler errors are preserved and deeply analysed.
- Identical duplicate compiler errors are filtered out.
- Obvious syntax cascade noise (unqualified-id, expected primary-expression) following
  a missing semicolon is suppressed.
- Root cause location deduplication prevents duplicate editor squiggles.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.pipeline import (
    build_result,
    deduplicate_and_filter_errors,
)


def test_independent_multiple_errors_preserved():
    """Independent errors across different lines must all be analysed."""
    errors = [
        {
            "file": "main.cpp",
            "line": 3,
            "column": 5,
            "severity": "error",
            "message": "cannot convert 'const char*' to 'int'",
        },
        {
            "file": "main.cpp",
            "line": 4,
            "column": 12,
            "severity": "error",
            "message": "no matching function for call to 'add(int)'",
        },
    ]
    source = (
        "int add(int a, int b) { return a + b; }\n"
        "int main() {\n"
        '    int x = "hello";\n'
        "    return add(1);\n"
        "}\n"
    )

    res = build_result(errors, source)
    assert res["status"] == "ok"
    assert len(res["diagnostics"]) == 2

    types = [d["error_type"] for d in res["diagnostics"]]
    assert "TYPE_MISMATCH" in types
    assert "WRONG_ARGUMENTS" in types

    # Each diagnosis must have root_cause_location and compiler_location
    for d in res["diagnostics"]:
        assert "compiler_location" in d
        assert "root_cause_location" in d
        assert d["confidence"] > 0
        assert "explanation" in d


def test_exact_duplicates_deduplicated():
    """Identical errors (e.g. from macro or template expansion) are deduplicated."""
    dup_error = {
        "file": "main.cpp",
        "line": 2,
        "column": 10,
        "severity": "error",
        "message": "'my_var' was not declared in this scope",
    }
    errors = [dup_error, dict(dup_error)]
    source = "int main() {\n    return my_var;\n}\n"

    filtered = deduplicate_and_filter_errors(errors)
    assert len(filtered) == 1

    res = build_result(errors, source)
    assert len(res["diagnostics"]) == 1


def test_cascade_noise_suppressed():
    """Parser desynchronization errors immediately following a missing semicolon are filtered."""
    errors = [
        {
            "file": "main.cpp",
            "line": 2,
            "column": 5,
            "severity": "error",
            "message": "expected ';' before 'return'",
        },
        {
            "file": "main.cpp",
            "line": 3,
            "column": 5,
            "severity": "error",
            "message": "expected unqualified-id before 'return'",
        },
    ]
    source = "int main() {\n    int x = 42\n    return 0;\n}\n"

    filtered = deduplicate_and_filter_errors(errors)
    assert len(filtered) == 1
    assert "expected ';'" in filtered[0]["message"]

    res = build_result(errors, source)
    assert len(res["diagnostics"]) == 1
    assert res["diagnostics"][0]["error_type"] == "MISSING_SEMICOLON"


def test_root_cause_location_deduplication():
    """Multiple compiler messages pointing to the exact same root cause statement are coalesced."""
    errors = [
        {
            "file": "main.cpp",
            "line": 3,
            "column": 1,
            "severity": "error",
            "message": "expected ';' before '}' token",
        },
        {
            "file": "main.cpp",
            "line": 3,
            "column": 2,
            "severity": "error",
            "message": "expected ';' before '}'",
        },
    ]
    source = "int main() {\n    int x = 42\n}\n"

    res = build_result(errors, source)
    assert res["status"] == "ok"
    assert len(res["diagnostics"]) == 1
    assert res["diagnostics"][0]["root_cause_location"]["line"] == 2
