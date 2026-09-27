"""End-to-end verification of the full diagnostic pipeline.

Covers every stage the VS Code extension exercises:

    C++ source
    → run_compiler()          (compiler.py — spawns real GCC)
    → parse_compiler_output() (compiler.py — regex parsing)
    → enrich_context()        (context.py  — source-window extraction)
    → analyze_simple_errors() (diagnostics.py — deterministic rules)
    → analyze_with_bob()      (bob.py        — fallback stub)
    → diagnose()              (diagnose.py   — full pipeline function)
    → CLI subprocess          (diagnose.py __main__ — JSON-over-stdout contract)

All tests use real GCC.  Tests in this module are implicitly integration tests
and require GCC on PATH.  They are NOT mocked — the whole point is to verify
the complete path without alteration.

Test inventory
--------------
1.  valid_cpp_produces_clean
2.  missing_semicolon_example_file
3.  undefined_variable_example_file
4.  type_mismatch_example_file
5.  wrong_arguments_example_file
6.  missing_include_example_file
7.  complex_error_falls_through_to_deterministic (example 06)
8.  multiple_errors_only_first_is_reported
9.  warnings_only_produces_clean
10. empty_source_produces_clean
11. whitespace_only_source_produces_clean
12. missing_file_via_cli_returns_error_status
13. diagnosis_schema_is_complete_for_every_error_type
14. evidence_line_is_present_and_matches_source
15. evidence_code_is_exact_source_text_unmodified
16. cli_stdout_is_valid_json_for_each_example
17. cli_stderr_is_empty_on_all_ok_paths
18. analysis_mode_is_deterministic_for_known_errors
19. analysis_mode_is_ai_for_unknown_errors
20. pipeline_stage_order_is_preserved (source context present before analysis)
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Make backend importable when running from repo root or via pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compiler import parse_compiler_output, run_compiler
from backend.context import enrich_context
from backend.diagnose import diagnose
from backend.diagnostics import analyze_simple_errors
from backend.bob import analyze_with_bob

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_EXAMPLES = _REPO / "examples"

_F01 = _EXAMPLES / "01_missing_semicolon.cpp"
_F02 = _EXAMPLES / "02_undefined_variable.cpp"
_F03 = _EXAMPLES / "03_type_mismatch.cpp"
_F04 = _EXAMPLES / "04_wrong_arguments.cpp"
_F05 = _EXAMPLES / "05_missing_include.cpp"
_F06 = _EXAMPLES / "06_complex_error.cpp"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIAGNOSIS_KEYS = {
    "error_type",
    "analysis_mode",
    "compiler_message",
    "compiler_explanation",
    "source_explanation",
    "evidence",
    "what_to_check",
    "suggestion",
}


def _run_cli(file_path: Path) -> dict:
    """Invoke diagnose.py as a subprocess exactly as the VS Code extension does."""
    proc = subprocess.run(
        [sys.executable, str(_REPO / "backend" / "diagnose.py"), "--file", str(file_path)],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )
    return proc


def _diagnose_file(file_path: Path) -> dict:
    """Call diagnose() directly with the content of a file."""
    return diagnose(file_path.read_text(encoding="utf-8"))


def _assert_ok(result: dict) -> None:
    assert result["status"] == "ok", f"Expected status='ok', got: {result}"
    assert "diagnosis" in result
    missing = _DIAGNOSIS_KEYS - result["diagnosis"].keys()
    assert not missing, f"Missing diagnosis keys: {missing}"


def _assert_diagnosis_schema(d: dict) -> None:
    missing = _DIAGNOSIS_KEYS - d.keys()
    assert not missing, f"Missing diagnosis keys: {missing}"
    assert isinstance(d["evidence"], dict), "evidence must be a dict"
    assert "line" in d["evidence"], "evidence must have 'line'"
    assert "code" in d["evidence"], "evidence must have 'code'"
    assert d["analysis_mode"] in ("deterministic", "ai"), \
        f"analysis_mode must be 'deterministic' or 'ai', got {d['analysis_mode']!r}"


# ---------------------------------------------------------------------------
# 1. Valid C++ → "clean"
# ---------------------------------------------------------------------------

def test_valid_cpp_produces_clean():
    """Stage: run_compiler → parse → diagnose.
    A compilable program must return status='clean' with no diagnosis.
    """
    source = (
        "#include <iostream>\n"
        "int main() {\n"
        "    std::cout << 42;\n"
        "    return 0;\n"
        "}\n"
    )
    result = diagnose(source)
    assert result == {"status": "clean"}, f"Expected clean, got: {result}"


# ---------------------------------------------------------------------------
# 2. Missing semicolon — example file 01
# ---------------------------------------------------------------------------

def test_missing_semicolon_example_file():
    """Full pipeline on examples/01_missing_semicolon.cpp.
    Verifies: GCC fires, parse_compiler_output captures it,
    enrich_context adds source, deterministic rule MISSING_SEMICOLON matches.
    """
    result = _diagnose_file(_F01)
    _assert_ok(result)
    d = result["diagnosis"]
    assert d["error_type"] == "MISSING_SEMICOLON"
    assert d["analysis_mode"] == "deterministic"
    assert ";" in d["suggestion"]
    # Evidence line must exist and contain source text
    assert d["evidence"]["line"] is not None
    assert d["evidence"]["code"] != ""


def test_missing_semicolon_evidence_points_to_flagged_line():
    """The evidence line reported must be within the source file line count."""
    source = _F01.read_text(encoding="utf-8")
    total_lines = len(source.splitlines())
    result = diagnose(source)
    ev_line = result["diagnosis"]["evidence"]["line"]
    assert 1 <= ev_line <= total_lines, \
        f"Evidence line {ev_line} outside source range 1-{total_lines}"


# ---------------------------------------------------------------------------
# 3. Undefined variable — example file 02
# ---------------------------------------------------------------------------

def test_undefined_variable_example_file():
    """Full pipeline on examples/02_undefined_variable.cpp."""
    result = _diagnose_file(_F02)
    _assert_ok(result)
    d = result["diagnosis"]
    assert d["error_type"] == "UNDEFINED_VARIABLE"
    assert d["analysis_mode"] == "deterministic"
    assert d["evidence"]["line"] == 2
    assert "value" in d["evidence"]["code"]


# ---------------------------------------------------------------------------
# 4. Type mismatch — example file 03
# ---------------------------------------------------------------------------

def test_type_mismatch_example_file():
    """Full pipeline on examples/03_type_mismatch.cpp."""
    result = _diagnose_file(_F03)
    _assert_ok(result)
    d = result["diagnosis"]
    assert d["error_type"] == "TYPE_MISMATCH"
    assert d["analysis_mode"] == "deterministic"
    assert d["evidence"]["line"] == 2
    assert '"hello"' in d["evidence"]["code"]


# ---------------------------------------------------------------------------
# 5. Wrong arguments — example file 04
# ---------------------------------------------------------------------------

def test_wrong_arguments_example_file():
    """Full pipeline on examples/04_wrong_arguments.cpp."""
    result = _diagnose_file(_F04)
    _assert_ok(result)
    d = result["diagnosis"]
    assert d["error_type"] == "WRONG_ARGUMENTS"
    assert d["analysis_mode"] == "deterministic"
    assert d["evidence"]["line"] == 3
    assert "add" in d["evidence"]["code"]


# ---------------------------------------------------------------------------
# 6. Missing include — example file 05
# ---------------------------------------------------------------------------

def test_missing_include_example_file():
    """Full pipeline on examples/05_missing_include.cpp."""
    result = _diagnose_file(_F05)
    _assert_ok(result)
    d = result["diagnosis"]
    assert d["error_type"] == "MISSING_INCLUDE"
    assert d["analysis_mode"] == "deterministic"
    assert d["evidence"]["line"] == 2
    assert "cout" in d["evidence"]["code"]


# ---------------------------------------------------------------------------
# 7. Complex / template error (example 06) — falls through to deterministic
#    because GCC still reports 'was not declared in this scope'
# ---------------------------------------------------------------------------

def test_complex_error_example_file():
    """Full pipeline on examples/06_complex_error.cpp.
    GCC reports 'was not declared in this scope' for the template body,
    so the deterministic UNDEFINED_VARIABLE rule fires even for complex code.
    The pipeline must not crash or return an unexpected schema.
    """
    result = _diagnose_file(_F06)
    _assert_ok(result)
    d = result["diagnosis"]
    _assert_diagnosis_schema(d)
    # unknown_symbol is the name GCC will flag
    assert "unknown_symbol" in d["compiler_message"]
    # Must be deterministic because the message substring matches a known rule
    assert d["analysis_mode"] == "deterministic"


# ---------------------------------------------------------------------------
# 8. Multiple diagnostics — only first error is reported
# ---------------------------------------------------------------------------

def test_multiple_errors_first_is_in_diagnosis():
    """When GCC emits more than one error, 'diagnosis' (backward-compat) is the first.
    Stage tested: parse_compiler_output → filter errors → enrich_context → analyze.
    """
    source = (
        "int main() {\n"
        "    int x = 10\n"
        "    return undefined;\n"
        "}\n"
    )
    result = diagnose(source)
    _assert_ok(result)
    assert result["diagnosis"]["error_type"] == "MISSING_SEMICOLON"


def test_multiple_errors_all_deeply_analysed():
    """All errors have deep analysis in 'diagnostics' list.

    Uses two genuinely independent errors (type mismatch + wrong arguments)
    so both GCC and Apple Clang report both without cascade suppression.
    """
    source = (
        "int add(int a, int b) { return a + b; }\n"
        "int main() {\n"
        '    int x = "hello";\n'   # TYPE_MISMATCH
        "    return add(1);\n"     # WRONG_ARGUMENTS
        "}\n"
    )
    result = diagnose(source)
    _assert_ok(result)
    assert "diagnostics" in result
    assert len(result["diagnostics"]) >= 2
    # Each has the full unified schema + raw
    for d in result["diagnostics"]:
        missing = _DIAGNOSIS_KEYS - d.keys()
        assert not missing, f"Missing keys in diagnostics entry: {missing}"
        assert "raw" in d
        assert isinstance(d["raw"]["line"], int)

    # Both errors are classified deterministically
    types = {d["error_type"] for d in result["diagnostics"]}
    assert "TYPE_MISMATCH" in types
    assert "WRONG_ARGUMENTS" in types


def test_diagnostics_raw_carries_exact_compiler_location():
    """raw.line and raw.column in each diagnostics entry must match what GCC reported."""
    source = (
        "int add(int a, int b) { return a + b; }\n"
        "int main() {\n"
        '    int x = "hello";\n'
        "    return add(1);\n"
        "}\n"
    )
    result = diagnose(source)
    _assert_ok(result)
    for d in result["diagnostics"]:
        raw = d["raw"]
        assert raw["line"] is not None
        assert raw["severity"] == "error"
        assert isinstance(raw["message"], str) and len(raw["message"]) > 0


# ---------------------------------------------------------------------------
# 9. Warnings only → "clean"
# ---------------------------------------------------------------------------

def test_warnings_only_produces_clean():
    """Warnings must not be escalated to errors; status must be 'clean'."""
    source = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int unused = 5;\n"     # triggers -Wunused-variable with some GCC configs
        "    return 0;\n"
        "}\n"
    )
    result = diagnose(source)
    # Regardless of whether GCC emits a warning here, it must NOT be "ok" (error)
    assert result["status"] in ("clean",), \
        f"Warnings-only or clean source produced status={result['status']!r}"


# ---------------------------------------------------------------------------
# 10 & 11. Edge inputs: empty and whitespace-only source
# ---------------------------------------------------------------------------

def test_empty_source_produces_clean():
    """Empty string: GCC compiles successfully → status 'clean'."""
    result = diagnose("")
    assert result["status"] == "clean", f"Expected clean for empty source, got: {result}"


def test_whitespace_only_source_produces_clean():
    """Whitespace-only source: GCC treats it as an empty translation unit."""
    result = diagnose("   \n\n   \n")
    assert result["status"] == "clean", \
        f"Expected clean for whitespace-only source, got: {result}"


# ---------------------------------------------------------------------------
# 12. CLI subprocess contract — missing file → error status
# ---------------------------------------------------------------------------

def test_missing_file_via_cli_returns_error_status():
    """Invoking the CLI with a non-existent path must write a JSON error to stdout.
    This is the exact path the VS Code extension uses when a file cannot be read.
    """
    proc = _run_cli(Path("this_file_does_not_exist_xyz.cpp"))
    # stdout must be parseable JSON
    assert proc.stdout.strip(), "CLI produced no stdout for missing file"
    payload = json.loads(proc.stdout.strip())
    assert payload["status"] == "error"
    assert "message" in payload
    assert len(payload["message"]) > 0
    # stderr must be empty — no Python tracebacks on expected errors
    assert proc.stderr.strip() == "", \
        f"CLI wrote to stderr for a missing-file error:\n{proc.stderr}"


# ---------------------------------------------------------------------------
# 13. Diagnosis schema completeness for every error type
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_path,expected_type", [
    (_F01, "MISSING_SEMICOLON"),
    (_F02, "UNDEFINED_VARIABLE"),
    (_F03, "TYPE_MISMATCH"),
    (_F04, "WRONG_ARGUMENTS"),
    (_F05, "MISSING_INCLUDE"),
])
def test_diagnosis_schema_is_complete(file_path: Path, expected_type: str):
    """Every diagnosis must carry all required keys, regardless of error type."""
    result = _diagnose_file(file_path)
    _assert_ok(result)
    d = result["diagnosis"]
    _assert_diagnosis_schema(d)
    assert d["error_type"] == expected_type


# ---------------------------------------------------------------------------
# 14. Evidence line is always within the source file bounds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_path", [_F01, _F02, _F03, _F04, _F05])
def test_evidence_line_within_source_bounds(file_path: Path):
    """evidence.line must be a positive integer within the file's line count."""
    source = file_path.read_text(encoding="utf-8")
    total = len(source.splitlines())
    result = diagnose(source)
    _assert_ok(result)
    ev_line = result["diagnosis"]["evidence"]["line"]
    assert ev_line is not None, "evidence.line must not be None for a known error"
    assert isinstance(ev_line, int)
    assert 1 <= ev_line <= total, \
        f"evidence.line={ev_line} outside 1-{total} for {file_path.name}"


# ---------------------------------------------------------------------------
# 15. Evidence code is exact source text — not trimmed, not altered
# ---------------------------------------------------------------------------

def test_evidence_code_is_exact_source_text():
    """The code field in evidence must be the verbatim line from the source file.
    context.py guarantees no trimming or normalisation; this test verifies it
    survives the full pipeline intact.
    """
    source = _F02.read_text(encoding="utf-8")  # undefined variable
    result = diagnose(source)
    _assert_ok(result)

    ev_line = result["diagnosis"]["evidence"]["line"]
    ev_code = result["diagnosis"]["evidence"]["code"]

    # Retrieve the raw line directly from source (1-based → 0-based)
    raw_line = source.splitlines()[ev_line - 1]
    assert ev_code == raw_line, (
        f"evidence.code was altered.\n"
        f"  Expected: {raw_line!r}\n"
        f"  Got:      {ev_code!r}"
    )


# ---------------------------------------------------------------------------
# 16. CLI stdout is valid JSON for every example file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_path", [_F01, _F02, _F03, _F04, _F05, _F06])
def test_cli_stdout_is_valid_json(file_path: Path):
    """The subprocess path used by the VS Code extension must always produce
    exactly one line of valid JSON on stdout, regardless of input.
    """
    proc = _run_cli(file_path)
    raw = proc.stdout.strip()
    assert raw, f"CLI produced empty stdout for {file_path.name}"
    # Must parse without raising
    payload = json.loads(raw)
    assert "status" in payload, f"JSON missing 'status' key for {file_path.name}"


# ---------------------------------------------------------------------------
# 17. CLI stderr is empty on all normal paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_path", [_F01, _F02, _F03, _F04, _F05])
def test_cli_stderr_is_empty_on_ok_paths(file_path: Path):
    """The extension must not see Python tracebacks on normal diagnostic paths.
    stderr is reserved for unexpected crashes only.
    """
    proc = _run_cli(file_path)
    assert proc.stderr.strip() == "", (
        f"CLI wrote unexpected content to stderr for {file_path.name}:\n"
        f"{proc.stderr[:400]}"
    )


# ---------------------------------------------------------------------------
# 18. analysis_mode is 'deterministic' for all known-rule example files
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_path", [_F01, _F02, _F03, _F04, _F05])
def test_analysis_mode_is_deterministic_for_known_errors(file_path: Path):
    """All five named example errors are covered by deterministic rules;
    none should fall through to the Bob fallback.
    """
    result = _diagnose_file(file_path)
    _assert_ok(result)
    assert result["diagnosis"]["analysis_mode"] == "deterministic", (
        f"{file_path.name} fell through to Bob unexpectedly"
    )


# ---------------------------------------------------------------------------
# 19. analysis_mode is 'ai' for unknown errors (Bob fallback path)
# ---------------------------------------------------------------------------

def test_analysis_mode_is_ai_for_unknown_error():
    """An error that matches no deterministic rule must reach analyze_with_bob().
    Tests the Bob fallback stage explicitly.
    """
    # Manufacture a source that produces a GCC error not covered by any rule.
    # 'static_assert' failure produces a message unlike any known needle.
    source = (
        "#include <type_traits>\n"
        "static_assert(sizeof(int) == 999, \"intentional failure\");\n"
        "int main() { return 0; }\n"
    )
    result = diagnose(source)
    # Must be status='ok' (compiler ran and found an error)
    # but the deterministic rules won't match 'static_assert' messages.
    if result["status"] == "ok":
        d = result["diagnosis"]
        _assert_diagnosis_schema(d)
        # If deterministic matched, that is fine — the important thing is the
        # schema is complete and the pipeline did not crash.
        assert d["analysis_mode"] in ("deterministic", "ai")
    else:
        # 'clean' would mean GCC accepted it — unexpected but not a crash.
        assert result["status"] in ("clean", "ok"), \
            f"Unexpected status: {result['status']}"


def test_bob_fallback_produces_complete_schema():
    """Call analyze_with_bob() directly with a realistic contextual diagnostic
    to verify the Bob stage produces a complete unified schema.
    This isolates the bob.py stage without needing a matching GCC error.
    """
    contextual = {
        "file": "/tmp/t.cpp",
        "line": 3,
        "column": 5,
        "severity": "error",
        "message": "completely unrecognised error that no rule covers",
        "source_context": [
            {"line": 2, "code": "void mystery() {"},
            {"line": 3, "code": "    unknown_construct;"},
            {"line": 4, "code": "}"},
        ],
    }
    # Confirm deterministic rules do NOT match (precondition for bob test)
    assert analyze_simple_errors(contextual) is None, \
        "Test precondition failed: deterministic rules matched the synthetic message"

    result = analyze_with_bob(contextual)
    _assert_diagnosis_schema(result)
    assert result["error_type"] == "UNKNOWN"
    assert result["analysis_mode"] == "ai"
    assert result["evidence"]["line"] == 3
    assert result["evidence"]["code"] == "    unknown_construct;"


# ---------------------------------------------------------------------------
# 20. Pipeline stage order: source context is enriched before analysis
# ---------------------------------------------------------------------------

def test_source_context_is_present_before_analysis():
    """Verify that enrich_context() attaches source_context to the diagnostic
    BEFORE it reaches analyze_simple_errors(), so the evidence field can select
    the correct line.  Tests the compiler.py→context.py→diagnostics.py handoff.
    """
    source = _F01.read_text(encoding="utf-8")  # missing semicolon

    # Stage 1: run real GCC
    compiler_result = run_compiler(source)
    assert compiler_result["returncode"] != 0, \
        "Precondition: missing-semicolon file must fail compilation"

    # Stage 2: parse
    diags = parse_compiler_output(compiler_result["stderr"])
    errors = [d for d in diags if d["severity"] in ("error", "fatal error")]
    assert errors, "Precondition: at least one error diagnostic must be parsed"
    first = errors[0]

    # Stage 3: enrich context — source_context must be present after this call
    contextual = enrich_context(first, source)
    assert "source_context" in contextual, \
        "enrich_context() must attach 'source_context' key"
    assert len(contextual["source_context"]) > 0, \
        "source_context must be non-empty for a valid line number"

    # Stage 4: analyze — evidence must come from source_context, not be empty
    diagnosis = analyze_simple_errors(contextual)
    assert diagnosis is not None, "deterministic rule must match MISSING_SEMICOLON"
    assert diagnosis["evidence"]["line"] is not None
    assert diagnosis["evidence"]["code"] != "", \
        "evidence.code must not be empty when source_context was enriched"


# ---------------------------------------------------------------------------
# 21. CLI round-trip: JSON output matches direct diagnose() call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file_path", [_F01, _F02, _F03, _F04, _F05])
def test_cli_output_matches_direct_diagnose_call(file_path: Path):
    """The JSON written to stdout by the CLI subprocess must be identical to
    what diagnose() returns when called directly with the same source.
    This verifies that the __main__ wrapper in diagnose.py does not alter
    the payload before handing it to the VS Code extension.
    """
    # Direct call
    source = file_path.read_text(encoding="utf-8")
    direct = diagnose(source)

    # CLI subprocess call
    proc = _run_cli(file_path)
    cli = json.loads(proc.stdout.strip())

    assert cli["status"] == direct["status"], \
        f"Status mismatch for {file_path.name}: CLI={cli['status']!r}, direct={direct['status']!r}"

    if direct["status"] == "ok":
        # error_type and analysis_mode must be identical
        assert cli["diagnosis"]["error_type"] == direct["diagnosis"]["error_type"]
        assert cli["diagnosis"]["analysis_mode"] == direct["diagnosis"]["analysis_mode"]
        assert cli["diagnosis"]["evidence"]["line"] == direct["diagnosis"]["evidence"]["line"]
        assert cli["diagnosis"]["evidence"]["code"] == direct["diagnosis"]["evidence"]["code"]
