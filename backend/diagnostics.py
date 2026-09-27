"""Deterministic diagnostic engine for C++ compiler errors.

Covers GCC and Apple Clang message variants with 100+ rules across:
  - Syntax / parsing errors
  - Missing semicolons, brackets, parentheses, braces
  - Undefined variables and scope errors
  - Type mismatches and invalid conversions
  - Function declarations, calls, arguments, return types
  - Classes, objects, constructors, member access
  - Pointers, references, and operators
  - Templates
  - Missing headers and namespaces
  - STL containers, iterators, algorithms
  - Common GCC/Clang diagnostic variations
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Rule schema
# ---------------------------------------------------------------------------
# Each rule has:
#   needle      — plain substring to match (lower-priority than pattern)
#   pattern     — optional compiled regex (preferred over needle when present)
#   error_type  — classification string (e.g. "MISSING_SEMICOLON")
#   priority    — higher number wins on multiple matches (default 0)
#   compiler_explanation, source_explanation, what_to_check, suggestion
# ---------------------------------------------------------------------------

_RULES: list[dict] = [

    # -----------------------------------------------------------------------
    # MISSING SEMICOLON
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"expected ';'", re.IGNORECASE),
        "error_type": "MISSING_SEMICOLON",
        "priority": 10,
        "compiler_explanation": (
            "The compiler reached a token it did not expect because a "
            "semicolon is missing at the end of the previous statement."
        ),
        "source_explanation": (
            "A statement on the line before the flagged location is not "
            "terminated with a semicolon (';')."
        ),
        "what_to_check": (
            "Look at the line immediately before the one the compiler flagged "
            "and confirm it ends with a semicolon."
        ),
        "suggestion": "Add a semicolon (';') at the end of the incomplete statement.",
    },
    {
        "pattern": re.compile(r"expected ',' or ';'", re.IGNORECASE),
        "error_type": "MISSING_SEMICOLON",
        "priority": 10,
        "compiler_explanation": (
            "The compiler expected a comma or semicolon to continue or end "
            "a declaration or statement."
        ),
        "source_explanation": (
            "A declaration or statement is missing a terminating semicolon."
        ),
        "what_to_check": (
            "Check the line before the flagged location for a missing ';' or ','."
        ),
        "suggestion": "Add a semicolon (';') at the end of the incomplete statement.",
    },
    {
        # Apple Clang: "expected ';' at end of declaration"
        "pattern": re.compile(r"expected ';' at end of declaration", re.IGNORECASE),
        "error_type": "MISSING_SEMICOLON",
        "priority": 12,
        "compiler_explanation": (
            "The compiler reached the end of a declaration without finding the "
            "required semicolon terminator."
        ),
        "source_explanation": (
            "A variable declaration is not terminated with a semicolon."
        ),
        "what_to_check": (
            "Check the declaration on the flagged line and confirm it ends with ';'."
        ),
        "suggestion": "Add a semicolon (';') at the end of the declaration.",
    },
    {
        "pattern": re.compile(r"expected ';' after (return|expression|declaration|statement)", re.IGNORECASE),
        "error_type": "MISSING_SEMICOLON",
        "priority": 11,
        "compiler_explanation": "The compiler expected a semicolon after this construct.",
        "source_explanation": "A return, expression, declaration, or statement is missing its terminating semicolon.",
        "what_to_check": "Add ';' at the end of the statement on the flagged line.",
        "suggestion": "Add a semicolon (';') after the statement.",
    },
    {
        "pattern": re.compile(r"expected ';' before '}'", re.IGNORECASE),
        "error_type": "MISSING_SEMICOLON",
        "priority": 11,
        "compiler_explanation": "The compiler found a closing brace before the expected semicolon.",
        "source_explanation": "The last statement inside a block is missing a semicolon before the closing brace.",
        "what_to_check": "Check the last statement inside the block for a missing ';'.",
        "suggestion": "Add a semicolon (';') after the last statement before '}'.",
    },

    # -----------------------------------------------------------------------
    # MISSING CLOSING BRACKET / BRACE / PAREN
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"expected '}' at end of input", re.IGNORECASE),
        "error_type": "MISSING_BRACE",
        "priority": 10,
        "compiler_explanation": (
            "The file ended without the expected closing brace. One or more "
            "opening braces '{' have no matching closing brace '}'."
        ),
        "source_explanation": "A function body or block is opened with '{' but never closed with '}'.",
        "what_to_check": "Count the opening and closing braces in your code; every '{' needs a matching '}'.",
        "suggestion": "Add the missing closing brace '}' at the end of the appropriate block.",
    },
    {
        "pattern": re.compile(r"expected '}' (before|after)", re.IGNORECASE),
        "error_type": "MISSING_BRACE",
        "priority": 10,
        "compiler_explanation": "The compiler expected a closing brace at this point.",
        "source_explanation": "A block opened with '{' is missing its closing brace '}'.",
        "what_to_check": "Verify that all '{' braces have matching '}' braces.",
        "suggestion": "Add the missing closing brace '}'.",
    },
    {
        "pattern": re.compile(r"expected '\)'", re.IGNORECASE),
        "error_type": "MISSING_PAREN",
        "priority": 10,
        "compiler_explanation": "The compiler expected a closing parenthesis.",
        "source_explanation": "A function call, condition, or expression is missing its closing parenthesis ')'.",
        "what_to_check": "Verify that every '(' has a matching ')'.",
        "suggestion": "Add the missing closing parenthesis ')'.",
    },
    {
        "pattern": re.compile(r"expected '\('", re.IGNORECASE),
        "error_type": "MISSING_PAREN",
        "priority": 10,
        "compiler_explanation": "The compiler expected an opening parenthesis.",
        "source_explanation": "A function call, condition, or expression is missing its opening parenthesis '('.",
        "what_to_check": "Verify that every ')' has a matching '('.",
        "suggestion": "Add the missing opening parenthesis '('.",
    },
    {
        "pattern": re.compile(r"expected '\]'", re.IGNORECASE),
        "error_type": "MISSING_BRACKET",
        "priority": 10,
        "compiler_explanation": "The compiler expected a closing bracket ']'.",
        "source_explanation": "An array subscript or declaration is missing its closing bracket.",
        "what_to_check": "Verify that every '[' has a matching ']'.",
        "suggestion": "Add the missing closing bracket ']'.",
    },
    {
        "pattern": re.compile(r"unmatched '\{'", re.IGNORECASE),
        "error_type": "MISSING_BRACE",
        "priority": 10,
        "compiler_explanation": "An opening brace '{' has no matching closing brace.",
        "source_explanation": "A block was opened but never closed.",
        "what_to_check": "Count '{' and '}' characters to find the unclosed block.",
        "suggestion": "Add the missing closing brace '}'.",
    },

    # -----------------------------------------------------------------------
    # UNDEFINED VARIABLE / UNDECLARED IDENTIFIER — GCC and Clang variants
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"was not declared in this scope", re.IGNORECASE),
        "error_type": "UNDEFINED_VARIABLE",
        "priority": 10,
        "compiler_explanation": (
            "The compiler could not find a declaration for this identifier "
            "in the current or any enclosing scope."
        ),
        "source_explanation": (
            "A variable or function name is used before it has been declared, "
            "or it is misspelled."
        ),
        "what_to_check": (
            "Verify the identifier is spelled correctly, that it is declared "
            "before use, and that it is visible in the current scope."
        ),
        "suggestion": "Declare the variable before using it, or fix the spelling if it is a typo.",
    },
    {
        # Apple Clang: "use of undeclared identifier 'x'"
        "pattern": re.compile(r"use of undeclared identifier", re.IGNORECASE),
        "error_type": "UNDEFINED_VARIABLE",
        "priority": 10,
        "compiler_explanation": (
            "The compiler cannot find a declaration for this identifier in any "
            "visible scope."
        ),
        "source_explanation": (
            "A variable, function, or type name is used that has not been "
            "declared in the current scope or any enclosing scope."
        ),
        "what_to_check": (
            "Check the spelling, ensure the variable is declared before use, "
            "and verify the correct headers are included."
        ),
        "suggestion": "Declare the identifier before using it, or add the required #include.",
    },
    {
        "pattern": re.compile(r"undeclared identifier", re.IGNORECASE),
        "error_type": "UNDEFINED_VARIABLE",
        "priority": 9,
        "compiler_explanation": "An identifier used in this expression has not been declared.",
        "source_explanation": "A variable or function name is used without a prior declaration.",
        "what_to_check": "Declare the variable or function, or check for a spelling error.",
        "suggestion": "Add a declaration for the identifier before it is used.",
    },
    {
        "pattern": re.compile(r"'[^']+' (was not declared|is not declared)", re.IGNORECASE),
        "error_type": "UNDEFINED_VARIABLE",
        "priority": 9,
        "compiler_explanation": "The named identifier has not been declared in this scope.",
        "source_explanation": "A name used in the code does not have a visible declaration.",
        "what_to_check": "Declare the name before use, check for typos, and include required headers.",
        "suggestion": "Declare the variable or add the required #include directive.",
    },
    {
        "pattern": re.compile(r"identifier '.*?' is undefined", re.IGNORECASE),
        "error_type": "UNDEFINED_VARIABLE",
        "priority": 9,
        "compiler_explanation": "The identifier has no definition visible at this point.",
        "source_explanation": "The name is used but has no declaration in scope.",
        "what_to_check": "Ensure the variable or function is declared before use.",
        "suggestion": "Add the variable declaration or include the appropriate header.",
    },
    {
        "pattern": re.compile(r"unknown type name", re.IGNORECASE),
        "error_type": "UNDEFINED_TYPE",
        "priority": 10,
        "compiler_explanation": "The compiler does not recognise this as a known type.",
        "source_explanation": "A type name is used that has not been defined or included.",
        "what_to_check": "Check that the type is defined, spelled correctly, and the correct header is included.",
        "suggestion": "Add the required #include or define/typedef the type before using it.",
    },

    # -----------------------------------------------------------------------
    # TYPE MISMATCH / CONVERSION ERRORS
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"cannot convert", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 10,
        "compiler_explanation": (
            "The compiler cannot implicitly convert the value to the required type."
        ),
        "source_explanation": (
            "A value of one type is being assigned or passed where a different, "
            "incompatible type is expected."
        ),
        "what_to_check": (
            "Check that both sides of the assignment or the function argument "
            "and parameter agree on their types."
        ),
        "suggestion": "Use a value of the correct type, or add an explicit cast if the conversion is intentional.",
    },
    {
        "pattern": re.compile(r"invalid conversion from", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 10,
        "compiler_explanation": "The compiler detected an invalid implicit type conversion.",
        "source_explanation": "A value is being converted to an incompatible type without an explicit cast.",
        "what_to_check": "Check the types on both sides of the assignment or in the function call arguments.",
        "suggestion": "Use a value of the correct type, or add an explicit cast if the conversion is intentional.",
    },
    {
        # Apple Clang: "cannot initialize a variable of type 'int' with ..."
        "pattern": re.compile(r"cannot initialize a variable of type '([^']+)' with", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 12,
        "compiler_explanation": (
            "The compiler cannot use the given value to initialize a variable "
            "of the declared type — the types are incompatible."
        ),
        "source_explanation": (
            "The right-hand side of the initialization is a different type than "
            "the variable being declared."
        ),
        "what_to_check": "Ensure the initializer value matches the declared variable type.",
        "suggestion": "Change the initializer to match the variable type, or cast the value explicitly.",
    },
    {
        # Apple Clang: "cannot initialize return object of type 'X' with an lvalue of type 'Y'"
        "pattern": re.compile(r"cannot initialize return object of type", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 12,
        "compiler_explanation": "The return value type does not match the declared return type of the function.",
        "source_explanation": "The value returned by the function is a different type than the function's declared return type.",
        "what_to_check": "Check the function's declared return type and ensure the returned value matches it.",
        "suggestion": "Change the returned value to match the function's return type, or update the return type declaration.",
    },
    {
        "pattern": re.compile(r"incompatible types", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 9,
        "compiler_explanation": "The types of the operands or assignment sides are incompatible.",
        "source_explanation": "An operation is attempted between values of incompatible types.",
        "what_to_check": "Ensure both sides of the operation use compatible types.",
        "suggestion": "Cast one operand to the correct type, or change the variable types to match.",
    },
    {
        "pattern": re.compile(r"assigning to '([^']+)' from incompatible type", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 11,
        "compiler_explanation": "The value being assigned is not compatible with the variable's type.",
        "source_explanation": "The right-hand side of an assignment has a different, incompatible type.",
        "what_to_check": "Ensure the assigned value matches the variable's declared type.",
        "suggestion": "Use a value of the correct type or add an explicit cast.",
    },
    {
        "pattern": re.compile(r"implicit conversion (from|loses)", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 9,
        "compiler_explanation": "An implicit type conversion may lose information.",
        "source_explanation": "A value is implicitly narrowed or converted to a smaller or incompatible type.",
        "what_to_check": "Check whether the conversion is intentional and whether data loss is acceptable.",
        "suggestion": "Use an explicit cast to make the conversion intentional, or use a type that fits the data.",
    },
    {
        "pattern": re.compile(r"no viable conversion from '([^']+)' to '([^']+)'", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 11,
        "compiler_explanation": "The compiler found no way to convert between these two types.",
        "source_explanation": "No implicit or explicit conversion exists from the source type to the target type.",
        "what_to_check": "Check the types involved and whether a conversion operator or constructor is needed.",
        "suggestion": "Provide an explicit conversion operator or constructor, or change the types to be compatible.",
    },
    {
        "pattern": re.compile(r"static_cast from '([^']+)' to '([^']+)' is not allowed", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 10,
        "compiler_explanation": "The requested static_cast between these types is not permitted.",
        "source_explanation": "The two types are not related in a way that allows a static_cast.",
        "what_to_check": "Consider whether reinterpret_cast or a user-defined conversion is more appropriate.",
        "suggestion": "Use the correct cast operator for the types involved, or redesign to avoid the cast.",
    },

    # -----------------------------------------------------------------------
    # FUNCTION / ARGUMENT ERRORS
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"no matching function for call to", re.IGNORECASE),
        "error_type": "WRONG_ARGUMENTS",
        "priority": 10,
        "compiler_explanation": (
            "The compiler could not find an overload of the function that "
            "accepts the argument types or count provided."
        ),
        "source_explanation": (
            "The function is called with the wrong number of arguments or "
            "with arguments of types that do not match any declared overload."
        ),
        "what_to_check": (
            "Compare the call site with the function declaration: check the "
            "number of arguments and their types."
        ),
        "suggestion": "Adjust the call to match the function signature — ensure the correct number and types of arguments are passed.",
    },
    {
        "pattern": re.compile(r"too few arguments to function", re.IGNORECASE),
        "error_type": "WRONG_ARGUMENTS",
        "priority": 10,
        "compiler_explanation": "The function call provides fewer arguments than required.",
        "source_explanation": "One or more required parameters have not been supplied.",
        "what_to_check": "Compare the call site with the function declaration: count the number of parameters.",
        "suggestion": "Add the missing argument(s) to the function call to match the function signature.",
    },
    {
        "pattern": re.compile(r"too many arguments to function", re.IGNORECASE),
        "error_type": "WRONG_ARGUMENTS",
        "priority": 10,
        "compiler_explanation": "The function call provides more arguments than the function accepts.",
        "source_explanation": "Extra values are being passed that the function does not have parameters for.",
        "what_to_check": "Compare the call site with the function declaration: count the number of parameters.",
        "suggestion": "Remove the extra argument(s) from the function call.",
    },
    {
        # Apple Clang: "no matching function for call to 'foo'" with "requires N arguments, but M was provided" note
        "pattern": re.compile(r"requires \d+ argument[s]?, but \d+ (was|were) provided", re.IGNORECASE),
        "error_type": "WRONG_ARGUMENTS",
        "priority": 11,
        "compiler_explanation": "The function requires a different number of arguments than were provided.",
        "source_explanation": "The call site does not match the function's parameter list.",
        "what_to_check": "Check the function's declaration and provide the correct number of arguments.",
        "suggestion": "Adjust the function call to provide the exact number of arguments the function expects.",
    },
    {
        "pattern": re.compile(r"no viable overloaded '(operator\S+|operator \S+)'", re.IGNORECASE),
        "error_type": "WRONG_ARGUMENTS",
        "priority": 10,
        "compiler_explanation": "No overload of the operator matches the operand types.",
        "source_explanation": "An operator (like <<, >>, +, etc.) is used with operand types that have no matching overload.",
        "what_to_check": "Check the types of the operands and whether the required operator overload exists.",
        "suggestion": "Define the operator overload for the types used, or convert the operands to compatible types.",
    },
    {
        "pattern": re.compile(r"call to (undefined|undeclared) function", re.IGNORECASE),
        "error_type": "UNDEFINED_FUNCTION",
        "priority": 10,
        "compiler_explanation": "The function called has not been declared.",
        "source_explanation": "A function is being called before it is declared, or the declaration is missing.",
        "what_to_check": "Declare or define the function before calling it, or include the appropriate header.",
        "suggestion": "Add a function declaration (prototype) before the call site, or include the header that declares the function.",
    },
    {
        "pattern": re.compile(r"implicit declaration of function", re.IGNORECASE),
        "error_type": "UNDEFINED_FUNCTION",
        "priority": 10,
        "compiler_explanation": "The function is used without a prior declaration (C-style implicit declaration).",
        "source_explanation": "In C++, functions must be declared before use. This function has no visible declaration.",
        "what_to_check": "Add a function prototype or #include the header that declares this function.",
        "suggestion": "Declare the function before calling it, or include the appropriate header.",
    },
    {
        "pattern": re.compile(r"'([^']+)' is not a function", re.IGNORECASE),
        "error_type": "WRONG_CALL",
        "priority": 10,
        "compiler_explanation": "The identifier is being called as a function but is not a function.",
        "source_explanation": "A variable or type name is used with function-call syntax '()' but is not callable.",
        "what_to_check": "Check whether you intended to call a function by this name, or if there is a naming conflict.",
        "suggestion": "Ensure the identifier refers to a callable function, not a variable or type.",
    },
    {
        "pattern": re.compile(r"function does not return a value", re.IGNORECASE),
        "error_type": "MISSING_RETURN",
        "priority": 10,
        "compiler_explanation": "A non-void function reaches the end without a return statement.",
        "source_explanation": "The function has a non-void return type but at least one execution path does not return a value.",
        "what_to_check": "Ensure every code path in the function ends with a return statement that provides a value.",
        "suggestion": "Add a return statement with an appropriate value to all exit paths of the function.",
    },
    {
        "pattern": re.compile(r"control (reaches|may reach) end of non-void function", re.IGNORECASE),
        "error_type": "MISSING_RETURN",
        "priority": 10,
        "compiler_explanation": "The compiler detected that control can reach the end of a non-void function without returning.",
        "source_explanation": "A non-void function has an execution path with no return value.",
        "what_to_check": "Add a return statement to all branches of the function.",
        "suggestion": "Ensure every execution path in the function returns a value of the declared type.",
    },
    {
        "pattern": re.compile(r"non-void function '([^']+)' should return a value", re.IGNORECASE),
        "error_type": "MISSING_RETURN",
        "priority": 11,
        "compiler_explanation": "A function with a non-void return type does not return a value on some path.",
        "source_explanation": "The function may reach the end without executing a return statement.",
        "what_to_check": "Check all branches of the function and ensure each returns a value.",
        "suggestion": "Add 'return <value>;' before the closing brace of the function.",
    },
    {
        "pattern": re.compile(r"return type '([^']+)' must match previous return type", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 10,
        "compiler_explanation": "Conflicting return types in a function definition.",
        "source_explanation": "The function's return type in its definition does not match an earlier declaration.",
        "what_to_check": "Ensure the return type in the function definition matches its declaration exactly.",
        "suggestion": "Make the return type consistent between the declaration and definition.",
    },
    {
        "pattern": re.compile(r"void function '([^']+)' should not return a value", re.IGNORECASE),
        "error_type": "WRONG_RETURN",
        "priority": 10,
        "compiler_explanation": "A void function has a return statement that returns a value.",
        "source_explanation": "A function declared as void is trying to return a value.",
        "what_to_check": "Remove the return value, or change the function's return type to match the value being returned.",
        "suggestion": "Change 'return <value>;' to just 'return;', or change the return type of the function.",
    },

    # -----------------------------------------------------------------------
    # MISSING INCLUDE / NAMESPACE
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"is not a member of 'std'", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 10,
        "compiler_explanation": (
            "The compiler does not recognise the identifier as a member of the "
            "'std' namespace because the required standard-library header has "
            "not been included."
        ),
        "source_explanation": (
            "A standard-library symbol such as 'std::cout' or 'std::string' is "
            "used without the corresponding #include directive."
        ),
        "what_to_check": "Check that the required #include (e.g. #include <iostream>) is present at the top of the file.",
        "suggestion": "Add the missing #include directive for the standard-library header that defines the symbol.",
    },
    {
        # Apple Clang: "use of undeclared identifier 'std'" (happens when no std headers included)
        "pattern": re.compile(r"use of undeclared identifier 'std'", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 15,
        "compiler_explanation": (
            "The 'std' namespace is not visible because no standard-library "
            "headers have been included."
        ),
        "source_explanation": (
            "Code is using std:: qualified names but no #include directives "
            "for the standard library are present."
        ),
        "what_to_check": "Check the top of the file for the required #include (e.g. #include <iostream> for std::cout).",
        "suggestion": "Add the required #include at the top of your file (e.g. #include <iostream>).",
    },
    {
        "pattern": re.compile(r"no member named '([^']+)' in namespace 'std'", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 12,
        "compiler_explanation": "The std namespace does not contain this member at this point in compilation.",
        "source_explanation": "The required header for this std:: symbol has not been included.",
        "what_to_check": "Check which header defines this std:: symbol and add the #include.",
        "suggestion": "Add the appropriate #include for the standard library symbol you are using.",
    },
    {
        "pattern": re.compile(r"no member named '([^']+)' in '([^']+)'", re.IGNORECASE),
        "error_type": "MEMBER_NOT_FOUND",
        "priority": 10,
        "compiler_explanation": "The named member does not exist in the specified type or namespace.",
        "source_explanation": "A member access uses a name that is not defined in the struct, class, or namespace.",
        "what_to_check": "Check the spelling of the member name and the type's definition.",
        "suggestion": "Correct the member name, or check that the correct type is being accessed.",
    },
    {
        "pattern": re.compile(r"has no member named", re.IGNORECASE),
        "error_type": "MEMBER_NOT_FOUND",
        "priority": 10,
        "compiler_explanation": "The class or struct does not have the accessed member.",
        "source_explanation": "A member variable or function is accessed that is not defined in the class/struct.",
        "what_to_check": "Check the class definition to confirm the member name exists and is spelled correctly.",
        "suggestion": "Fix the member name, or add the member to the class definition.",
    },
    {
        "pattern": re.compile(r"no include path in which to search for", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 10,
        "compiler_explanation": "The compiler cannot find the specified header file in its include search paths.",
        "source_explanation": "A #include directive references a file that cannot be found.",
        "what_to_check": "Verify the include path is correct and the file exists in a directory on the include path.",
        "suggestion": "Check the filename spelling, add the directory to the compiler include path, or install the missing library.",
    },
    {
        "pattern": re.compile(r"'([^']+)': file not found", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 11,
        "compiler_explanation": "The compiler cannot find the included header file.",
        "source_explanation": "A #include directive references a header file that does not exist in the include search paths.",
        "what_to_check": "Verify the header file name is correct and the include path is set up properly.",
        "suggestion": "Check the filename spelling, add the missing include directory, or install the required library.",
    },
    {
        "pattern": re.compile(r"no such file or directory", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 10,
        "compiler_explanation": "The compiler cannot find the included file.",
        "source_explanation": "A #include directive references a file that does not exist at the expected path.",
        "what_to_check": "Verify the header name is spelled correctly and that the library is installed.",
        "suggestion": "Correct the filename or install the missing library package.",
    },
    {
        "pattern": re.compile(r"namespace '([^']+)' has no member named", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 11,
        "compiler_explanation": "The accessed name does not exist in the namespace, possibly because a header is missing.",
        "source_explanation": "The namespace does not expose this name — either the wrong namespace or a missing include.",
        "what_to_check": "Include the appropriate header and verify the correct namespace is used.",
        "suggestion": "Add the required #include or correct the namespace qualifier.",
    },

    # -----------------------------------------------------------------------
    # CLASS / OBJECT / CONSTRUCTOR
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"no matching constructor for initialization", re.IGNORECASE),
        "error_type": "WRONG_CONSTRUCTOR",
        "priority": 10,
        "compiler_explanation": "No constructor matches the argument types used in the initialization.",
        "source_explanation": "The class has no constructor that accepts the provided argument types.",
        "what_to_check": "Check the class's available constructors and the types of the arguments you are passing.",
        "suggestion": "Use an argument type that matches an existing constructor, or add a new constructor.",
    },
    {
        "pattern": re.compile(r"no matching (constructor|conversion function) for", re.IGNORECASE),
        "error_type": "WRONG_CONSTRUCTOR",
        "priority": 10,
        "compiler_explanation": "No constructor or conversion function matches the argument types.",
        "source_explanation": "An object construction or conversion uses incompatible types.",
        "what_to_check": "Verify the constructor arguments match a declared constructor signature.",
        "suggestion": "Correct the constructor arguments or add a matching constructor overload.",
    },
    {
        "pattern": re.compile(r"private member '([^']+)' of class '([^']+)'", re.IGNORECASE),
        "error_type": "ACCESS_VIOLATION",
        "priority": 10,
        "compiler_explanation": "A private member of the class is being accessed from outside the class.",
        "source_explanation": "The code tries to access a private field or method directly from a non-member context.",
        "what_to_check": "Check the class definition — use a public getter/setter or make the access internal.",
        "suggestion": "Access private members only from within the class or through a public interface.",
    },
    {
        "pattern": re.compile(r"'([^']+)' is (a private|private|protected) member", re.IGNORECASE),
        "error_type": "ACCESS_VIOLATION",
        "priority": 10,
        "compiler_explanation": "The member cannot be accessed from the current context due to access control.",
        "source_explanation": "A private or protected member is being accessed from outside its allowed scope.",
        "what_to_check": "Use a public method to access the private/protected member, or change the access specifier.",
        "suggestion": "Provide a public accessor method or declare the accessing code as a friend.",
    },
    {
        "pattern": re.compile(r"member access into incomplete type", re.IGNORECASE),
        "error_type": "INCOMPLETE_TYPE",
        "priority": 10,
        "compiler_explanation": "A member is being accessed on a type that is only forward-declared, not fully defined.",
        "source_explanation": "The class or struct must be fully defined (not just declared) before its members can be accessed.",
        "what_to_check": "Ensure the full class definition is included before accessing members.",
        "suggestion": "Move the #include for the full class definition before the point of member access.",
    },
    {
        "pattern": re.compile(r"(cannot|not allowed to) instantiate abstract class", re.IGNORECASE),
        "error_type": "ABSTRACT_CLASS",
        "priority": 10,
        "compiler_explanation": "A class with pure virtual functions cannot be instantiated directly.",
        "source_explanation": "An abstract class (with at least one pure virtual function) is being created with 'new' or as a local variable.",
        "what_to_check": "Override all pure virtual functions in a derived class and instantiate the derived class instead.",
        "suggestion": "Create a concrete derived class that implements all pure virtual methods.",
    },
    {
        "pattern": re.compile(r"object of abstract class type '([^']+)' is not allowed", re.IGNORECASE),
        "error_type": "ABSTRACT_CLASS",
        "priority": 11,
        "compiler_explanation": "Cannot instantiate an abstract class — it has unimplemented pure virtual functions.",
        "source_explanation": "An attempt is made to create an object of a class that is abstract.",
        "what_to_check": "Implement all pure virtual methods in a derived class.",
        "suggestion": "Create a derived class that overrides all pure virtual methods, then instantiate that derived class.",
    },

    # -----------------------------------------------------------------------
    # POINTERS AND REFERENCES
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"indirection requires pointer operand", re.IGNORECASE),
        "error_type": "POINTER_ERROR",
        "priority": 10,
        "compiler_explanation": "The dereference operator '*' is applied to a non-pointer value.",
        "source_explanation": "A non-pointer variable is being dereferenced with '*', which is only valid for pointer types.",
        "what_to_check": "Ensure the variable being dereferenced is declared as a pointer.",
        "suggestion": "Remove the '*' dereference operator, or change the variable to a pointer type.",
    },
    {
        "pattern": re.compile(r"cannot take the address of an rvalue", re.IGNORECASE),
        "error_type": "POINTER_ERROR",
        "priority": 10,
        "compiler_explanation": "The address-of operator '&' is applied to a temporary (rvalue), which has no address.",
        "source_explanation": "Only lvalues (named variables or stable memory locations) have addresses.",
        "what_to_check": "Store the temporary value in a named variable first, then take its address.",
        "suggestion": "Assign the value to a local variable before taking its address.",
    },
    {
        "pattern": re.compile(r"cannot dereference non-pointer type", re.IGNORECASE),
        "error_type": "POINTER_ERROR",
        "priority": 10,
        "compiler_explanation": "A dereference operation is attempted on a non-pointer type.",
        "source_explanation": "The '*' operator is used on a value that is not a pointer.",
        "what_to_check": "Check whether the variable is a pointer; remove the dereference if not.",
        "suggestion": "Remove the '*' operator or declare the variable as a pointer.",
    },
    {
        "pattern": re.compile(r"dereferencing pointer to incomplete type", re.IGNORECASE),
        "error_type": "POINTER_ERROR",
        "priority": 10,
        "compiler_explanation": "A pointer is dereferenced, but the pointed-to type is only forward-declared.",
        "source_explanation": "The type pointed to by the pointer needs a full definition before it can be dereferenced.",
        "what_to_check": "Include the full type definition before dereferencing the pointer.",
        "suggestion": "Add the full class/struct definition with #include before the dereference point.",
    },
    {
        "pattern": re.compile(r"binding reference of type '([^']+)' to value of type '([^']+)' drops (const|volatile) qualifier", re.IGNORECASE),
        "error_type": "CONST_VIOLATION",
        "priority": 10,
        "compiler_explanation": "A reference is binding to a value but discarding a const or volatile qualifier.",
        "source_explanation": "A non-const reference cannot bind to a const value.",
        "what_to_check": "Declare the reference as const if you only need to read the value.",
        "suggestion": "Change the reference to 'const T&' or use a const_cast if intentional.",
    },
    {
        "pattern": re.compile(r"cannot bind (non-const|rvalue reference of type)", re.IGNORECASE),
        "error_type": "REFERENCE_ERROR",
        "priority": 10,
        "compiler_explanation": "A reference binding constraint is violated.",
        "source_explanation": "A non-const lvalue reference cannot bind to a temporary or a const variable.",
        "what_to_check": "Use a const reference or a value parameter instead of a non-const reference.",
        "suggestion": "Change the parameter or variable to 'const T&' or pass by value.",
    },

    # -----------------------------------------------------------------------
    # TEMPLATES
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"template (instantiation|argument) (deduction (failed|error)|error)", re.IGNORECASE),
        "error_type": "TEMPLATE_ERROR",
        "priority": 10,
        "compiler_explanation": "The compiler cannot deduce the template arguments from the provided arguments.",
        "source_explanation": "Template type deduction failed because the arguments don't match the template parameter requirements.",
        "what_to_check": "Specify the template arguments explicitly, or ensure the argument types match the template parameters.",
        "suggestion": "Provide explicit template arguments like func<int>() or ensure argument types are correct.",
    },
    {
        "pattern": re.compile(r"in instantiation of (function|class|member) template", re.IGNORECASE),
        "error_type": "TEMPLATE_ERROR",
        "priority": 9,
        "compiler_explanation": "The error occurred inside a template instantiation.",
        "source_explanation": "A template was instantiated with types that trigger an error inside the template body.",
        "what_to_check": "Check the template parameter constraints and the types used in the instantiation.",
        "suggestion": "Ensure the types used to instantiate the template satisfy all type requirements.",
    },
    {
        "pattern": re.compile(r"invalid use of (incomplete type|template)", re.IGNORECASE),
        "error_type": "TEMPLATE_ERROR",
        "priority": 10,
        "compiler_explanation": "A template or incomplete type is used in a way that is not permitted.",
        "source_explanation": "The template instantiation uses a type that is not yet complete at this point.",
        "what_to_check": "Ensure the type is fully defined before it is used in a template.",
        "suggestion": "Move the full type definition before the template instantiation.",
    },
    {
        "pattern": re.compile(r"'typename' (keyword|specifier) used outside of template", re.IGNORECASE),
        "error_type": "TEMPLATE_ERROR",
        "priority": 10,
        "compiler_explanation": "The 'typename' keyword is used where it is not applicable.",
        "source_explanation": "'typename' is only valid inside template definitions to disambiguate dependent type names.",
        "what_to_check": "Remove 'typename' from non-template contexts.",
        "suggestion": "Remove the 'typename' keyword, or move the code into a template context.",
    },

    # -----------------------------------------------------------------------
    # STL CONTAINERS / ITERATORS
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"no (type|member) named 'iterator' in '([^']+)'", re.IGNORECASE),
        "error_type": "ITERATOR_ERROR",
        "priority": 10,
        "compiler_explanation": "The type used does not have an iterator member type.",
        "source_explanation": "An iterator is requested from a type that does not support iteration.",
        "what_to_check": "Ensure you are using a container type that supports iterators (std::vector, std::list, etc.).",
        "suggestion": "Use a standard container type or define the iterator type in your class.",
    },
    {
        "pattern": re.compile(r"size_type.*not a member of.*std", re.IGNORECASE),
        "error_type": "MISSING_INCLUDE",
        "priority": 10,
        "compiler_explanation": "The std::size_type is not visible because the required header is missing.",
        "source_explanation": "A container size type is used without including the correct header.",
        "what_to_check": "Include the header for the container type being used.",
        "suggestion": "Add the appropriate #include for the container (e.g. #include <vector>).",
    },
    {
        "pattern": re.compile(r"use of class template '([^']+)' requires template arguments", re.IGNORECASE),
        "error_type": "TEMPLATE_ERROR",
        "priority": 10,
        "compiler_explanation": "A class template is used without specifying its required type arguments.",
        "source_explanation": "Class templates like std::vector or std::map require type parameters.",
        "what_to_check": "Provide the template arguments for the class template (e.g. std::vector<int>).",
        "suggestion": "Add the template type argument (e.g. change 'vector' to 'vector<int>').",
    },

    # -----------------------------------------------------------------------
    # OPERATORS
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"(no match|no viable) for operator", re.IGNORECASE),
        "error_type": "OPERATOR_ERROR",
        "priority": 10,
        "compiler_explanation": "No matching operator overload is found for the given operand types.",
        "source_explanation": "An operator is used with types for which no matching overload exists.",
        "what_to_check": "Verify the operand types are compatible with the operator, or define the operator overload.",
        "suggestion": "Convert the operands to compatible types or define the operator overload.",
    },
    {
        "pattern": re.compile(r"overloaded '([^']+)' not found", re.IGNORECASE),
        "error_type": "OPERATOR_ERROR",
        "priority": 10,
        "compiler_explanation": "The compiler could not find a matching overloaded operator.",
        "source_explanation": "The overloaded operator is not defined for the types used in the expression.",
        "what_to_check": "Define the operator overload for the types involved.",
        "suggestion": "Add an operator overload function that handles the types used.",
    },
    {
        "pattern": re.compile(r"invalid operands to binary expression", re.IGNORECASE),
        "error_type": "OPERATOR_ERROR",
        "priority": 10,
        "compiler_explanation": "The binary operator has operands of incompatible types.",
        "source_explanation": "A binary operation (like + or -) is attempted with types that don't support the operation.",
        "what_to_check": "Ensure both operands are of compatible types for the binary operation.",
        "suggestion": "Convert the operands to compatible types or define the operator overload.",
    },
    {
        "pattern": re.compile(r"expression is not assignable", re.IGNORECASE),
        "error_type": "OPERATOR_ERROR",
        "priority": 10,
        "compiler_explanation": "The left-hand side of an assignment is not assignable (not an lvalue or is const).",
        "source_explanation": "An attempt is made to assign to a const variable, a literal, or a temporary.",
        "what_to_check": "Ensure the left-hand side of the assignment is a non-const lvalue.",
        "suggestion": "Remove the const qualifier from the variable, or change the code to avoid assigning to a non-assignable expression.",
    },
    {
        "pattern": re.compile(r"lvalue required as (left operand of|unary|assignment target)", re.IGNORECASE),
        "error_type": "OPERATOR_ERROR",
        "priority": 10,
        "compiler_explanation": "An assignable location (lvalue) is required but a temporary or literal was provided.",
        "source_explanation": "An assignment or address-of operator needs a named memory location on the left side.",
        "what_to_check": "Ensure the target of the assignment is a named variable, not a literal or temporary.",
        "suggestion": "Assign to a named variable instead of a literal or temporary expression.",
    },

    # -----------------------------------------------------------------------
    # REDEFINITION / DUPLICATE
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"redefinition of '([^']+)'", re.IGNORECASE),
        "error_type": "REDEFINITION",
        "priority": 10,
        "compiler_explanation": "The same name is defined more than once in the same scope.",
        "source_explanation": "A variable, function, or class is defined multiple times, which is not allowed.",
        "what_to_check": "Search for all definitions of this name and remove the duplicate.",
        "suggestion": "Remove the duplicate definition or rename one of the definitions.",
    },
    {
        "pattern": re.compile(r"conflicting (types|return type|declaration)", re.IGNORECASE),
        "error_type": "REDEFINITION",
        "priority": 10,
        "compiler_explanation": "A name is declared or defined with conflicting types.",
        "source_explanation": "The same function or variable is declared with different types in different places.",
        "what_to_check": "Ensure that all declarations of the same name use the same type.",
        "suggestion": "Make the type consistent across all declarations and definitions.",
    },
    {
        "pattern": re.compile(r"previous definition is here", re.IGNORECASE),
        "error_type": "REDEFINITION",
        "priority": 8,
        "compiler_explanation": "This note indicates where the first definition appears.",
        "source_explanation": "A name is defined multiple times — this shows where the earlier definition is.",
        "what_to_check": "Compare the two definitions and remove or rename the duplicate.",
        "suggestion": "Remove the duplicate definition or rename one instance.",
    },

    # -----------------------------------------------------------------------
    # SYNTAX / PARSE ERRORS
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"expected (primary-expression|unqualified-id)", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "The compiler expected a valid expression but found unexpected text.",
        "source_explanation": "A statement or expression is malformed — a keyword, operator, or identifier is out of place.",
        "what_to_check": "Check the syntax at the flagged location for misplaced operators, missing identifiers, or typos.",
        "suggestion": "Fix the syntax error at the indicated location.",
    },
    {
        "pattern": re.compile(r"expected expression", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "The compiler expected a valid expression at this point.",
        "source_explanation": "The code has a missing or malformed expression.",
        "what_to_check": "Inspect the flagged line for a missing value, operator, or closing token.",
        "suggestion": "Provide a valid expression at the indicated location.",
    },
    {
        "pattern": re.compile(r"stray '([^']+)' in program", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A character that has no syntactic meaning in this context was found.",
        "source_explanation": "An unexpected or non-ASCII character exists in the source code.",
        "what_to_check": "Look for invisible characters, incorrectly copied text, or encoding issues on the flagged line.",
        "suggestion": "Remove the stray character from the source file.",
    },
    {
        "pattern": re.compile(r"unexpected token", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 9,
        "compiler_explanation": "The compiler encountered a token it did not expect at this position.",
        "source_explanation": "A keyword, symbol, or identifier is placed where it is not syntactically valid.",
        "what_to_check": "Check the surrounding code for a missing token, misplaced keyword, or extra character.",
        "suggestion": "Remove or relocate the unexpected token.",
    },
    {
        "pattern": re.compile(r"expected '(class|struct|typename)' keyword", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A template parameter declaration requires the 'class' or 'typename' keyword.",
        "source_explanation": "A template parameter list is malformed.",
        "what_to_check": "Add the 'class' or 'typename' keyword before the template parameter name.",
        "suggestion": "Write 'template <typename T>' or 'template <class T>' for the template parameter.",
    },

    # -----------------------------------------------------------------------
    # RETURN TYPE / DECLARATION ERRORS
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"'([^']+)' declared as a parameter", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A function parameter name conflicts with a local declaration.",
        "source_explanation": "A name that is a function parameter is being redeclared inside the function.",
        "what_to_check": "Rename either the parameter or the local variable to avoid the conflict.",
        "suggestion": "Use distinct names for the parameter and the local variable.",
    },
    {
        "pattern": re.compile(r"declaration of '([^']+)' shadows a (parameter|local|global|member)", re.IGNORECASE),
        "error_type": "SHADOWING",
        "priority": 8,
        "compiler_explanation": "A new declaration hides a name from an outer scope.",
        "source_explanation": "An inner declaration uses the same name as an outer variable, parameter, or global.",
        "what_to_check": "Rename one of the variables to avoid confusion.",
        "suggestion": "Use a unique name for the inner variable to avoid shadowing.",
    },

    # -----------------------------------------------------------------------
    # DIVISION / ARITHMETIC
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"division by zero", re.IGNORECASE),
        "error_type": "DIVISION_BY_ZERO",
        "priority": 10,
        "compiler_explanation": "A compile-time division by zero was detected.",
        "source_explanation": "An expression divides by a constant zero value.",
        "what_to_check": "Check the divisor in the expression for a zero value.",
        "suggestion": "Ensure the divisor is non-zero, or add a runtime check.",
    },

    # -----------------------------------------------------------------------
    # MEMORY / ALLOCATION
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"use of (deleted function|deleted member function)", re.IGNORECASE),
        "error_type": "DELETED_FUNCTION",
        "priority": 10,
        "compiler_explanation": "A function that has been explicitly deleted (with '= delete') is called.",
        "source_explanation": "The code attempts to use a function that has been explicitly disabled.",
        "what_to_check": "Check the class definition — the function may be deleted because of copy/move semantics.",
        "suggestion": "Avoid calling the deleted function. If copying or moving is needed, implement the appropriate constructor/assignment operator.",
    },

    # -----------------------------------------------------------------------
    # C++11 / MODERN C++
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"'([^']+)' is not a (constexpr|consteval) function", re.IGNORECASE),
        "error_type": "CONSTEXPR_ERROR",
        "priority": 10,
        "compiler_explanation": "A function required to be constexpr does not satisfy the constraints.",
        "source_explanation": "The function contains statements that are not allowed in a constexpr context.",
        "what_to_check": "Ensure the constexpr function only uses constexpr-compatible operations.",
        "suggestion": "Remove non-constexpr operations from the constexpr function, or remove the constexpr qualifier.",
    },
    {
        "pattern": re.compile(r"'auto' type specifier requires an initializer", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A variable declared with 'auto' must have an initializer so the type can be deduced.",
        "source_explanation": "An auto-typed variable is declared without an assignment.",
        "what_to_check": "Provide an initializer for the auto variable, or use an explicit type.",
        "suggestion": "Write 'auto x = <value>;' to allow the type to be deduced.",
    },
    {
        "pattern": re.compile(r"initializer list fallback disabled", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 9,
        "compiler_explanation": "Initializer list syntax is used in an unsupported context.",
        "source_explanation": "An initializer list {} is used where it cannot be applied.",
        "what_to_check": "Use a different initialization syntax appropriate for the type.",
        "suggestion": "Use parentheses () for constructor calls or explicit assignment for non-aggregate types.",
    },
    {
        "pattern": re.compile(r"lambda expression.*not allowed in this context", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 9,
        "compiler_explanation": "A lambda expression is used where lambdas are not permitted.",
        "source_explanation": "Lambda expressions cannot be used in all contexts in C++.",
        "what_to_check": "Move the lambda to a valid context, or use a named function instead.",
        "suggestion": "Extract the lambda body into a named function or use it in an expression context.",
    },

    # -----------------------------------------------------------------------
    # MISC / CATCH-ALL SYNTAX
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"expected (type-specifier|a type)", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 9,
        "compiler_explanation": "The compiler expected a type name at this location.",
        "source_explanation": "A type is required but a keyword, identifier, or expression is present instead.",
        "what_to_check": "Check the declaration syntax for missing type names.",
        "suggestion": "Add the correct type name at the indicated location.",
    },
    {
        "pattern": re.compile(r"'([^']+)' was not declared in this scope; did you mean '([^']+)'", re.IGNORECASE),
        "error_type": "UNDEFINED_VARIABLE",
        "priority": 12,
        "compiler_explanation": "The identifier is not declared in this scope; the compiler suggests a similar name.",
        "source_explanation": "A possibly misspelled identifier is used.",
        "what_to_check": "Check the spelling of the identifier — the compiler suggests an alternative.",
        "suggestion": "Use the suggested name, or declare the intended identifier before using it.",
    },
    {
        "pattern": re.compile(r"error: (ld|linker) returned (1|non-zero) exit status", re.IGNORECASE),
        "error_type": "LINKER_ERROR",
        "priority": 10,
        "compiler_explanation": "The linker failed to produce the final executable.",
        "source_explanation": "One or more object files could not be linked, usually due to missing definitions.",
        "what_to_check": "Check for undefined references or missing library links in the build output above.",
        "suggestion": "Resolve undefined references by providing the missing definitions or adding the required library with -l.",
    },
    {
        "pattern": re.compile(r"undefined (reference|symbol) to '([^']+)'", re.IGNORECASE),
        "error_type": "LINKER_ERROR",
        "priority": 10,
        "compiler_explanation": "The linker could not find the definition for the named function or variable.",
        "source_explanation": "A function or variable is declared but never defined, or the library that defines it is not linked.",
        "what_to_check": "Ensure the function or variable is defined (not just declared) and the correct library is linked.",
        "suggestion": "Provide the function definition, or link the required library with -l<libname>.",
    },
    {
        "pattern": re.compile(r"multiple definition of '([^']+)'", re.IGNORECASE),
        "error_type": "LINKER_ERROR",
        "priority": 10,
        "compiler_explanation": "The same function or variable is defined in multiple translation units.",
        "source_explanation": "A definition appears in more than one .cpp file (perhaps via a header).",
        "what_to_check": "Move the definition to exactly one .cpp file and only declare it in headers.",
        "suggestion": "Use 'inline' for header-only definitions or move the definition to a single .cpp file.",
    },
    {
        "pattern": re.compile(r"cannot open (output file|source file)", re.IGNORECASE),
        "error_type": "IO_ERROR",
        "priority": 8,
        "compiler_explanation": "The compiler or linker cannot open a required file.",
        "source_explanation": "A file path used during compilation is inaccessible.",
        "what_to_check": "Verify file permissions and that the output directory exists.",
        "suggestion": "Check that the output directory exists and has write permissions.",
    },

    # -----------------------------------------------------------------------
    # ADDITIONAL RULES — reaching 100+ total
    # -----------------------------------------------------------------------
    {
        "pattern": re.compile(r"narrowing conversion from '([^']+)' to '([^']+)'", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 10,
        "compiler_explanation": "A value is narrowed (truncated) when converting to a smaller type.",
        "source_explanation": "The conversion would lose part of the value's range or precision.",
        "what_to_check": "Check the types on both sides; consider whether data loss is acceptable.",
        "suggestion": "Use an explicit cast to silence the error intentionally, or use a larger type.",
    },
    {
        "pattern": re.compile(r"taking address of temporary", re.IGNORECASE),
        "error_type": "POINTER_ERROR",
        "priority": 10,
        "compiler_explanation": "A pointer is taken to a temporary value, which will be destroyed after the statement.",
        "source_explanation": "The address of a temporary is taken, leaving a dangling pointer.",
        "what_to_check": "Store the temporary in a named variable before taking its address.",
        "suggestion": "Assign the value to a local variable, then take its address.",
    },
    {
        "pattern": re.compile(r"jump to (label|case) '([^']+)' crosses initialization", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A goto or switch statement jumps over a variable initialization.",
        "source_explanation": "The program flow bypasses a variable declaration, which is not allowed in C++.",
        "what_to_check": "Move the variable declaration before the goto/switch, or enclose it in a block.",
        "suggestion": "Move variable declarations before the jump point, or enclose the initialization in a block.",
    },
    {
        "pattern": re.compile(r"'([^']+)' is not (a struct|a class|a union|an enum|a namespace)", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "An identifier is used as a type (struct/class/enum/namespace) but is not one.",
        "source_explanation": "The name refers to a variable, function, or something else, not the type being used.",
        "what_to_check": "Check whether you intended a different name, or whether the type definition is missing.",
        "suggestion": "Define the struct, class, enum, or namespace with the expected name.",
    },
    {
        "pattern": re.compile(r"case value '([^']+)' not in enumerated type", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A switch case value does not belong to the switch expression's enumerated type.",
        "source_explanation": "A case label uses a value that is not a member of the enum type being switched on.",
        "what_to_check": "Use only enum values from the correct enum type in switch case labels.",
        "suggestion": "Replace the case value with a valid enum member from the type used in the switch expression.",
    },
    {
        "pattern": re.compile(r"(subscript|array subscript) of pointer to incomplete type", re.IGNORECASE),
        "error_type": "POINTER_ERROR",
        "priority": 10,
        "compiler_explanation": "Array subscript is used on a pointer to an incomplete (forward-declared) type.",
        "source_explanation": "The pointed-to type must be fully defined before array subscripting.",
        "what_to_check": "Include the full definition of the pointed-to type before using subscript notation.",
        "suggestion": "Add the full type definition with #include before the subscript operation.",
    },
    {
        "pattern": re.compile(r"this declaration has no storage class or type specifier", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 10,
        "compiler_explanation": "A declaration at global scope is missing a type or storage class.",
        "source_explanation": "A statement or expression at global scope is not a valid declaration.",
        "what_to_check": "Check whether the code should be inside a function, or add the correct type specifier.",
        "suggestion": "Move the code inside a function, or add the required type to the declaration.",
    },
    {
        "pattern": re.compile(r"call to (pure virtual|overridden) function", re.IGNORECASE),
        "error_type": "ABSTRACT_CLASS",
        "priority": 10,
        "compiler_explanation": "A pure virtual function is called directly, which is not allowed.",
        "source_explanation": "Pure virtual functions must be overridden in derived classes before they can be called.",
        "what_to_check": "Call the function through a derived class that overrides the pure virtual method.",
        "suggestion": "Override the pure virtual function in a concrete derived class and call through an instance of that class.",
    },
    {
        "pattern": re.compile(r"initializer for aggregate with no elements requires explicit braces", re.IGNORECASE),
        "error_type": "SYNTAX_ERROR",
        "priority": 9,
        "compiler_explanation": "An empty aggregate initializer requires explicit braces {}.",
        "source_explanation": "An aggregate type (array or struct) is initialized without the required brace syntax.",
        "what_to_check": "Use {} to initialize an empty aggregate.",
        "suggestion": "Use '= {}' or '{}'  to initialize the aggregate explicitly.",
    },
    {
        "pattern": re.compile(r"'([^']+)' does not name a type", re.IGNORECASE),
        "error_type": "UNDEFINED_TYPE",
        "priority": 10,
        "compiler_explanation": "The identifier is not a known type in this context.",
        "source_explanation": "A name used as a type has not been defined or is not in scope.",
        "what_to_check": "Check the type name is spelled correctly, defined, and the appropriate header is included.",
        "suggestion": "Add the type definition, include the required header, or fix the spelling.",
    },
    {
        "pattern": re.compile(r"forward declaration of '([^']+)'", re.IGNORECASE),
        "error_type": "INCOMPLETE_TYPE",
        "priority": 8,
        "compiler_explanation": "Only a forward declaration is visible here, not the full definition.",
        "source_explanation": "The type is declared but not yet fully defined at this point in the code.",
        "what_to_check": "Include the header or move the full definition before this usage.",
        "suggestion": "Add the full type definition or move the #include before this usage.",
    },
    {
        "pattern": re.compile(r"comparison between (signed and unsigned|pointer and integer)", re.IGNORECASE),
        "error_type": "TYPE_MISMATCH",
        "priority": 9,
        "compiler_explanation": "A comparison is made between incompatible integer types (signed vs unsigned, or pointer vs int).",
        "source_explanation": "Comparing signed and unsigned integers can give unexpected results due to implicit conversion.",
        "what_to_check": "Ensure the comparison operands are of compatible types.",
        "suggestion": "Cast one operand to match the other type, or use consistent signedness across comparisons.",
    },
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_text(contextual_diagnostic: dict) -> str:
    """Extract the best text from the diagnostic for matching."""
    return (
        contextual_diagnostic.get("normalized")
        or contextual_diagnostic.get("message")
        or ""
    )


def _extract_evidence(contextual_diagnostic: dict) -> dict:
    """Extract the most relevant evidence line from the diagnostic dict.

    Prefers the source_context entry whose 'line' matches the diagnostic's
    reported line number. Falls back to the first available context entry,
    then to None/empty if nothing is present.
    """
    diag_line = contextual_diagnostic.get("line")
    source_context = contextual_diagnostic.get("source_context", [])

    if diag_line is not None and source_context:
        for entry in source_context:
            if entry.get("line") == diag_line:
                return {"line": entry["line"], "code": entry.get("code", "")}

    # Fall back to first available context entry
    if source_context:
        first = source_context[0]
        return {"line": first.get("line"), "code": first.get("code", "")}

    # No context available
    return {"line": diag_line, "code": ""}


def analyze_simple_errors(contextual_diagnostic: dict) -> Optional[dict]:
    """Match the compiler message against known error rules.

    Returns a unified diagnosis dict when a rule matches, or None if no rule
    matches. Rules are tried in priority order (highest first); within the
    same priority the first pattern match wins.

    The text searched is the 'normalized' field first, falling back to
    'message'. Pattern (regex) rules take priority over needle (substring)
    rules at the same priority level.
    """
    text = _get_text(contextual_diagnostic)
    if not text:
        return None

    # Separate pattern-based and needle-based rules, then sort by priority desc
    pattern_rules = sorted(
        [r for r in _RULES if "pattern" in r],
        key=lambda r: r.get("priority", 0),
        reverse=True,
    )

    best_match = None

    for rule in pattern_rules:
        if rule["pattern"].search(text):
            best_match = rule
            break

    if best_match is None:
        return None

    evidence = _extract_evidence(contextual_diagnostic)
    return {
        "error_type": best_match["error_type"],
        "analysis_mode": "deterministic",
        "compiler_message": text,
        "compiler_explanation": best_match["compiler_explanation"],
        "source_explanation": best_match["source_explanation"],
        "evidence": evidence,
        "what_to_check": best_match["what_to_check"],
        "suggestion": best_match["suggestion"],
    }
