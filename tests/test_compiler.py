import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from backend.compiler import run_compiler, CompilerResult, parse_compiler_output, Diagnostic

VALID_CPP = """\
#include <iostream>

int main() {
    int x = 10;
    std::cout << x;
    return 0;
}
"""

INVALID_CPP = """\
#include <iostream>

int main() {
    int x = 10
    std::cout << x;
}
"""


def test_run_compiler_valid_cpp():
    """Valid C++ should compile cleanly: returncode 0, captured stdout/stderr."""
    result = run_compiler(VALID_CPP)
    assert result["returncode"] == 0
    assert isinstance(result["stdout"], str)
    assert isinstance(result["stderr"], str)


# ---------------------------------------------------------------------------
# parse_compiler_output — unit tests using representative GCC stderr strings
# ---------------------------------------------------------------------------

# Real GCC on Windows produces paths like:
#   C:\Users\...\AppData\Local\Temp\tmpXXXX.cpp:4:5: error: ...
# Use a representative synthetic path to keep tests portable and deterministic.

_UNIX_PATH = "/tmp/test.cpp"
_WIN_PATH = r"C:\Users\user\AppData\Local\Temp\tmp1234.cpp"


def test_parse_single_error():
    """A single error line is parsed into one Diagnostic with correct fields."""
    raw = f"{_UNIX_PATH}:5:10: error: expected ';' before 'std'\n"
    result = parse_compiler_output(raw)
    assert len(result) == 1
    d = result[0]
    assert d["file"] == _UNIX_PATH
    assert d["line"] == 5
    assert d["column"] == 10
    assert d["severity"] == "error"
    assert "';'" in d["message"]


def test_parse_multiple_diagnostics():
    """Multiple diagnostic lines in one output are each parsed independently."""
    raw = (
        f"{_UNIX_PATH}: In function 'int main()':\n"
        f"{_UNIX_PATH}:4:5: error: expected ',' or ';' before 'std'\n"
        f"    4 |     std::cout << x;\n"
        f"      |     ^~~\n"
        f"{_UNIX_PATH}:6:1: error: expected '}}' at end of input\n"
    )
    result = parse_compiler_output(raw)
    assert len(result) == 2
    assert result[0]["line"] == 4
    assert result[0]["severity"] == "error"
    assert result[1]["line"] == 6
    assert result[1]["severity"] == "error"


def test_parse_warning_and_note():
    """Warning and note severities are recognised."""
    raw = (
        f"{_UNIX_PATH}: In function 'int main()':\n"
        f"{_UNIX_PATH}:3:9: warning: unused variable 'x' [-Wunused-variable]\n"
        f"{_UNIX_PATH}:3:9: note: 'x' declared here\n"
    )
    result = parse_compiler_output(raw)
    severities = {d["severity"] for d in result}
    assert "warning" in severities
    assert "note" in severities
    assert len(result) == 2


def test_parse_windows_path():
    """Windows drive-letter paths (C:\\...) are captured as the full file field."""
    raw = f"{_WIN_PATH}:10:3: error: 'foo' was not declared in this scope\n"
    result = parse_compiler_output(raw)
    assert len(result) == 1
    assert result[0]["file"] == _WIN_PATH
    assert result[0]["line"] == 10
    assert result[0]["column"] == 3


def test_parse_malformed_lines_ignored():
    """Lines that do not match the diagnostic format are silently skipped."""
    raw = (
        "collect2: error: ld returned 1 exit status\n"   # linker summary, no col
        "In file included from /usr/include/stdio.h:28:\n"
        "This is not a diagnostic line at all.\n"
        f"{_UNIX_PATH}:2:1: error: real diagnostic\n"
    )
    result = parse_compiler_output(raw)
    # Only the one properly-formed diagnostic line should survive
    assert len(result) == 1
    assert result[0]["line"] == 2


def test_parse_empty_output():
    """Empty stderr (successful compile) produces an empty list."""
    assert parse_compiler_output("") == []


def test_run_compiler_invalid_cpp():
    """Invalid C++ should fail: non-zero returncode and non-empty stderr."""
    result = run_compiler(INVALID_CPP)
    assert result["returncode"] != 0
    assert result["stderr"] != ""
    assert isinstance(result["stdout"], str)

# ---------------------------------------------------------------------------
# get_source_context — unit tests
# ---------------------------------------------------------------------------

from backend.context import get_source_context

# Source used across context tests: 8 lines, 1-based numbering.
_SOURCE = """\
#include <iostream>

int main() {
    int x = 10
    std::cout << x;
    return 0;
}
"""
# Line map (1-based):
#  1: #include <iostream>
#  2: (empty)
#  3: int main() {
#  4:     int x = 10
#  5:     std::cout << x;
#  6:     return 0;
#  7: }
#  8: (empty — trailing newline produces an empty last element)


def test_context_middle_of_file():
    """Diagnostic in the middle: window of 2 yields 5 lines centred on the error."""
    diag = {"line": 4, "column": 5, "severity": "error", "message": "missing ';'"}
    ctx = get_source_context(diag, _SOURCE)
    line_nums = [e["line"] for e in ctx]
    assert line_nums == [2, 3, 4, 5, 6]
    # The diagnostic line itself must be present.
    assert any(e["line"] == 4 for e in ctx)


def test_context_near_first_line():
    """Diagnostic on line 1: window is clamped — no negative indices."""
    diag = {"line": 1, "column": 1, "severity": "error", "message": "bad include"}
    ctx = get_source_context(diag, _SOURCE)
    line_nums = [e["line"] for e in ctx]
    assert 1 in line_nums
    assert min(line_nums) == 1          # never goes below line 1
    assert max(line_nums) <= 3          # window of 2 above is clamped to nothing


def test_context_near_last_line():
    """Diagnostic on the last line: window is clamped at end of file."""
    lines = _SOURCE.splitlines()
    last = len(lines)
    diag = {"line": last, "column": 1, "severity": "error", "message": "eof"}
    ctx = get_source_context(diag, _SOURCE)
    line_nums = [e["line"] for e in ctx]
    assert last in line_nums
    assert max(line_nums) == last       # never goes past the last line


def test_context_single_line_source():
    """Single-line source: result contains exactly that one line."""
    src = "int x = 10"
    diag = {"line": 1, "column": 1, "severity": "error", "message": "oops"}
    ctx = get_source_context(diag, src)
    assert len(ctx) == 1
    assert ctx[0]["line"] == 1
    assert ctx[0]["code"] == "int x = 10"


def test_context_invalid_line_zero():
    """Line number 0 is out of range — returns empty list, no crash."""
    diag = {"line": 0, "column": 1, "severity": "error", "message": "bad"}
    assert get_source_context(diag, _SOURCE) == []


def test_context_invalid_line_too_large():
    """Line number beyond file length — returns empty list, no crash."""
    diag = {"line": 9999, "column": 1, "severity": "error", "message": "bad"}
    assert get_source_context(diag, _SOURCE) == []


def test_context_missing_line_key():
    """Diagnostic without a 'line' key — returns empty list, no crash."""
    diag = {"column": 1, "severity": "error", "message": "no line"}
    assert get_source_context(diag, _SOURCE) == []


def test_context_preserves_exact_source_text():
    """Source lines are returned verbatim — no trimming or normalisation."""
    src = "   int   x  =  10  ;\n   return   0  ;\n"
    diag = {"line": 1, "column": 1, "severity": "error", "message": "x"}
    ctx = get_source_context(diag, src, window=0)
    assert ctx[0]["code"] == "   int   x  =  10  ;"


def test_context_configurable_window():
    """Explicit window=1 yields at most 3 lines (before, target, after)."""
    diag = {"line": 4, "column": 5, "severity": "error", "message": "x"}
    ctx = get_source_context(diag, _SOURCE, window=1)
    line_nums = [e["line"] for e in ctx]
    assert line_nums == [3, 4, 5]


def test_context_window_zero():
    """window=0 returns only the diagnostic line itself."""
    diag = {"line": 4, "column": 5, "severity": "error", "message": "x"}
    ctx = get_source_context(diag, _SOURCE, window=0)
    assert len(ctx) == 1
    assert ctx[0]["line"] == 4



# ---------------------------------------------------------------------------
# enrich_context — source_line_count and line-number integrity
# ---------------------------------------------------------------------------
# Phase 2 regression tests: ensure enrich_context stores the source length and
# that out-of-bounds diagnostic lines are not silently passed through.

from backend.context import enrich_context

# 15-line source used for out-of-range tests.
_SOURCE_15 = "\n".join(f"line{i}" for i in range(1, 16))  # lines 1–15, no trailing \n


def test_enrich_context_stores_source_line_count():
    """enrich_context must add 'source_line_count' equal to the file length."""
    diag = {"line": 3, "column": 1, "severity": "error", "message": "oops"}
    enriched = enrich_context(diag, _SOURCE_15)
    assert "source_line_count" in enriched, "source_line_count must be present in enriched dict"
    assert enriched["source_line_count"] == 15


def test_enrich_context_line_count_first_line():
    """Valid diagnostic on line 1 — source_line_count still correct."""
    diag = {"line": 1, "column": 1, "severity": "error", "message": "x"}
    enriched = enrich_context(diag, _SOURCE_15)
    assert enriched["source_line_count"] == 15
    assert len(enriched["source_context"]) > 0, "context must be non-empty for valid line 1"
    assert enriched["source_context"][0]["line"] == 1


def test_enrich_context_line_count_last_line():
    """Valid diagnostic on the last line — source_line_count still correct."""
    diag = {"line": 15, "column": 1, "severity": "error", "message": "x"}
    enriched = enrich_context(diag, _SOURCE_15)
    assert enriched["source_line_count"] == 15
    assert any(e["line"] == 15 for e in enriched["source_context"])


def test_enrich_context_out_of_range_line_empty_context():
    """A diagnostic line beyond the source length yields empty source_context."""
    diag = {"line": 19, "column": 1, "severity": "error", "message": "x"}
    enriched = enrich_context(diag, _SOURCE_15)
    assert enriched["source_line_count"] == 15
    assert enriched["source_context"] == [], \
        "source_context must be empty for a line beyond source length"
    # The original compiler line number must be preserved — not clamped
    assert enriched["line"] == 19


def test_enrich_context_line_count_single_line_source():
    """Single-line source: source_line_count == 1."""
    diag = {"line": 1, "column": 1, "severity": "error", "message": "x"}
    enriched = enrich_context(diag, "int x = 0;")
    assert enriched["source_line_count"] == 1
