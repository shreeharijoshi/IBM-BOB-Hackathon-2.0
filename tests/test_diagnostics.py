import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.diagnostics import analyze_simple_errors
from backend.bob import analyze_with_bob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNIFIED_KEYS = {
    "error_type",
    "analysis_mode",
    "compiler_message",
    "compiler_explanation",
    "source_explanation",
    "evidence",
    "what_to_check",
    "suggestion",
}


def _assert_unified_keys(result: dict) -> None:
    """Assert that all required unified output keys are present."""
    missing = _UNIFIED_KEYS - result.keys()
    assert not missing, f"Missing unified output keys: {missing}"


# ---------------------------------------------------------------------------
# Deterministic rule tests
# ---------------------------------------------------------------------------

def test_missing_semicolon():
    diag = {
        "normalized": "main.cpp:4:5: error: expected ';' before 'std'",
        "line": 4,
        "source_context": [
            {"line": 3, "code": "int main() {"},
            {"line": 4, "code": "int x = 10"},
            {"line": 5, "code": "std::cout << x;"},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    _assert_unified_keys(result)
    assert result["error_type"] == "MISSING_SEMICOLON"
    assert result["analysis_mode"] == "deterministic"
    assert "expected ';'" in result["compiler_message"]
    # Evidence must select line 4, not line 3 (first entry)
    assert result["evidence"]["line"] == 4
    assert result["evidence"]["code"] == "int x = 10"


def test_missing_semicolon_gcc_variant():
    """GCC message 'expected ',' or ';' before ...' must match MISSING_SEMICOLON."""
    diag = {
        "file": "temp.cpp",
        "line": 4,
        "column": 5,
        "severity": "error",
        "message": "expected ',' or ';' before 'std'",
        "source_context": [
            {"line": 3, "code": "int main() {"},
            {"line": 4, "code": "    int x = 10"},
            {"line": 5, "code": "    std::cout << x;"},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None, "Pipeline returned None — rule did not match"
    _assert_unified_keys(result)
    assert result["error_type"] == "MISSING_SEMICOLON"
    assert result["analysis_mode"] == "deterministic"
    assert "expected ',' or ';' before 'std'" in result["compiler_message"]
    assert result["evidence"]["line"] == 4
    assert result["evidence"]["code"] == "    int x = 10"


def test_undefined_variable():
    diag = {
        "normalized": "main.cpp:2:12: error: 'value' was not declared in this scope",
        "line": 2,
        "source_context": [
            {"line": 1, "code": "int main() {"},
            {"line": 2, "code": "    return value;"},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    _assert_unified_keys(result)
    assert result["error_type"] == "UNDEFINED_VARIABLE"
    assert result["analysis_mode"] == "deterministic"
    assert "was not declared in this scope" in result["compiler_message"]
    assert result["evidence"]["line"] == 2
    assert result["evidence"]["code"] == "    return value;"


def test_type_mismatch_cannot_convert():
    diag = {
        "normalized": (
            "main.cpp:2:13: error: cannot convert 'const char*' to 'int'"
        ),
        "line": 2,
        "source_context": [
            {"line": 1, "code": "int main() {"},
            {"line": 2, "code": '    int x = "hello";'},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    _assert_unified_keys(result)
    assert result["error_type"] == "TYPE_MISMATCH"
    assert result["analysis_mode"] == "deterministic"
    assert "cannot convert" in result["compiler_message"]
    assert result["evidence"]["line"] == 2
    assert result["evidence"]["code"] == '    int x = "hello";'


def test_type_mismatch_invalid_conversion():
    diag = {
        "normalized": "main.cpp:3:9: error: invalid conversion from 'char*' to 'int'",
        "line": 3,
        "source_context": [
            {"line": 3, "code": "    int y = ptr;"},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    _assert_unified_keys(result)
    assert result["error_type"] == "TYPE_MISMATCH"
    assert result["analysis_mode"] == "deterministic"


def test_wrong_arguments():
    diag = {
        "normalized": (
            "main.cpp:3:12: error: no matching function for call to 'add(int)'"
        ),
        "line": 3,
        "source_context": [
            {"line": 1, "code": "int add(int a, int b) { return a + b; }"},
            {"line": 2, "code": "int main() {"},
            {"line": 3, "code": "    return add(1);"},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    _assert_unified_keys(result)
    assert result["error_type"] == "WRONG_ARGUMENTS"
    assert result["analysis_mode"] == "deterministic"
    assert "no matching function" in result["compiler_message"]
    assert result["evidence"]["line"] == 3
    assert result["evidence"]["code"] == "    return add(1);"


def test_missing_include():
    diag = {
        "normalized": (
            "main.cpp:2:10: error: 'cout' is not a member of 'std'"
        ),
        "line": 2,
        "source_context": [
            {"line": 1, "code": "int main() {"},
            {"line": 2, "code": '    std::cout << "hi";'},
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    _assert_unified_keys(result)
    assert result["error_type"] == "MISSING_INCLUDE"
    assert result["analysis_mode"] == "deterministic"
    assert "is not a member of 'std'" in result["compiler_message"]
    assert result["evidence"]["line"] == 2
    assert result["evidence"]["code"] == '    std::cout << "hi";'


# ---------------------------------------------------------------------------
# Evidence selection: matching line beats first-entry fallback
# ---------------------------------------------------------------------------

def test_evidence_selects_matching_line_not_first():
    """When source_context has multiple entries, evidence must use the entry
    whose line matches the diagnostic's reported line, not the first entry."""
    diag = {
        "normalized": "main.cpp:5:3: error: expected ';' before 'return'",
        "line": 5,
        "source_context": [
            {"line": 3, "code": "int main() {"},
            {"line": 4, "code": "    int x = 42"},   # first entry — wrong line
            {"line": 5, "code": "    return x;"},    # correct match
        ],
    }
    result = analyze_simple_errors(diag)

    assert result is not None
    assert result["evidence"]["line"] == 5
    assert result["evidence"]["code"] == "    return x;"


# ---------------------------------------------------------------------------
# Unknown error → None
# ---------------------------------------------------------------------------

def test_unknown_error_returns_none():
    diag = {"normalized": "some completely unrecognised compiler message XYZ"}
    result = analyze_simple_errors(diag)
    assert result is None


# ---------------------------------------------------------------------------
# Bob stub tests
# ---------------------------------------------------------------------------

_BOB_REQUIRED_KEYS = _UNIFIED_KEYS


def test_bob_empty_input_shape():
    """analyze_with_bob must return a valid unified dict even for an empty input."""
    result = analyze_with_bob({})

    assert result is not None
    missing = _BOB_REQUIRED_KEYS - result.keys()
    assert not missing, f"Missing unified output keys: {missing}"
    assert result["analysis_mode"] == "ai"
    assert result["error_type"] == "UNKNOWN"
    assert result["compiler_message"] == ""
    assert result["evidence"] == {"line": None, "code": ""}


def test_bob_with_message_and_context():
    """analyze_with_bob must reflect the compiler message and select the
    correct evidence line from source_context."""
    diag = {
        "normalized": "template deduction failed: unknown_symbol",
        "line": 2,
        "source_context": [
            {"line": 1, "code": "template <typename T>"},
            {"line": 2, "code": "T f(T x) { return x + unknown_symbol; }"},
        ],
    }
    result = analyze_with_bob(diag)

    assert result["analysis_mode"] == "ai"
    assert result["error_type"] == "UNKNOWN"
    assert result["compiler_message"] == "template deduction failed: unknown_symbol"
    assert result["evidence"]["line"] == 2
    assert result["evidence"]["code"] == "T f(T x) { return x + unknown_symbol; }"
    missing = _BOB_REQUIRED_KEYS - result.keys()
    assert not missing, f"Missing unified output keys: {missing}"
