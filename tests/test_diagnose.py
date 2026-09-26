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
# Only first error is analysed when multiple errors are present
# ---------------------------------------------------------------------------

def test_only_first_error_is_analysed():
    """When GCC emits multiple errors, only the first is returned in diagnosis."""
    source = (
        "int main() {\n"
        "    int x = 10\n"        # missing semicolon → error on line 3
        "    return value;\n"     # undefined variable → error on line 3 or 4
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
    # First error wins
    assert result["diagnosis"]["error_type"] == "MISSING_SEMICOLON"


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
