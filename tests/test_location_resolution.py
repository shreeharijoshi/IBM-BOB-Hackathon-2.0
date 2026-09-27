"""Regression tests for root-cause location resolution.

Verifies:
- Missing semicolon (GCC reports next token/line -> resolved to previous statement end)
- Missing semicolon on same line
- Missing braces (GCC reports EOF -> resolved to opening brace)
- Missing parentheses (GCC reports ';' -> resolved to before ';')
- Undefined variable (exact symbol range)
- Wrong arguments (function call range)
- Type mismatch (expression range)
- Missing include (unresolved symbol)
- Function/member errors (member name range)
- Retention of GCC location when root cause cannot be resolved confidently
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.location import resolve_root_cause


def test_missing_semicolon_next_line_reported():
    """When GCC reports line 2 because line 1 is missing ';', root cause must be line 1."""
    source = "int x = 10\nstd::cout << x;\n"
    diag = {
        "file": "main.cpp",
        "line": 2,
        "column": 1,
        "message": "expected ';' before 'std'",
        "source_context": [
            {"line": 1, "code": "int x = 10"},
            {"line": 2, "code": "std::cout << x;"},
        ],
        "source_line_count": 2,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_SEMICOLON", source_code=source)
    assert rc["line"] == 1
    # Line 1 is 'int x = 10' -> length is 10, missing ';' belongs at column 11
    assert rc["column"] == 11
    assert rc["end_column"] == 12
    assert rc["source"] == "int x = 10"


def test_missing_semicolon_before_closing_brace():
    """When GCC reports line 3 (closing brace), root cause must be line 2."""
    source = "int main() {\n    int x = 42\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 3,
        "column": 1,
        "message": "expected ';' before '}' token",
        "source_context": [
            {"line": 1, "code": "int main() {"},
            {"line": 2, "code": "    int x = 42"},
            {"line": 3, "code": "}"},
        ],
        "source_line_count": 3,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_SEMICOLON", source_code=source)
    assert rc["line"] == 2
    assert rc["column"] == len("    int x = 42") + 1
    assert rc["source"] == "    int x = 42"


def test_missing_semicolon_same_line():
    """When two statements are on the same line, missing ';' is on the same line."""
    source = "int x = 10 int y = 20;\n"
    diag = {
        "file": "main.cpp",
        "line": 1,
        "column": 12,
        "message": "expected ';' before 'int'",
        "source_context": [
            {"line": 1, "code": "int x = 10 int y = 20;"},
        ],
        "source_line_count": 1,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_SEMICOLON", source_code=source)
    assert rc["line"] == 1
    assert rc["column"] == 11
    assert rc["source"] == "int x = 10 int y = 20;"


def test_missing_semicolon_at_end_of_declaration():
    """Clang reports 'expected ';' at end of declaration' on the declaration line itself."""
    source = "int main() {\n    int x = 42\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 2,
        "column": 15,
        "message": "expected ';' at end of declaration",
        "source_context": [
            {"line": 1, "code": "int main() {"},
            {"line": 2, "code": "    int x = 42"},
            {"line": 3, "code": "}"},
        ],
        "source_line_count": 3,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_SEMICOLON", source_code=source)
    assert rc["line"] == 2
    assert rc["column"] == len("    int x = 42") + 1


def test_missing_brace_unclosed_block():
    """When closing brace is missing, resolve to the line of the unclosed opening brace."""
    source = "int main() {\n    int x = 10;\n    if (x > 0) {\n        return 1;\n"
    diag = {
        "file": "main.cpp",
        "line": 4,
        "column": 18,
        "message": "expected '}' at end of input",
        "source_context": [
            {"line": 3, "code": "    if (x > 0) {"},
            {"line": 4, "code": "        return 1;"},
        ],
        "source_line_count": 4,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_BRACE", source_code=source)
    # The last unclosed brace was on line 3 at if (x > 0) {
    assert rc["line"] == 3
    assert rc["column"] == 16  # position of '{' in "    if (x > 0) {"


def test_missing_parenthesis_before_semicolon():
    """When ')' is missing before ';', root cause column is right before the semicolon."""
    source = "int main() {\n    if (x > 0;\n    return 0;\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 2,
        "column": 14,
        "message": "expected ')' before ';' token",
        "source_context": [
            {"line": 2, "code": "    if (x > 0;"},
        ],
        "source_line_count": 4,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_PAREN", source_code=source)
    assert rc["line"] == 2
    assert rc["column"] == 13  # right before column 14
    assert rc["end_column"] == 14


def test_undefined_variable_precise_symbol_range():
    """Undefined variable should locate the exact identifier tokens on the line."""
    source = "int main() {\n    return my_secret_var;\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 2,
        "column": 12,
        "message": "'my_secret_var' was not declared in this scope",
        "source_context": [
            {"line": 2, "code": "    return my_secret_var;"},
        ],
        "source_line_count": 3,
    }
    rc = resolve_root_cause(diag, error_type="UNDEFINED_VARIABLE", source_code=source)
    assert rc["line"] == 2
    # In "    return my_secret_var;", my_secret_var starts at col 12 (1-based)
    assert rc["column"] == 12
    assert rc["end_column"] == 12 + len("my_secret_var")


def test_wrong_arguments_call_range():
    """Wrong argument count should span the function call."""
    source = "int add(int a, int b) { return a + b; }\nint main() {\n    return add(1);\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 3,
        "column": 12,
        "message": "no matching function for call to 'add(int)'",
        "source_context": [
            {"line": 3, "code": "    return add(1);"},
        ],
        "source_line_count": 4,
    }
    rc = resolve_root_cause(diag, error_type="WRONG_ARGUMENTS", source_code=source)
    assert rc["line"] == 3
    assert rc["column"] == 12  # 'add' starts at col 12
    assert rc["end_column"] == 18  # closes at ')' (col 18)


def test_type_mismatch_range():
    """Type mismatch should highlight the mismatched expression token."""
    source = 'int main() {\n    int x = "hello";\n    return x;\n}\n'
    diag = {
        "file": "main.cpp",
        "line": 2,
        "column": 13,
        "message": "cannot convert 'const char*' to 'int'",
        "source_context": [
            {"line": 2, "code": '    int x = "hello";'},
        ],
        "source_line_count": 4,
    }
    rc = resolve_root_cause(diag, error_type="TYPE_MISMATCH", source_code=source)
    assert rc["line"] == 2
    assert rc["column"] == 13
    assert rc["end_column"] == 20  # "hello" length is 7


def test_missing_include_symbol():
    """Missing include symbol highlights the symbol needing the include."""
    source = "int main() {\n    std::cout << 42;\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 2,
        "column": 10,
        "message": "'cout' is not a member of 'std'",
        "source_context": [
            {"line": 2, "code": "    std::cout << 42;"},
        ],
        "source_line_count": 3,
    }
    rc = resolve_root_cause(diag, error_type="MISSING_INCLUDE", source_code=source)
    assert rc["line"] == 2
    assert rc["column"] == 10
    assert rc["end_column"] == 14  # 'cout' length 4


def test_member_not_found():
    """Member not found highlights the invalid member name."""
    source = "struct Point { int x; int y; };\nint main() {\n    Point p;\n    return p.z;\n}\n"
    diag = {
        "file": "main.cpp",
        "line": 4,
        "column": 14,
        "message": "'struct Point' has no member named 'z'",
        "source_context": [
            {"line": 4, "code": "    return p.z;"},
        ],
        "source_line_count": 5,
    }
    rc = resolve_root_cause(diag, error_type="MEMBER_NOT_FOUND", source_code=source)
    assert rc["line"] == 4
    assert rc["column"] == 14
    assert rc["end_column"] == 15


def test_unconfident_retains_gcc_location():
    """When error cannot be classified or resolved confidently, retain GCC location."""
    diag = {
        "file": "unknown.cpp",
        "line": 42,
        "column": 7,
        "message": "some completely unknown custom compiler error",
        "source_context": [],
        "source_line_count": 100,
    }
    rc = resolve_root_cause(diag, error_type="UNKNOWN", source_code="")
    assert rc["line"] == 42
    assert rc["column"] == 7
