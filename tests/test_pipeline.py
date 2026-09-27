"""Phase 5 regression tests: HTTP endpoint and CLI diagnose() must produce
equivalent diagnosis results for the same compiler output.

These tests use mocked compiler output (no real GCC required) and verify that
backend/pipeline.py is the single source of truth: both paths call the same
analyse_one / build_result logic and therefore cannot diverge.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from backend.main import app
from backend.diagnose import diagnose
from backend.compiler import CompilerResult

client = TestClient(app)

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


def _compiler_result(stderr: str, returncode: int = 1) -> CompilerResult:
    return CompilerResult(stdout="", stderr=stderr, returncode=returncode)


def _http_diagnose(source_code: str, compiler_output: str) -> dict:
    """Call the HTTP /diagnose endpoint with pre-supplied compiler output."""
    response = client.post(
        "/diagnose",
        json={"source_code": source_code, "compiler_output": compiler_output},
    )
    assert response.status_code == 200
    return response.json()


def _cli_diagnose(source_code: str, stderr: str, returncode: int = 1) -> dict:
    """Call diagnose() directly with mocked compiler output."""
    with patch(
        "backend.diagnose.run_compiler",
        return_value=_compiler_result(stderr, returncode),
    ):
        return diagnose(source_code)


def _assert_equivalent(http: dict, cli: dict, label: str = "") -> None:
    """Assert that HTTP and CLI results agree on the fields that matter."""
    tag = f" [{label}]" if label else ""
    assert http["status"] == cli["status"], \
        f"status mismatch{tag}: HTTP={http['status']!r}, CLI={cli['status']!r}"

    if cli["status"] != "ok":
        return

    # Core diagnosis fields must be identical
    hd = http["diagnosis"]
    cd = cli["diagnosis"]
    assert hd["error_type"] == cd["error_type"], \
        f"error_type mismatch{tag}: HTTP={hd['error_type']!r}, CLI={cd['error_type']!r}"
    assert hd["analysis_mode"] == cd["analysis_mode"], \
        f"analysis_mode mismatch{tag}: HTTP={hd['analysis_mode']!r}, CLI={cd['analysis_mode']!r}"
    assert hd["evidence"]["line"] == cd["evidence"]["line"], \
        f"evidence.line mismatch{tag}: HTTP={hd['evidence']['line']!r}, CLI={cd['evidence']['line']!r}"
    assert hd["evidence"]["code"] == cd["evidence"]["code"], \
        f"evidence.code mismatch{tag}: HTTP={hd['evidence']['code']!r}, CLI={cd['evidence']['code']!r}"

    # Both must carry all unified keys
    for diag, path in ((hd, "HTTP"), (cd, "CLI")):
        missing = _UNIFIED_KEYS - diag.keys()
        assert not missing, f"{path} diagnosis missing keys{tag}: {missing}"

    # diagnostics list length must agree
    assert len(http["diagnostics"]) == len(cli["diagnostics"]), (
        f"diagnostics list length mismatch{tag}: "
        f"HTTP={len(http['diagnostics'])}, CLI={len(cli['diagnostics'])}"
    )


# ---------------------------------------------------------------------------
# Equivalence: clean output
# ---------------------------------------------------------------------------

def test_http_cli_equivalent_clean_no_output():
    """Empty compiler output → both return status='clean'."""
    source = "int main() { return 0; }"
    http = _http_diagnose(source, "")
    cli = _cli_diagnose(source, "", returncode=0)
    _assert_equivalent(http, cli, "clean/no-output")


def test_http_cli_equivalent_clean_warnings_only():
    """Warning-only compiler output → both return status='clean'."""
    source = "int main() { int x = 1; return 0; }"
    stderr = "main.cpp:1:14: warning: unused variable 'x' [-Wunused-variable]\n"
    http = _http_diagnose(source, stderr)
    cli = _cli_diagnose(source, stderr, returncode=0)
    _assert_equivalent(http, cli, "clean/warnings-only")


# ---------------------------------------------------------------------------
# Equivalence: single known error
# ---------------------------------------------------------------------------

def test_http_cli_equivalent_missing_semicolon():
    """MISSING_SEMICOLON diagnosed identically by HTTP and CLI."""
    source = "int main() {\n    int x = 10\n    return 0;\n}\n"
    stderr = "/tmp/t.cpp:2:16: error: expected ';' before 'return'\n"
    http = _http_diagnose(source, stderr)
    cli = _cli_diagnose(source, stderr)
    _assert_equivalent(http, cli, "MISSING_SEMICOLON")
    assert http["diagnosis"]["error_type"] == "MISSING_SEMICOLON"


def test_http_cli_equivalent_undefined_variable():
    """UNDEFINED_VARIABLE diagnosed identically by HTTP and CLI."""
    source = "int main() {\n    return value;\n}\n"
    stderr = "/tmp/t.cpp:2:12: error: 'value' was not declared in this scope\n"
    http = _http_diagnose(source, stderr)
    cli = _cli_diagnose(source, stderr)
    _assert_equivalent(http, cli, "UNDEFINED_VARIABLE")
    assert http["diagnosis"]["error_type"] == "UNDEFINED_VARIABLE"


def test_http_cli_equivalent_type_mismatch():
    """TYPE_MISMATCH diagnosed identically by HTTP and CLI."""
    source = 'int main() {\n    int x = "hello";\n    return 0;\n}\n'
    stderr = "/tmp/t.cpp:2:13: error: cannot convert 'const char*' to 'int'\n"
    http = _http_diagnose(source, stderr)
    cli = _cli_diagnose(source, stderr)
    _assert_equivalent(http, cli, "TYPE_MISMATCH")
    assert http["diagnosis"]["error_type"] == "TYPE_MISMATCH"


def test_http_cli_equivalent_bob_fallback():
    """Unknown error falls back to Bob identically in both paths."""
    source = "int main() { return 0; }\n"
    stderr = "/tmp/t.cpp:1:5: error: some completely unknown error XYZ999\n"
    http = _http_diagnose(source, stderr)
    cli = _cli_diagnose(source, stderr)
    _assert_equivalent(http, cli, "bob-fallback")
    assert http["diagnosis"]["analysis_mode"] == "ai"
    assert http["diagnosis"]["error_type"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Equivalence: multiple errors
# ---------------------------------------------------------------------------

def test_http_cli_equivalent_multiple_errors():
    """Multiple errors are diagnosed in the same order by both paths."""
    source = "int main() {\n    int x = 10\n    return value;\n}\n"
    stderr = (
        "/tmp/t.cpp:3:5: error: expected ';' before 'return'\n"
        "/tmp/t.cpp:3:12: error: 'value' was not declared in this scope\n"
    )
    http = _http_diagnose(source, stderr)
    cli = _cli_diagnose(source, stderr)
    _assert_equivalent(http, cli, "multiple-errors")
    # Both have two entries in diagnostics
    assert len(http["diagnostics"]) == 2
    assert http["diagnostics"][0]["error_type"] == "MISSING_SEMICOLON"
    assert http["diagnostics"][1]["error_type"] == "UNDEFINED_VARIABLE"
    assert cli["diagnostics"][0]["error_type"] == "MISSING_SEMICOLON"
    assert cli["diagnostics"][1]["error_type"] == "UNDEFINED_VARIABLE"


# ---------------------------------------------------------------------------
# Structural: pipeline.py exports work correctly
# ---------------------------------------------------------------------------

def test_analyse_one_returns_unified_keys():
    """pipeline.analyse_one returns all unified keys plus 'raw'."""
    from backend.pipeline import analyse_one

    error = {
        "file": "t.cpp",
        "line": 2,
        "column": 5,
        "severity": "error",
        "message": "expected ';' before 'return'",
        "normalized": "t.cpp:2:5: error: expected ';' before 'return'",
    }
    result = analyse_one(error, "int main() {\n    int x = 0\n    return 0;\n}\n")
    missing = _UNIFIED_KEYS - result.keys()
    assert not missing, f"analyse_one missing keys: {missing}"
    assert "raw" in result, "analyse_one must attach 'raw' field"
    assert result["raw"]["line"] == 2
    assert result["raw"]["severity"] == "error"


def test_build_result_empty_errors_returns_clean():
    """pipeline.build_result([]) returns status='clean'."""
    from backend.pipeline import build_result

    result = build_result([], "int main() { return 0; }")
    assert result == {"status": "clean"}


def test_build_result_single_error_shape():
    """pipeline.build_result with one error returns correct shape."""
    from backend.pipeline import build_result

    errors = [{
        "file": "t.cpp",
        "line": 1,
        "column": 1,
        "severity": "error",
        "message": "expected ';' before '}'",
        "normalized": "t.cpp:1:1: error: expected ';' before '}'",
    }]
    result = build_result(errors, "int main() {}")
    assert result["status"] == "ok"
    assert "diagnosis" in result
    assert "diagnostics" in result
    assert len(result["diagnostics"]) == 1
    assert result["diagnosis"] is result["diagnostics"][0]
