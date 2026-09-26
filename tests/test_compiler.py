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
