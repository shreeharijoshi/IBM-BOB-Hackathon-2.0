"""Comprehensive tests for backend/diagnostics.py rule engine.

Covers all rule categories with both GCC and Apple Clang message variants:
  - Missing semicolons (multiple variants)
  - Missing braces, parens, brackets
  - Undefined variables (GCC + Clang)
  - Undefined types
  - Type mismatches (GCC + Clang variants)
  - Wrong arguments (GCC + Clang variants)
  - Missing includes (GCC + Clang variants)
  - Undefined / undeclared functions
  - Missing return values
  - Class / member / access errors
  - Pointer and reference errors
  - Template errors
  - Operator errors
  - Redefinition
  - Linker errors
  - Syntax errors
  - Rule priority and pattern specificity
  - Evidence selection
  - Bob fallback (analyze_with_bob)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
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
    missing = _UNIFIED_KEYS - result.keys()
    assert not missing, f"Missing unified output keys: {missing}"


def _make_diag(message: str, line: int = 1, code: str = "test code") -> dict:
    return {
        "normalized": message,
        "line": line,
        "source_context": [{"line": line, "code": code}],
    }


def _check(message: str, expected_type: str, line: int = 1, code: str = "test code") -> dict:
    diag = _make_diag(message, line, code)
    result = analyze_simple_errors(diag)
    assert result is not None, f"No rule matched for: {message!r}"
    _assert_unified_keys(result)
    assert result["error_type"] == expected_type, (
        f"Expected {expected_type!r}, got {result['error_type']!r} for: {message!r}"
    )
    assert result["analysis_mode"] == "deterministic"
    return result


# ===========================================================================
# MISSING SEMICOLON — GCC and Clang variants
# ===========================================================================

class TestMissingSemicolon:

    def test_gcc_expected_semicolon(self):
        _check("main.cpp:4:5: error: expected ';' before 'return'", "MISSING_SEMICOLON")

    def test_gcc_expected_comma_or_semicolon(self):
        _check("main.cpp:4:5: error: expected ',' or ';' before 'std'", "MISSING_SEMICOLON")

    def test_clang_expected_semicolon_at_end_of_declaration(self):
        _check("main.cpp:2:15: error: expected ';' at end of declaration", "MISSING_SEMICOLON")

    def test_expected_semicolon_after_return(self):
        _check("main.cpp:3:8: error: expected ';' after return statement", "MISSING_SEMICOLON")

    def test_expected_semicolon_after_expression(self):
        _check("main.cpp:5:10: error: expected ';' after expression", "MISSING_SEMICOLON")

    def test_expected_semicolon_before_closing_brace(self):
        _check("main.cpp:6:1: error: expected ';' before '}'", "MISSING_SEMICOLON")

    def test_evidence_on_correct_line(self):
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
        assert result["evidence"]["line"] == 4
        assert result["evidence"]["code"] == "int x = 10"


# ===========================================================================
# MISSING BRACE / PAREN / BRACKET
# ===========================================================================

class TestMissingBrackets:

    def test_expected_closing_brace_at_end_of_input(self):
        _check("main.cpp:10:1: error: expected '}' at end of input", "MISSING_BRACE")

    def test_expected_closing_brace_before(self):
        _check("main.cpp:5:1: error: expected '}' before 'return'", "MISSING_BRACE")

    def test_expected_closing_paren(self):
        _check("main.cpp:3:5: error: expected ')'", "MISSING_PAREN")

    def test_expected_opening_paren(self):
        _check("main.cpp:3:5: error: expected '('", "MISSING_PAREN")

    def test_expected_closing_bracket(self):
        _check("main.cpp:3:5: error: expected ']'", "MISSING_BRACKET")

    def test_unmatched_open_brace(self):
        _check("main.cpp:1:1: error: unmatched '{'", "MISSING_BRACE")


# ===========================================================================
# UNDEFINED VARIABLE — GCC and Clang variants
# ===========================================================================

class TestUndefinedVariable:

    def test_gcc_was_not_declared_in_this_scope(self):
        _check("main.cpp:2:12: error: 'value' was not declared in this scope", "UNDEFINED_VARIABLE")

    def test_clang_use_of_undeclared_identifier(self):
        _check("main.cpp:2:12: error: use of undeclared identifier 'value'", "UNDEFINED_VARIABLE")

    def test_clang_use_of_undeclared_identifier_std(self):
        # std namespace undeclared → MISSING_INCLUDE (higher priority)
        result = _check("main.cpp:2:5: error: use of undeclared identifier 'std'", "MISSING_INCLUDE")
        assert result is not None

    def test_undeclared_identifier_generic(self):
        _check("main.cpp:5:10: error: undeclared identifier 'foo'", "UNDEFINED_VARIABLE")

    def test_identifier_is_undefined(self):
        _check("main.cpp:5:1: error: identifier 'bar' is undefined", "UNDEFINED_VARIABLE")

    def test_was_not_declared_pattern(self):
        _check("main.cpp:3:5: error: 'x' was not declared in this scope", "UNDEFINED_VARIABLE")


# ===========================================================================
# UNDEFINED TYPE
# ===========================================================================

class TestUndefinedType:

    def test_unknown_type_name(self):
        _check("main.cpp:3:1: error: unknown type name 'MyClass'", "UNDEFINED_TYPE")

    def test_unknown_type_name_variant(self):
        _check("main.cpp:1:1: error: unknown type name 'uint32_t'", "UNDEFINED_TYPE")


# ===========================================================================
# TYPE MISMATCH
# ===========================================================================

class TestTypeMismatch:

    def test_gcc_cannot_convert(self):
        _check("main.cpp:2:13: error: cannot convert 'const char*' to 'int'", "TYPE_MISMATCH")

    def test_gcc_invalid_conversion_from(self):
        _check("main.cpp:3:9: error: invalid conversion from 'char*' to 'int'", "TYPE_MISMATCH")

    def test_clang_cannot_initialize_variable(self):
        _check("main.cpp:2:9: error: cannot initialize a variable of type 'int' with an lvalue of type 'const char[6]'", "TYPE_MISMATCH")

    def test_clang_cannot_initialize_return_object(self):
        _check("main.cpp:5:12: error: cannot initialize return object of type 'int' with an lvalue of type 'const char *'", "TYPE_MISMATCH")

    def test_incompatible_types(self):
        _check("main.cpp:4:8: error: incompatible types when assigning to type 'int' from type 'char *'", "TYPE_MISMATCH")

    def test_assigning_from_incompatible_type(self):
        _check("main.cpp:4:5: error: assigning to 'int' from incompatible type 'const char *'", "TYPE_MISMATCH")

    def test_no_viable_conversion(self):
        _check("main.cpp:3:14: error: no viable conversion from 'string' to 'int'", "TYPE_MISMATCH")

    def test_static_cast_not_allowed(self):
        _check("main.cpp:3:5: error: static_cast from 'int*' to 'double*' is not allowed", "TYPE_MISMATCH")

    def test_implicit_conversion_loses(self):
        _check("main.cpp:3:5: warning: implicit conversion loses integer precision", "TYPE_MISMATCH")


# ===========================================================================
# WRONG ARGUMENTS
# ===========================================================================

class TestWrongArguments:

    def test_gcc_no_matching_function(self):
        _check("main.cpp:3:12: error: no matching function for call to 'add(int)'", "WRONG_ARGUMENTS")

    def test_gcc_too_few_arguments(self):
        _check("main.cpp:7:21: error: too few arguments to function 'int add(int, int)'", "WRONG_ARGUMENTS")

    def test_gcc_too_many_arguments(self):
        _check("main.cpp:3:5: error: too many arguments to function 'void greet()'", "WRONG_ARGUMENTS")

    def test_clang_requires_n_arguments(self):
        _check("main.cpp:3:12: note: candidate function not viable: requires 2 arguments, but 1 was provided", "WRONG_ARGUMENTS")

    def test_clang_requires_n_arguments_plural(self):
        _check("main.cpp:5:5: note: requires 3 arguments, but 2 were provided", "WRONG_ARGUMENTS")

    def test_no_viable_overloaded_operator(self):
        _check("main.cpp:8:15: error: no viable overloaded 'operator<<'", "WRONG_ARGUMENTS")


# ===========================================================================
# MISSING INCLUDE
# ===========================================================================

class TestMissingInclude:

    def test_gcc_is_not_member_of_std(self):
        _check("main.cpp:2:10: error: 'cout' is not a member of 'std'", "MISSING_INCLUDE")

    def test_clang_use_of_undeclared_std(self):
        _check("main.cpp:2:5: error: use of undeclared identifier 'std'", "MISSING_INCLUDE")

    def test_no_member_named_in_namespace_std(self):
        _check("main.cpp:2:10: error: no member named 'cout' in namespace 'std'", "MISSING_INCLUDE")

    def test_file_not_found(self):
        _check("main.cpp:1:10: error: 'myheader.h': file not found", "MISSING_INCLUDE")

    def test_no_such_file_or_directory(self):
        _check("main.cpp:1:10: fatal error: myheader.h: No such file or directory", "MISSING_INCLUDE")

    def test_namespace_has_no_member_named(self):
        _check("main.cpp:5:10: error: namespace 'std' has no member named 'cout'", "MISSING_INCLUDE")

    # ------------------------------------------------------------------
    # Regression: old GCC "'std' was not declared in this scope" must
    # NOT be classified as UNDEFINED_VARIABLE (issue #1 in the audit).
    # ------------------------------------------------------------------

    def test_gcc_old_std_not_declared_basic(self):
        """GCC 6: 'std' was not declared → MISSING_INCLUDE, not UNDEFINED_VARIABLE."""
        _check(
            "main.cpp:3:5: error: 'std' was not declared in this scope",
            "MISSING_INCLUDE",
        )

    def test_gcc_old_std_not_declared_beats_generic(self):
        """The specific 'std' rule must win over the generic undeclared rule."""
        result = _check(
            "main.cpp:5:10: error: 'std' was not declared in this scope",
            "MISSING_INCLUDE",
        )
        # Confirm the result points to a missing #include, not a typo/missing decl
        assert "include" in result["suggestion"].lower()

    def test_gcc_old_std_not_declared_with_column(self):
        """Variant with different line/column numbers still classifies correctly."""
        _check(
            "foo.cpp:10:14: error: 'std' was not declared in this scope",
            "MISSING_INCLUDE",
        )

    def test_genuine_undefined_variable_unchanged(self):
        """Genuine user variable ('myVar') must still → UNDEFINED_VARIABLE."""
        _check(
            "main.cpp:4:5: error: 'myVar' was not declared in this scope",
            "UNDEFINED_VARIABLE",
        )

    def test_genuine_undefined_function_unchanged(self):
        """A non-std undeclared name must still → UNDEFINED_VARIABLE."""
        _check(
            "main.cpp:7:3: error: 'compute' was not declared in this scope",
            "UNDEFINED_VARIABLE",
        )


# ===========================================================================
# MEMBER NOT FOUND
# ===========================================================================

class TestMemberNotFound:

    def test_has_no_member_named(self):
        _check("main.cpp:5:10: error: 'struct Point' has no member named 'z'", "MEMBER_NOT_FOUND")

    def test_no_member_named_in_class(self):
        _check("main.cpp:5:10: error: no member named 'length' in 'std::basic_string<char>'", "MEMBER_NOT_FOUND")


# ===========================================================================
# UNDEFINED FUNCTION
# ===========================================================================

class TestUndefinedFunction:

    def test_call_to_undeclared_function(self):
        _check("main.cpp:3:5: error: call to undeclared function 'compute'", "UNDEFINED_FUNCTION")

    def test_call_to_undefined_function(self):
        _check("main.cpp:3:5: error: call to undefined function 'compute'", "UNDEFINED_FUNCTION")

    def test_implicit_declaration_of_function(self):
        _check("main.cpp:3:5: warning: implicit declaration of function 'printf'", "UNDEFINED_FUNCTION")


# ===========================================================================
# MISSING RETURN
# ===========================================================================

class TestMissingReturn:

    def test_function_does_not_return_a_value(self):
        _check("main.cpp:5:1: warning: function does not return a value", "MISSING_RETURN")

    def test_control_reaches_end_of_non_void_function(self):
        _check("main.cpp:5:1: warning: control reaches end of non-void function", "MISSING_RETURN")

    def test_control_may_reach_end(self):
        _check("main.cpp:5:1: warning: control may reach end of non-void function", "MISSING_RETURN")

    def test_non_void_function_should_return_a_value(self):
        _check("main.cpp:5:1: warning: non-void function 'compute' should return a value", "MISSING_RETURN")


# ===========================================================================
# ACCESS VIOLATIONS
# ===========================================================================

class TestAccessViolation:

    def test_private_member_of_class(self):
        _check("main.cpp:8:10: error: 'x' is a private member of 'MyClass'", "ACCESS_VIOLATION")

    def test_private_member_field(self):
        _check("main.cpp:8:10: error: private member 'secret' of class 'Foo'", "ACCESS_VIOLATION")

    def test_is_protected_member(self):
        _check("main.cpp:8:10: error: 'base_val' is protected member", "ACCESS_VIOLATION")


# ===========================================================================
# POINTER ERRORS
# ===========================================================================

class TestPointerErrors:

    def test_indirection_requires_pointer_operand(self):
        _check("main.cpp:5:5: error: indirection requires pointer operand ('int' invalid)", "POINTER_ERROR")

    def test_cannot_take_address_of_rvalue(self):
        _check("main.cpp:5:8: error: cannot take the address of an rvalue of type 'int'", "POINTER_ERROR")

    def test_cannot_dereference_non_pointer(self):
        _check("main.cpp:4:5: error: cannot dereference non-pointer type 'int'", "POINTER_ERROR")

    def test_dereferencing_incomplete_type(self):
        _check("main.cpp:4:5: error: dereferencing pointer to incomplete type 'struct Foo'", "POINTER_ERROR")


# ===========================================================================
# REFERENCE ERRORS
# ===========================================================================

class TestReferenceErrors:

    def test_cannot_bind_non_const(self):
        _check("main.cpp:3:8: error: cannot bind non-const lvalue reference of type 'int&' to an rvalue", "REFERENCE_ERROR")

    def test_drops_const_qualifier(self):
        _check("main.cpp:3:8: error: binding reference of type 'int&' to value of type 'const int' drops const qualifier", "CONST_VIOLATION")


# ===========================================================================
# TEMPLATE ERRORS
# ===========================================================================

class TestTemplateErrors:

    def test_template_argument_deduction_failed(self):
        _check("main.cpp:5:5: error: template argument deduction failed", "TEMPLATE_ERROR")

    def test_template_instantiation_error(self):
        _check("main.cpp:5:5: error: template instantiation error", "TEMPLATE_ERROR")

    def test_in_instantiation_of_function_template(self):
        _check("main.cpp:5:5: note: in instantiation of function template specialization", "TEMPLATE_ERROR")

    def test_invalid_use_of_incomplete_type(self):
        _check("main.cpp:5:5: error: invalid use of incomplete type", "TEMPLATE_ERROR")


# ===========================================================================
# OPERATOR ERRORS
# ===========================================================================

class TestOperatorErrors:

    def test_no_match_for_operator(self):
        _check("main.cpp:5:8: error: no match for operator+ with operands 'int' and 'string'", "OPERATOR_ERROR")

    def test_no_viable_for_operator(self):
        _check("main.cpp:5:8: error: no viable for operator<<", "OPERATOR_ERROR")

    def test_invalid_operands_to_binary_expression(self):
        _check("main.cpp:5:10: error: invalid operands to binary expression ('int' and 'std::string')", "OPERATOR_ERROR")

    def test_expression_not_assignable(self):
        _check("main.cpp:3:5: error: expression is not assignable", "OPERATOR_ERROR")

    def test_lvalue_required_as_left_operand(self):
        _check("main.cpp:3:5: error: lvalue required as left operand of assignment", "OPERATOR_ERROR")


# ===========================================================================
# REDEFINITION
# ===========================================================================

class TestRedefinition:

    def test_redefinition_of(self):
        _check("main.cpp:5:5: error: redefinition of 'main'", "REDEFINITION")

    def test_conflicting_types(self):
        _check("main.cpp:5:5: error: conflicting types for 'foo'", "REDEFINITION")

    def test_conflicting_return_type(self):
        _check("main.cpp:5:5: error: conflicting return type specified for 'bar'", "REDEFINITION")


# ===========================================================================
# LINKER ERRORS
# ===========================================================================

class TestLinkerErrors:

    def test_undefined_reference(self):
        _check("main.cpp:3:5: error: undefined reference to 'compute()'", "LINKER_ERROR")

    def test_undefined_symbol(self):
        _check("main.cpp:3:5: error: undefined symbol to 'MyClass::method'", "LINKER_ERROR")

    def test_multiple_definition(self):
        _check("main.cpp:3:5: error: multiple definition of 'globalVar'", "LINKER_ERROR")

    def test_ld_returned_nonzero(self):
        _check("collect2: error: ld returned 1 exit status", "LINKER_ERROR")


# ===========================================================================
# SYNTAX ERRORS
# ===========================================================================

class TestSyntaxErrors:

    def test_expected_primary_expression(self):
        _check("main.cpp:3:5: error: expected primary-expression before '}'", "SYNTAX_ERROR")

    def test_expected_unqualified_id(self):
        _check("main.cpp:3:5: error: expected unqualified-id before '{' token", "SYNTAX_ERROR")

    def test_expected_expression(self):
        _check("main.cpp:3:5: error: expected expression", "SYNTAX_ERROR")

    def test_stray_in_program(self):
        _check("main.cpp:3:5: error: stray '\\302' in program", "SYNTAX_ERROR")

    def test_auto_requires_initializer(self):
        _check("main.cpp:3:5: error: 'auto' type specifier requires an initializer", "SYNTAX_ERROR")

    def test_expected_class_keyword(self):
        _check("main.cpp:1:10: error: expected 'class' keyword", "SYNTAX_ERROR")


# ===========================================================================
# DIVISION BY ZERO
# ===========================================================================

class TestDivisionByZero:

    def test_division_by_zero(self):
        _check("main.cpp:3:5: error: division by zero", "DIVISION_BY_ZERO")


# ===========================================================================
# DELETED FUNCTION
# ===========================================================================

class TestDeletedFunction:

    def test_use_of_deleted_function(self):
        _check("main.cpp:5:5: error: use of deleted function 'Foo::Foo(const Foo&)'", "DELETED_FUNCTION")

    def test_use_of_deleted_member_function(self):
        _check("main.cpp:5:5: error: use of deleted member function 'Bar::operator=(const Bar&)'", "DELETED_FUNCTION")


# ===========================================================================
# ABSTRACT CLASS
# ===========================================================================

class TestAbstractClass:

    def test_cannot_instantiate_abstract_class(self):
        _check("main.cpp:5:5: error: cannot instantiate abstract class", "ABSTRACT_CLASS")

    def test_object_of_abstract_class_not_allowed(self):
        _check("main.cpp:5:5: error: object of abstract class type 'Base' is not allowed", "ABSTRACT_CLASS")


# ===========================================================================
# WRONG RETURN
# ===========================================================================

class TestWrongReturn:

    def test_void_function_should_not_return(self):
        _check("main.cpp:3:5: warning: void function 'foo' should not return a value", "WRONG_RETURN")


# ===========================================================================
# INCOMPLETE TYPE
# ===========================================================================

class TestIncompleteType:

    def test_member_access_into_incomplete_type(self):
        _check("main.cpp:5:8: error: member access into incomplete type 'Foo'", "INCOMPLETE_TYPE")


# ===========================================================================
# Evidence selection
# ===========================================================================

class TestEvidenceSelection:

    def test_matching_line_beats_first_entry(self):
        diag = {
            "normalized": "main.cpp:5:3: error: expected ';' before 'return'",
            "line": 5,
            "source_context": [
                {"line": 3, "code": "int main() {"},
                {"line": 4, "code": "    int x = 42"},
                {"line": 5, "code": "    return x;"},
            ],
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        assert result["evidence"]["line"] == 5
        assert result["evidence"]["code"] == "    return x;"

    def test_no_match_in_context_returns_safe_empty(self):
        """Phase 3: when diag line is absent from context, return safe empty —
        never fall back to an unrelated first context entry."""
        diag = {
            "normalized": "main.cpp:10:5: error: expected ';' before 'return'",
            "line": 10,
            "source_context": [
                {"line": 8, "code": "void f() {"},
                {"line": 9, "code": "    int x = 0"},
            ],
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        # Line 10 is not in the context window — must NOT fall back to line 8.
        # Safe empty: preserve the compiler line, return no source text.
        assert ev["line"] == 10, (
            "Compiler line must be preserved, not replaced by first context entry"
        )
        assert ev["code"] == "", "No unrelated source text should be returned"
        assert ev.get("line_mismatch") is not True, (
            "No line_mismatch flag without source_line_count"
        )

    def test_no_source_context_still_returns_result(self):
        diag = {
            "normalized": "main.cpp:3:5: error: expected ';' before '}'",
            "line": 3,
            "source_context": [],
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        assert result["evidence"]["line"] == 3
        assert result["evidence"]["code"] == ""

    # ------------------------------------------------------------------
    # Phase 2 regression: line-number integrity / out-of-bounds detection
    # ------------------------------------------------------------------

    def test_valid_first_line_no_mismatch(self):
        """Line 1 diagnostic on a 5-line source — valid, no mismatch flag."""
        diag = {
            "normalized": "main.cpp:1:1: error: expected ';' before '}'",
            "line": 1,
            "source_context": [{"line": 1, "code": "int x = 0"}],
            "source_line_count": 5,
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 1
        assert ev.get("line_mismatch") is None or ev.get("line_mismatch") is False

    def test_valid_middle_line_no_mismatch(self):
        """Line 3 of 5 — valid, no mismatch flag."""
        diag = {
            "normalized": "main.cpp:3:5: error: expected ';' before '}'",
            "line": 3,
            "source_context": [{"line": 3, "code": "    int y = 0"}],
            "source_line_count": 5,
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 3
        assert ev.get("line_mismatch") is None or ev.get("line_mismatch") is False

    def test_valid_last_line_no_mismatch(self):
        """Diagnostic exactly on the last line — valid, no mismatch flag."""
        diag = {
            "normalized": "main.cpp:5:1: error: expected ';' before '}'",
            "line": 5,
            "source_context": [{"line": 5, "code": "}"}],
            "source_line_count": 5,
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 5
        assert ev.get("line_mismatch") is None or ev.get("line_mismatch") is False

    def test_out_of_range_line_sets_mismatch_flag(self):
        """Line 19 on a 15-line source — compiler/source mismatch must be flagged."""
        diag = {
            "normalized": "main.cpp:19:1: error: expected ';' before '}'",
            "line": 19,
            "source_context": [],   # get_source_context returns [] for OOB line
            "source_line_count": 15,
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        # Compiler line preserved — do NOT clamp to 15
        assert ev["line"] == 19, "Compiler line must not be altered or clamped"
        # No invented source text
        assert ev["code"] == "", "No source text must be invented for an OOB line"
        # Mismatch must be flagged
        assert ev.get("line_mismatch") is True, \
            "line_mismatch must be True when compiler line exceeds source length"

    def test_out_of_range_line_no_source_line_count_no_mismatch_flag(self):
        """Without source_line_count (manually built diag), no mismatch flag added."""
        diag = {
            "normalized": "main.cpp:99:1: error: expected ';' before '}'",
            "line": 99,
            "source_context": [],
            # deliberately no source_line_count — simulates old/manual tests
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 99
        assert ev["code"] == ""
        assert "line_mismatch" not in ev, \
            "line_mismatch must not be added when source_line_count is absent"

    def test_unmatched_line_within_range_no_mismatch(self):
        """Phase 3: diag line not in context window but within source_line_count —
        safe empty evidence, no line_mismatch flag."""
        diag = {
            "normalized": "main.cpp:7:3: error: expected ';' before '}'",
            "line": 7,
            "source_context": [
                {"line": 5, "code": "int a = 1;"},
                {"line": 6, "code": "int b = 2;"},
                # line 7 deliberately absent from the window
            ],
            "source_line_count": 20,
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        # Must NOT borrow line 5 or 6 — those are unrelated
        assert ev["line"] == 7, "Compiler line must be preserved"
        assert ev["code"] == "", "No unrelated source text should be returned"
        assert ev.get("line_mismatch") is not True, \
            "No mismatch flag when diag line is within source_line_count"

    def test_exact_match_in_context_with_source_line_count(self):
        """Phase 3: when context entry matches diag line, return it (no mismatch)."""
        diag = {
            "normalized": "main.cpp:4:1: error: expected ';' before '}'",
            "line": 4,
            "source_context": [
                {"line": 2, "code": "int a = 1;"},
                {"line": 4, "code": "int c = 3"},
                {"line": 6, "code": "}"},
            ],
            "source_line_count": 10,
        }
        result = analyze_simple_errors(diag)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 4
        assert ev["code"] == "int c = 3"
        assert ev.get("line_mismatch") is not True


# ===========================================================================
# No match → None
# ===========================================================================

class TestNoMatch:

    def test_completely_unknown_message_returns_none(self):
        diag = {"normalized": "this is a completely unrecognised message XYZ123"}
        result = analyze_simple_errors(diag)
        assert result is None

    def test_empty_message_returns_none(self):
        result = analyze_simple_errors({"normalized": ""})
        assert result is None

    def test_missing_message_returns_none(self):
        result = analyze_simple_errors({})
        assert result is None


# ===========================================================================
# Rule count verification
# ===========================================================================

class TestRuleCoverage:

    def test_rule_count_over_100(self):
        """The rule engine must have at least 100 rule entries."""
        from backend.diagnostics import _RULES
        assert len(_RULES) >= 100, f"Only {len(_RULES)} rules found — need at least 100"

    def test_all_rules_have_required_fields(self):
        """Every rule must have pattern, error_type, and explanation fields."""
        from backend.diagnostics import _RULES
        required = {"error_type", "compiler_explanation", "source_explanation", "what_to_check", "suggestion"}
        for i, rule in enumerate(_RULES):
            assert "pattern" in rule, f"Rule {i} has no 'pattern'"
            missing = required - rule.keys()
            assert not missing, f"Rule {i} ({rule.get('error_type', '?')}) missing fields: {missing}"

    def test_all_patterns_compile(self):
        """All regex patterns must be valid compiled patterns."""
        import re
        from backend.diagnostics import _RULES
        for i, rule in enumerate(_RULES):
            if "pattern" in rule:
                assert hasattr(rule["pattern"], "search"), f"Rule {i} pattern is not a compiled regex"


# ===========================================================================
# Bob fallback
# ===========================================================================

class TestBobFallback:

    def test_empty_input_returns_valid_schema(self):
        result = analyze_with_bob({})
        assert result is not None
        missing = _UNIFIED_KEYS - result.keys()
        assert not missing, f"Missing keys: {missing}"
        assert result["analysis_mode"] == "ai"
        assert result["error_type"] == "UNKNOWN"
        assert result["compiler_message"] == ""
        assert result["evidence"] == {"line": None, "code": ""}

    def test_with_message_and_context(self):
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

    def test_hint_for_template_error(self):
        diag = {"normalized": "template argument deduction error", "line": 1, "source_context": []}
        result = analyze_with_bob(diag)
        assert "template" in result["compiler_explanation"].lower()

    def test_hint_for_conversion_error(self):
        diag = {"normalized": "some cast or conversion issue", "line": 1, "source_context": []}
        result = analyze_with_bob(diag)
        assert "conversion" in result["compiler_explanation"].lower() or "cast" in result["compiler_explanation"].lower()

    def test_never_returns_none(self):
        """analyze_with_bob must always return a dict, never None."""
        for msg in ["", "xyz", "template error", "namespace issue"]:
            result = analyze_with_bob({"normalized": msg})
            assert result is not None
            assert isinstance(result, dict)

    def test_diag_line_not_in_context_window_preserves_compiler_line(self):
        """Regression: GCC line 3 must not become line 5 via the bob fallback.

        When diag_line is absent from the source_context window (e.g. the
        window was clamped or shifted), the old for...else fallback replaced
        evidence.line with source_context[0].line — the first entry of the
        context window — instead of preserving the compiler's reported line.

        For a 5-line file where GCC reports line 3 but the context window only
        contains lines 4 and 5, the old code would return evidence.line=4.
        For a window containing lines 1–5 where GCC reported line 5 (closing
        brace '}'), the old code would return evidence.line=1.

        After the fix, evidence.line must always equal the compiler's reported
        line number regardless of what the context window contains.
        """
        # Scenario: diag_line=3, but the context window happens not to include
        # line 3 (e.g. clamped to lines 4-5 due to a hypothetical edge case).
        # The fix must preserve diag_line=3, not fall back to source_context[0].line=4.
        diag_no_match = {
            "line": 3,
            "source_context": [
                {"line": 4, "code": "    return 0;"},
                {"line": 5, "code": "}"},
            ],
            "message": "some unknown error XYZ",
        }
        result = analyze_with_bob(diag_no_match)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 3, (
            f"evidence.line must be the compiler's line (3), "
            f"not source_context[0].line (4); got {ev['line']}"
        )
        assert ev["code"] == "", "No unrelated source text should be used"

    def test_diag_line_in_context_window_uses_matching_entry(self):
        """When diag_line IS present in the context window, use that entry's code."""
        diag_match = {
            "line": 3,
            "source_context": [
                {"line": 1, "code": "int main(){"},
                {"line": 2, "code": "    int a=0;"},
                {"line": 3, "code": "    cout<<a;"},
                {"line": 4, "code": "    return 0;"},
                {"line": 5, "code": "}"},
            ],
            "message": "some unknown error XYZ",
        }
        result = analyze_with_bob(diag_match)
        assert result is not None
        ev = result["evidence"]
        assert ev["line"] == 3
        assert ev["code"] == "    cout<<a;"

    def test_diag_line_none_with_context_returns_none_line(self):
        """When diag_line is None, evidence.line must be None (not context[0].line)."""
        diag = {
            "line": None,
            "source_context": [
                {"line": 1, "code": "int main(){"},
                {"line": 2, "code": "}"},
            ],
            "message": "unknown",
        }
        result = analyze_with_bob(diag)
        assert result is not None
        assert result["evidence"]["line"] is None, (
            "evidence.line must be None when diag_line is None, "
            f"not source_context[0].line; got {result['evidence']['line']}"
        )

