"""Tests for backend/diagnose.py — the VS Code extension adapter.

All tests call ``diagnose()`` directly (no subprocess) so they are fast and
deterministic.  The only tests that require a real GCC installation are
clearly marked with ``@pytest.mark.integration``.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.diagnose import diagnose
from backend.compiler import GCCNotFoundError, GCCTimeoutError, CompilerResult

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


def _assert_ok(result: dict) -> None:
    assert result["status"] == "ok", f"Expected status='ok', got: {result}"
    assert "diagnosis" in result
    missing = _UNIFIED_KEYS - result["diagnosis"].keys()
    assert not missing, f"Missing diagnosis keys: {missing}"


def _compiler_result(stderr: str, returncode: int = 1) -> CompilerResult:
    return CompilerResult(stdout="", stderr=stderr, returncode=returncode)


# ---------------------------------------------------------------------------
# Status: "clean" — compiler reports no errors
# ---------------------------------------------------------------------------

def test_clean_on_no_errors():
    """When GCC produces no diagnostics the result must be {'status': 'clean'}."""
    with patch("backend.diagnose.run_compiler", return_value=_compiler_result("", 0)):
        result = diagnose("int main() { return 0; }")
    assert result == {"status": "clean"}


def test_clean_when_only_warnings():
    """Warnings are not errors; the result must still be {'status': 'clean'}."""
    warn_stderr = "/tmp/t.cpp:2:9: warning: unused variable 'x' [-Wunused-variable]\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(warn_stderr, 0)):
        result = diagnose("int main() { int x = 1; return 0; }")
    assert result == {"status": "clean"}


# ---------------------------------------------------------------------------
# Status: "ok" — deterministic rules matched
# ---------------------------------------------------------------------------

def test_missing_semicolon_deterministic():
    """A missing-semicolon error is classified deterministically."""
    source = (
        "int main() {\n"
        "    int x = 10\n"
        "    return 0;\n"
        "}\n"
    )
    stderr = "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "MISSING_SEMICOLON"
    assert result["diagnosis"]["analysis_mode"] == "deterministic"


def test_undefined_variable_deterministic():
    """An undefined-variable error is classified deterministically."""
    source = (
        "int main() {\n"
        "    return value;\n"
        "}\n"
    )
    stderr = "/tmp/t.cpp:2:12: error: 'value' was not declared in this scope\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "UNDEFINED_VARIABLE"
    assert result["diagnosis"]["analysis_mode"] == "deterministic"


def test_type_mismatch_deterministic():
    """A cannot-convert error is classified as TYPE_MISMATCH."""
    source = (
        "int main() {\n"
        '    int x = "hello";\n'
        "    return 0;\n"
        "}\n"
    )
    stderr = "/tmp/t.cpp:2:13: error: cannot convert 'const char*' to 'int'\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "TYPE_MISMATCH"
    assert result["diagnosis"]["analysis_mode"] == "deterministic"


def test_wrong_arguments_deterministic():
    """A no-matching-function error is classified as WRONG_ARGUMENTS."""
    source = (
        "int add(int a, int b) { return a + b; }\n"
        "int main() {\n"
        "    return add(1);\n"
        "}\n"
    )
    stderr = "/tmp/t.cpp:3:12: error: no matching function for call to 'add(int)'\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "WRONG_ARGUMENTS"
    assert result["diagnosis"]["analysis_mode"] == "deterministic"


def test_missing_include_deterministic():
    """A 'not a member of std' error is classified as MISSING_INCLUDE."""
    source = (
        "int main() {\n"
        '    std::cout << "hi";\n'
        "    return 0;\n"
        "}\n"
    )
    stderr = "/tmp/t.cpp:2:10: error: 'cout' is not a member of 'std'\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "MISSING_INCLUDE"
    assert result["diagnosis"]["analysis_mode"] == "deterministic"


# ---------------------------------------------------------------------------
# Status: "ok" — bob fallback (unknown error)
# ---------------------------------------------------------------------------

def test_unknown_error_falls_back_to_bob():
    """An unrecognised compiler message must fall back to Bob (analysis_mode='ai')."""
    source = "int main() { return 0; }\n"
    stderr = "/tmp/t.cpp:1:5: error: some completely unknown error XYZ\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "UNKNOWN"
    assert result["diagnosis"]["analysis_mode"] == "ai"


# ---------------------------------------------------------------------------
# Multi-error deep analysis
# ---------------------------------------------------------------------------

def test_multiple_errors_returns_diagnostics_list():
    """When GCC emits multiple errors, all are deeply analysed in 'diagnostics'."""
    source = (
        "int main() {\n"
        "    int x = 10\n"
        "    return value;\n"
        "}\n"
    )
    stderr = (
        "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:3:12: error: 'value' was not declared in this scope\n"
    )
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    # Backward compat: first error in top-level 'diagnosis'
    assert result["diagnosis"]["error_type"] == "MISSING_SEMICOLON"
    # New: all errors in 'diagnostics' list
    assert "diagnostics" in result
    assert len(result["diagnostics"]) == 2
    assert result["diagnostics"][0]["error_type"] == "MISSING_SEMICOLON"
    assert result["diagnostics"][1]["error_type"] == "UNDEFINED_VARIABLE"


def test_diagnostics_list_each_has_unified_keys():
    """Every entry in 'diagnostics' must have all unified output keys."""
    source = (
        "int main() {\n"
        "    int x = 10\n"
        "    return value;\n"
        "}\n"
    )
    stderr = (
        "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:3:12: error: 'value' was not declared in this scope\n"
    )
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    for d in result["diagnostics"]:
        missing = _UNIFIED_KEYS - d.keys()
        assert not missing, f"Missing unified keys in diagnostics entry: {missing}"
        assert "raw" in d, "Each diagnostics entry must have a 'raw' field"


def test_diagnostics_raw_field_carries_compiler_location():
    """The 'raw' field in each diagnosis must carry file/line/column/severity/message."""
    stderr = (
        "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:5:8: error: 'val' was not declared in this scope\n"
    )
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose("int main() {\n    int x = 10\n    return val;\n}\n")

    raw0 = result["diagnostics"][0]["raw"]
    assert raw0["line"] == 3
    assert raw0["column"] == 5
    assert raw0["severity"] == "error"
    assert "';'" in raw0["message"]

    raw1 = result["diagnostics"][1]["raw"]
    assert raw1["line"] == 5
    assert raw1["column"] == 8


def test_single_error_diagnostics_list_has_one_entry():
    """When there is only one error, 'diagnostics' has exactly one entry."""
    stderr = "/tmp/t.cpp:2:5: error: expected ';' before 'return'\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose("int main() {\n    int x = 10\n    return 0;\n}\n")

    _assert_ok(result)
    assert len(result["diagnostics"]) == 1
    assert result["diagnostics"][0] is result["diagnosis"]  # same object


def test_three_errors_all_deeply_analysed():
    """Three distinct error types must all be independently analysed."""
    stderr = (
        "/tmp/t.cpp:2:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:3:8: error: 'val' was not declared in this scope\n"
        "/tmp/t.cpp:4:5: error: no matching function for call to 'add(int)'\n"
    )
    source = "int add(int a, int b) { return a+b; }\nint main() {\n    int x=10\n    return val;\n    return add(1);\n}\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert len(result["diagnostics"]) == 3
    types = [d["error_type"] for d in result["diagnostics"]]
    assert types[0] == "MISSING_SEMICOLON"
    assert types[1] == "UNDEFINED_VARIABLE"
    assert types[2] == "WRONG_ARGUMENTS"


def test_mixed_known_and_unknown_errors():
    """Mix of known and unknown errors: known → deterministic, unknown → ai."""
    stderr = (
        "/tmp/t.cpp:2:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:3:5: error: some completely unknown xyz error\n"
    )
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose("int main() {\n    int x = 10\n    unknown;\n}\n")

    _assert_ok(result)
    assert len(result["diagnostics"]) == 2
    assert result["diagnostics"][0]["analysis_mode"] == "deterministic"
    assert result["diagnostics"][1]["analysis_mode"] == "ai"


def test_first_error_in_diagnosis_matches_diagnostics_zero():
    """'diagnosis' (top-level) must always equal 'diagnostics[0]'."""
    stderr = (
        "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:4:5: error: 'x' was not declared in this scope\n"
    )
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose("int main() {\n    int x=10\n    return x;\n}\n")

    assert result["diagnosis"] is result["diagnostics"][0]


# ---------------------------------------------------------------------------
# Warnings and notes mixed with errors — only errors are analysed
# ---------------------------------------------------------------------------

def test_warnings_do_not_suppress_error_analysis():
    """Warnings alongside an error must not prevent the error from being analysed."""
    source = (
        "int main() {\n"
        "    int x = 10\n"
        "    return 0;\n"
        "}\n"
    )
    stderr = (
        "/tmp/t.cpp:2:9: warning: unused variable 'y' [-Wunused-variable]\n"
        "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
    )
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "MISSING_SEMICOLON"


# ---------------------------------------------------------------------------
# Infrastructure error cases
# ---------------------------------------------------------------------------

def test_gcc_not_found_returns_error_status():
    """GCCNotFoundError must be surfaced as {'status': 'error', 'message': ...}."""
    with patch("backend.diagnose.run_compiler",
               side_effect=GCCNotFoundError("GCC not found.")):
        result = diagnose("int main() {}")

    assert result["status"] == "error"
    assert "GCC" in result["message"]


def test_gcc_timeout_returns_error_status():
    """GCCTimeoutError must be surfaced as {'status': 'error', 'message': ...}."""
    with patch("backend.diagnose.run_compiler",
               side_effect=GCCTimeoutError("GCC timed out.")):
        result = diagnose("int main() {}")

    assert result["status"] == "error"
    assert "timed out" in result["message"].lower() or "GCC" in result["message"]


# ---------------------------------------------------------------------------
# Evidence field is always present and well-formed
# ---------------------------------------------------------------------------

def test_evidence_is_well_formed_for_known_error():
    """The evidence dict must always contain 'line' and 'code' keys."""
    source = (
        "int main() {\n"
        "    int x = 10\n"
        "    return 0;\n"
        "}\n"
    )
    stderr = "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    evidence = result["diagnosis"]["evidence"]
    assert "line" in evidence
    assert "code" in evidence


def test_evidence_is_well_formed_for_unknown_error():
    """Bob's evidence dict must also always contain 'line' and 'code' keys."""
    source = "int main() { return 0; }\n"
    stderr = "/tmp/t.cpp:1:5: error: mysterious unknown error\n"
    with patch("backend.diagnose.run_compiler",
               return_value=_compiler_result(stderr)):
        result = diagnose(source)

    evidence = result["diagnosis"]["evidence"]
    assert "line" in evidence
    assert "code" in evidence


# ---------------------------------------------------------------------------
# Integration tests (require real GCC on PATH)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_integration_clean_compile():
    """Valid C++ source must produce {'status': 'clean'} with a real GCC."""
    source = (
        "#include <iostream>\n"
        "int main() {\n"
        "    std::cout << 42;\n"
        "    return 0;\n"
        "}\n"
    )
    result = diagnose(source)
    assert result["status"] == "clean"


@pytest.mark.integration
def test_integration_missing_semicolon():
    """Real GCC on a missing-semicolon file must produce a deterministic diagnosis."""
    source = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int x = 10\n"
        "    std::cout << x;\n"
        "    return 0;\n"
        "}\n"
    )
    result = diagnose(source)
    assert result["status"] == "ok"
    assert result["diagnosis"]["error_type"] == "MISSING_SEMICOLON"
    assert result["diagnosis"]["analysis_mode"] == "deterministic"
